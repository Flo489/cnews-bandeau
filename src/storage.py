"""Stockage, deduplication textuelle, export CSV/JSON et envoi Google Sheets."""

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
from .ocr_engine import ExtractedBanner

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.86


@dataclass
class BannerRecord:
    """Enregistrement horodate et deduplique d'un bandeau CNews."""

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


def is_similar_banner(a: BannerRecord, ticker: str, surtitre: str, title: str) -> bool:
    """Compare deux lectures OCR en tolerant de legeres variations de caracteres."""
    sig_a = _signature(a.ticker_text, a.subject_surtitre, a.subject_title)
    sig_b = _signature(ticker, surtitre, title)
    if not sig_a or not sig_b:
        return False
    if sig_a == sig_b:
        return True
    return SequenceMatcher(None, sig_a, sig_b).ratio() >= SIMILARITY_THRESHOLD


class BannerStore:
    """Gere la persistance locale (JSONL/CSV/docs JSON) et la synchro tableur."""

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
        """Charge tous les bandeaux enregistres."""
        if not self.jsonl_path.exists():
            return []
        records: list[BannerRecord] = []
        for line in self.jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            records.append(BannerRecord(**json.loads(line)))
        return records

    def load_metrics(self) -> dict:
        """Charge les compteurs de frames valides et publicites filtrees."""
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
        """Met a jour les metriques de filtrage anti-publicite."""
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
        """Enregistre un etat technique (ex: geo_blocked, no_stream) sans casser les compteurs."""
        metrics = self.load_metrics()
        metrics["last_run_at"] = timestamp
        metrics["last_status"] = status
        self.metrics_path.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def upsert_banner(
        self,
        extracted: ExtractedBanner,
        captured_at: str,
        source_id: str,
    ) -> tuple[BannerRecord, bool]:
        """Ajoute un nouveau bandeau ou prolonge le dernier bandeau identique."""
        records = self.load_records()
        local_dt = _parse_iso(captured_at, self.settings.timezone)
        local_iso = local_dt.isoformat(timespec="seconds")
        date_local = local_dt.strftime("%Y-%m-%d")

        # Compare avec les 5 derniers bandeaux du meme jour pour fusionner le texte identique.
        for candidate in reversed(records[-5:]):
            if candidate.date_local != date_local:
                continue
            if is_similar_banner(
                candidate,
                extracted.ticker_text,
                extracted.subject_surtitre,
                extracted.subject_title,
            ):
                candidate.last_seen = local_iso
                candidate.sample_count += 1
                if not candidate.show_name and extracted.show_name:
                    candidate.show_name = extracted.show_name
                if len(extracted.ticker_text) > len(candidate.ticker_text):
                    candidate.ticker_text = extracted.ticker_text
                if len(extracted.subject_title) > len(candidate.subject_title):
                    candidate.subject_title = extracted.subject_title
                self._save_all(records)
                return candidate, False

        rec = BannerRecord(
            id=f"{date_local}-{len(records) + 1:05d}",
            date_local=date_local,
            first_seen=local_iso,
            last_seen=local_iso,
            sample_count=1,
            ticker_text=extracted.ticker_text,
            subject_surtitre=extracted.subject_surtitre,
            subject_title=extracted.subject_title,
            show_name=extracted.show_name,
            source_id=source_id,
        )
        records.append(rec)
        self._save_all(records)
        return rec, True

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
        """Envoie un nouveau bandeau vers Google Sheets via Google Apps Script si configure."""
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
            logger.warning("Echec envoi Google Sheets: %s", exc)
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
            logger.warning("Echec envoi analyse Gemini vers Google Sheets: %s", exc)
            return False
