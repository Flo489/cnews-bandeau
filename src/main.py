"""Point d'entree CLI : capture CNews, filtre anti-pub, OCR, analyse Gemini 5 min et serveur temps reel."""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from .config import BASE_DIR, ROI_SUBJECT_MAIN, ROI_TICKER, Settings, crop_normalized
from .gemini_analyzer import update_stats_and_daily_report
from .ocr_engine import BannerOCREngine
from .storage import BannerStore
from .stream_capture import fetch_latest_hls_frames, iter_live_frames

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("cnews-bandeau")


def _crop_signature(frame: np.ndarray) -> np.ndarray:
    """Genere une miniature en niveaux de gris des zones de bandeaux pour detecter les changements."""
    bot = crop_normalized(frame, (ROI_SUBJECT_MAIN[0], ROI_TICKER[1], 0.0, 0.825))
    gray = cv2.cvtColor(bot, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (240, 48))


def _process_frame(
    frame: np.ndarray,
    captured_at: str,
    source_id: str,
    ocr: BannerOCREngine,
    store: BannerStore,
    prev_sig: np.ndarray | None,
) -> tuple[np.ndarray | None, dict]:
    sig = _crop_signature(frame)
    if prev_sig is not None:
        diff = float(np.mean(np.abs(sig.astype(np.int16) - prev_sig.astype(np.int16))))
        if diff < 2.2:
            return prev_sig, {"status": "unchanged_pixels", "diff": round(diff, 2)}

    extracted = ocr.extract_from_frame(frame)
    store.record_frame_check(extracted.accepted, extracted.rejection_reason, captured_at)

    if not extracted.accepted:
        logger.info("Frame ignoree (%s)", extracted.rejection_reason)
        return sig, {
            "status": "rejected",
            "reason": extracted.rejection_reason,
            "metrics": extracted.layout_metrics,
        }

    record, is_new = store.upsert_banner(extracted, captured_at, source_id)
    if is_new:
        store.sync_banner_to_sheets(record)
    logger.info(
        "%s [%s] Sujet=%r | Fil=%r",
        "NOUVEAU" if is_new else "MAJ",
        record.show_name or "CNews",
        record.subject_title,
        record.ticker_text,
    )
    return sig, {
        "status": "accepted",
        "is_new": is_new,
        "id": record.id,
        "show_name": record.show_name,
        "subject_surtitre": record.subject_surtitre,
        "subject_title": record.subject_title,
        "ticker_text": record.ticker_text,
    }


def run_once(settings: Settings, max_segments: int = 3) -> int:
    """Capture les derniers segments disponibles et met a jour l'analyse Gemini (cycle 5 min)."""
    store = BannerStore(settings=settings)
    ocr = BannerOCREngine()
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        frames = fetch_latest_hls_frames(settings.dailymotion_id, max_segments=max_segments)
    except Exception as exc:  # noqa: BLE001
        logger.error("Capture flux impossible: %s", exc)
        store.record_stream_status(f"stream_error:{exc}", now_iso)
        update_stats_and_daily_report(store, auto_periodic_gemini=True)
        return 0

    prev_sig: np.ndarray | None = None
    for item in frames:
        prev_sig, _ = _process_frame(
            item.frame, item.captured_at, item.source_id, ocr, store, prev_sig
        )

    update_stats_and_daily_report(store, auto_periodic_gemini=True)
    return 0


def run_watch(settings: Settings, interval_s: float, duration_s: float | None) -> int:
    """Surveille le flux en continu et declenche Gemini toutes les 5 minutes."""
    store = BannerStore(settings=settings)
    ocr = BannerOCREngine()
    prev_sig: np.ndarray | None = None

    # Genere immediatement les statistiques et une premiere analyse Gemini si disponible.
    update_stats_and_daily_report(store, auto_periodic_gemini=True)

    while True:
        try:
            for item in iter_live_frames(
                settings.dailymotion_id,
                poll_interval_s=interval_s,
                duration_s=duration_s,
            ):
                prev_sig, res = _process_frame(
                    item.frame, item.captured_at, item.source_id, ocr, store, prev_sig
                )
                if res.get("status") != "unchanged_pixels":
                    update_stats_and_daily_report(store, auto_periodic_gemini=True)
            if duration_s is not None:
                break
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reprise automatique de la surveillance apres erreur (%s)...", exc)
            store.record_stream_status(
                f"reconnecting:{exc}",
                datetime.now(timezone.utc).isoformat(),
            )
            update_stats_and_daily_report(store, auto_periodic_gemini=True)
            if duration_s is not None:
                break
            time.sleep(5.0)

    update_stats_and_daily_report(store, auto_periodic_gemini=True)
    return 0


