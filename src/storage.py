"""Stockage, déduplication intelligente, nettoyage rétroactif, export CSV/JSON et Google Sheets."""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .banner_detector import strip_accents
from .config import DATA_DIR, DOCS_DATA_DIR, Settings
from .ocr_engine import (
    ExtractedBanner,
    clean_ocr_text,
    clean_subject_surtitre,
    clean_subject_title,
    clean_ticker_text,
)

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.86


@dataclass
class BannerRecord:
    """Enregistrement horodaté et dédupliqué d'un bandeau CNews."""

    id: str
    date_local: str
    first_seen: str
    last_seen: str
    sample_count: int
    ticker_text: str
    subject_surtitre: str
    subject_title: str
    show_name: str
    source_id: str


def _canonical(text: str) -> str:
    clean = strip_accents(text).lower()
    clean = re.sub(r"[^a-z0-9 ]+", " ", clean)
    return re.sub(r"\s+", " ", clean).strip()


def _parse_iso(ts: str, tz_name: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo(tz_name))


def _signature(ticker: str, surtitre: str, title: str) -> str:
    return _canonical(" | ".join(part for part in (title, surtitre, ticker) if part))


def _text_similar(a: str, b: str, threshold: float = SIMILARITY_THRESHOLD) -> bool:
    ca = _canonical(a)
    cb = _canonical(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    return SequenceMatcher(None, ca, cb).ratio() >= threshold


def is_similar_banner(a: BannerRecord, ticker: str, surtitre: str, title: str) -> bool:
    """Compare deux lectures OCR en tolérant de légères variations de caractères."""
    sig_a = _signature(a.ticker_text, a.subject_surtitre, a.subject_title)
    sig_b = _signature(ticker, surtitre, title)
    if not sig_a or not sig_b:
        return False
    if sig_a == sig_b:
        return True
    return SequenceMatcher(None, sig_a, sig_b).ratio() >= SIMILARITY_THRESHOLD


class BannerStore:
    """Gère la persistance locale (JSONL/CSV/docs JSON) et la synchro tableur."""

    def __init__(
        self,
        data_dir: Path = DATA_DIR,
        docs_data_dir: Path = DOCS_DATA_DIR,
        settings: Settings | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.docs_data_dir = docs_data_dir
        self.settings = settings or Settings()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.docs_data_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.data_dir / "banners.jsonl"
        self.csv_path = self.data_dir / "banners.csv"
        self.metrics_path = self.data_dir / "capture_metrics.json"
        self.docs_banners_path = self.docs_data_dir / "banners.json"

    def load_records(self) -> list[BannerRecord]:
        """Charge tous les bandeaux enregistrés."""
        if not self.jsonl_path.exists():
            return []
        records: list[BannerRecord] = []
        for line in self.jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            records.append(BannerRecord(**json.loads(line)))
        return records

    def load_metrics(self) -> dict:
        """Charge les compteurs de frames valides et publicités filtrées."""
        if not self.metrics_path.exists():
            return {
                "total_frames_checked": 0,
                "accepted_frames": 0,
                "rejected_ads_or_jingles": 0,
                "rejected_reasons": {},
                "last_run_at": None,
                "last_status": "never_run",
            }
        return json.loads(self.metrics_path.read_text(encoding="utf-8"))

    def record_frame_check(self, accepted: bool, rejection_reason: str | None, timestamp: str) -> None:
        """Met à jour les métriques de filtrage anti-publicité."""
        metrics = self.load_metrics()
        metrics["total_frames_checked"] += 1
        if accepted:
            metrics["accepted_frames"] += 1
            metrics["last_status"] = "broadcast_captured"
        else:
            metrics["rejected_ads_or_jingles"] += 1
            reasons = metrics.setdefault("rejected_reasons", {})
            key = rejection_reason or "unknown"
            reasons[key] = reasons.get(key, 0) + 1
            metrics["last_status"] = f"filtered:{key}"
        metrics["last_run_at"] = timestamp
        self.metrics_path.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def record_stream_status(self, status: str, timestamp: str) -> None:
        """Enregistre un état technique (ex: geo_blocked, no_stream) sans casser les compteurs."""
        metrics = self.load_metrics()
        metrics["last_run_at"] = timestamp
        metrics["last_status"] = status
        self.metrics_path.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _find_merge_candidate(
        self,
        records: list[BannerRecord],
        date_local: str,
        ticker: str,
        surtitre: str,
        title: str,
    ) -> BannerRecord | None:
        """Recherche un bandeau existant du même jour à fusionner pour éviter les doublons liés à la boucle du fil info."""
        day_records = [r for r in records if r.date_local == date_local]
        if not day_records:
            return None

        # 1. Correspondance directe sur le couple (sujet, fil info) dans toute la journée
        for candidate in reversed(day_records[-60:]):
            if is_similar_banner(candidate, ticker, surtitre, title):
                return candidate

        # 2. Si le bandeau sujet principal est présent :
        # Si ce même sujet a déjà été enregistré récemment ET que le fil info en bas a déjà été vu aujourd'hui
        # (ou est vide), on fusionne avec l'enregistrement du sujet principal pour éviter de dupliquer le sujet
        # à chaque rotation des 7 dépêches du fil info.
        if title:
            matching_title_rec: BannerRecord | None = None
            for candidate in reversed(day_records[-50:]):
                if candidate.subject_title and _text_similar(candidate.subject_title, title):
                    matching_title_rec = candidate
                    break
            if matching_title_rec is not None:
                ticker_already_known = (not ticker) or any(
                    r.ticker_text and _text_similar(r.ticker_text, ticker)
                    for r in day_records
                )
                if ticker_already_known:
                    return matching_title_rec

        # 3. Si seul le fil info est visible (transition sans bandeau sujet) et que ce fil info est déjà connu aujourd'hui
        if not title and ticker:
            for candidate in reversed(day_records):
                if candidate.ticker_text and _text_similar(candidate.ticker_text, ticker):
                    return candidate

        return None

    def upsert_banner(
        self,
        extracted: ExtractedBanner,
        captured_at: str,
        source_id: str,
    ) -> tuple[BannerRecord, bool]:
        """Ajoute un nouveau bandeau ou prolonge le bandeau existant correspondant."""
        records = self.load_records()
        local_dt = _parse_iso(captured_at, self.settings.timezone)
        local_iso = local_dt.isoformat(timespec="seconds")
        date_local = local_dt.strftime("%Y-%m-%d")

        ticker = clean_ticker_text(extracted.ticker_text)
        surtitre = clean_subject_surtitre(extracted.subject_surtitre)
        title = clean_subject_title(extracted.subject_title)
        show = clean_ocr_text(extracted.show_name)

        if not ticker and not title:
            # Si le nettoyage a éliminé un fragment tronqué vide, retourne le dernier enregistrement sans écriture
            if records:
                return records[-1], False

        candidate = self._find_merge_candidate(records, date_local, ticker, surtitre, title)
        if candidate is not None:
            candidate.last_seen = local_iso
            candidate.sample_count += 1
            if not candidate.show_name and show:
                candidate.show_name = show
            if not candidate.subject_surtitre and surtitre:
                candidate.subject_surtitre = surtitre
            if not candidate.ticker_text and ticker:
                candidate.ticker_text = ticker
            if not candidate.subject_title and title:
                candidate.subject_title = title
            self._save_all(records)
            return candidate, False

        rec = BannerRecord(
            id=f"{date_local}-{len(records) + 1:05d}",
            date_local=date_local,
            first_seen=local_iso,
            last_seen=local_iso,
            sample_count=1,
            ticker_text=ticker,
            subject_surtitre=surtitre,
            subject_title=title,
            show_name=show,
            source_id=source_id,
        )
        records.append(rec)
        self._save_all(records)
        return rec, True

    def clean_and_deduplicate_history(self) -> list[BannerRecord]:
        """Nettoie rétroactivement toutes les fautes d'OCR et fusionne les doublons de l'historique."""
        raw_records = self.load_records()
        cleaned_list: list[BannerRecord] = []

        for r in raw_records:
            ticker = clean_ticker_text(r.ticker_text)
            surtitre = clean_subject_surtitre(r.subject_surtitre)
            title = clean_subject_title(r.subject_title)
            show = clean_ocr_text(r.show_name)

            if not ticker and not title:
                continue

            candidate = self._find_merge_candidate(cleaned_list, r.date_local, ticker, surtitre, title)
            if candidate is not None:
                candidate.last_seen = max(candidate.last_seen, r.last_seen)
                candidate.sample_count += max(1, r.sample_count)
                if not candidate.subject_surtitre and surtitre:
                    candidate.subject_surtitre = surtitre
                if not candidate.ticker_text and ticker:
                    candidate.ticker_text = ticker
                if not candidate.subject_title and title:
                    candidate.subject_title = title
                if not candidate.show_name and show:
                    candidate.show_name = show
            else:
                new_id = f"{r.date_local}-{len(cleaned_list) + 1:05d}"
                cleaned_list.append(
                    BannerRecord(
                        id=new_id,
                        date_local=r.date_local,
                        first_seen=r.first_seen,
                        last_seen=r.last_seen,
                        sample_count=max(1, r.sample_count),
                        ticker_text=ticker,
                        subject_surtitre=surtitre,
                        subject_title=title,
                        show_name=show,
                        source_id=r.source_id,
                    )
                )

        self._save_all(cleaned_list)
        return cleaned_list

    def _save_all(self, records: list[BannerRecord]) -> None:
        jsonl_text = "\n".join(json.dumps(asdict(r), ensure_ascii=False) for r in records)
        self.jsonl_path.write_text(jsonl_text + ("\n" if jsonl_text else ""), encoding="utf-8")

        with self.csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "id",
                    "date_local",
                    "first_seen",
                    "last_seen",
                    "sample_count",
                    "show_name",
                    "subject_surtitre",
                    "subject_title",
                    "ticker_text",
                    "source_id",
                ],
                delimiter=";",
            )
            writer.writeheader()
            for r in records:
                writer.writerow(asdict(r))

        self.docs_banners_path.write_text(
            json.dumps([asdict(r) for r in reversed(records[-500:])], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def sync_banner_to_sheets(self, record: BannerRecord) -> bool:
        """Envoie un nouveau bandeau vers Google Sheets via Google Apps Script si configuré."""
        url = self.settings.apps_script_url
        if not url:
            return False
        payload = {
            "action": "append_banner",
            "secret": self.settings.sheets_secret,
            "record": asdict(record),
        }
        try:
            resp = requests.post(url, json=payload, timeout=12)
            resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Échec envoi Google Sheets: %s", exc)
            return False

    def sync_daily_analysis_to_sheets(self, analysis: dict) -> bool:
        """Envoie le bilan quotidien Gemini vers l'onglet Analyses_IA du Google Sheet."""
        url = self.settings.apps_script_url
        if not url:
            return False
        payload = {
            "action": "append_analysis",
            "secret": self.settings.sheets_secret,
            "analysis": analysis,
        }
        try:
            resp = requests.post(url, json=payload, timeout=12)
            resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Échec envoi analyse Gemini vers Google Sheets: %s", exc)
            return False