class _NoCacheStaticHandler(http.server.SimpleHTTPRequestHandler):
    """Sert le dossier docs/ sans cache navigateur pour un rafraichissement temps reel."""

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def run_serve(settings: Settings, port: int, interval_s: float) -> int:
    """Lance le serveur web temps reel + la capture CNews et l'analyse Gemini 5 min en arriere-plan."""
    docs_dir = BASE_DIR / "docs"
    store = BannerStore(settings=settings)
    update_stats_and_daily_report(store, auto_periodic_gemini=True)

    watcher_thread = threading.Thread(
        target=run_watch,
        args=(settings, interval_s, None),
        daemon=True,
        name="cnews-live-watcher",
    )
    watcher_thread.start()

    handler_cls = functools.partial(_NoCacheStaticHandler, directory=str(docs_dir))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler_cls)
    logger.info("Tableau de bord temps reel actif sur http://localhost:%d/", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Arret du serveur.")
    finally:
        server.server_close()
    return 0


def run_image(settings: Settings, image_path: str, save: bool) -> int:
    """Analyse une image locale (capture d'ecran) et affiche le resultat JSON."""
    frame = cv2.imread(image_path)
    if frame is None:
        logger.error("Image introuvable ou illisible: %s", image_path)
        return 1

    ocr = BannerOCREngine()
    if save:
        store = BannerStore(settings=settings)
        now_iso = datetime.now(timezone.utc).isoformat()
        _, res = _process_frame(frame, now_iso, "local_image", ocr, store, None)
        update_stats_and_daily_report(store, auto_periodic_gemini=True)
    else:
        ext = ocr.extract_from_frame(frame)
        res = {
            "accepted": ext.accepted,
            "rejection_reason": ext.rejection_reason,
            "show_name": ext.show_name,
            "subject_surtitre": ext.subject_surtitre,
            "subject_title": ext.subject_title,
            "ticker_text": ext.ticker_text,
            "logo_texts": ext.logo_texts,
            "metrics": ext.layout_metrics,
        }
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyseur temps reel des bandeaux CNews")
    parser.add_argument("--once", action="store_true", help="Capture ponctuelle des derniers segments")
    parser.add_argument("--watch", action="store_true", help="Surveillance continue du direct")
    parser.add_argument("--serve", action="store_true", help="Serveur web temps reel + surveillance continue")
    parser.add_argument("--port", type=int, default=8080, help="Port HTTP en mode --serve")
    parser.add_argument("--interval", type=float, default=4.0, help="Intervalle (s) de lecture du flux")
    parser.add_argument("--duration", type=float, default=None, help="Duree max (s) en mode --watch")
    parser.add_argument("--segments", type=int, default=3, help="Segments HLS inspectes en mode --once")
    parser.add_argument("--image", type=str, help="Chemin d'une image locale a analyser")
    parser.add_argument("--save", action="store_true", help="Enregistre le resultat avec --image")
    parser.add_argument("--analyze-daily", action="store_true", help="Force immediatement une analyse Gemini")
    parser.add_argument("--date", type=str, default=None, help="Date cible YYYY-MM-DD pour --analyze-daily")

    args = parser.parse_args()
    settings = Settings()

    if args.image:
        return run_image(settings, args.image, args.save)
    if args.analyze_daily:
        store = BannerStore(settings=settings)
        stats = update_stats_and_daily_report(store, target_date=args.date, run_gemini=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0
    if args.serve:
        return run_serve(settings, args.port, args.interval)
    if args.watch:
        return run_watch(settings, args.interval, args.duration)
    return run_once(settings, max_segments=args.segments)


if __name__ == "__main__":
    sys.exit(main())
