"""Statistiques locales et analyse IA toutes les 5 minutes via l'API Gemini gratuite."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .banner_detector import strip_accents
from .config import DOCS_DATA_DIR, FRENCH_STOPWORDS, Settings
from .storage import BannerRecord, BannerStore

logger = logging.getLogger(__name__)

_LAST_HF_PUSH_MONOTONIC = 0.0

GEMINI_FALLBACK_MODELS = (
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
)


def extract_top_keywords(records: list[BannerRecord], limit: int = 20) -> list[dict[str, object]]:
    """Calcule les mots-cles les plus frequents dans les bandeaux captures."""
    counts: Counter[str] = Counter()
    display_form: dict[str, str] = {}

    for rec in records:
        text = " ".join(p for p in (rec.subject_title, rec.ticker_text) if p)
        seen_in_record: set[str] = set()
        for raw_word in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ-]{3,}", text):
            norm = strip_accents(raw_word).lower().strip("-")
            if len(norm) < 3 or norm in FRENCH_STOPWORDS or norm.isdigit():
                continue
            if norm not in display_form:
                display_form[norm] = raw_word.lower()
            if norm not in seen_in_record:
                counts[norm] += 1
                seen_in_record.add(norm)

    return [
        {"keyword": display_form[norm], "count": cnt}
        for norm, cnt in counts.most_common(limit)
    ]


def compute_deterministic_stats(records: list[BannerRecord], metrics: dict) -> dict:
    """Agrege les statistiques chiffrees a partir de l'historique."""
    by_show: Counter[str] = Counter()
    by_hour: Counter[str] = Counter()
    by_day: dict[str, int] = defaultdict(int)
    subject_count = 0
    ticker_count = 0

    for rec in records:
        show = rec.show_name.strip() or "Non precisee"
        by_show[show] += 1
        by_day[rec.date_local] += 1
        if rec.subject_title:
            subject_count += 1
        if rec.ticker_text:
            ticker_count += 1
        try:
            hour = rec.first_seen[11:13] + "h"
            by_hour[hour] += 1
        except Exception:  # noqa: BLE001
            pass

    return {
        "total_unique_banners": len(records),
        "subject_banners_count": subject_count,
        "ticker_banners_count": ticker_count,
        "capture_metrics": metrics,
        "shows": [{"name": k, "count": v} for k, v in by_show.most_common(12)],
        "hourly_distribution": [
            {"hour": f"{h:02d}h", "count": by_hour.get(f"{h:02d}h", 0)}
            for h in range(24)
        ],
        "daily_counts": [
            {"date": d, "count": by_day[d]}
            for d in sorted(by_day.keys())[-30:]
        ],
        "top_keywords": extract_top_keywords(records, limit=20),
    }


def _build_gemini_prompt(target_label: str, recent_records: list[BannerRecord]) -> str:
    lines: list[str] = []
    for idx, rec in enumerate(recent_records[-120:], start=1):
        heure = rec.first_seen[11:19] if len(rec.first_seen) >= 19 else rec.first_seen[11:16]
        parts = []
        if rec.show_name:
            parts.append(f"[{rec.show_name}]")
        if rec.subject_surtitre:
            parts.append(f"Intervenant: {rec.subject_surtitre}")
        if rec.subject_title:
            parts.append(f"Sujet: {rec.subject_title}")
        if rec.ticker_text:
            parts.append(f"Fil: {rec.ticker_text}")
        lines.append(f"{idx}. ({heure}) " + " | ".join(parts))

    joined = "\n".join(lines)
    return (
        f"Tu es un analyste media objectif en temps reel. Voici {len(lines)} bandeaux TV "
        f"captures sur CNews ({target_label}, hors publicites).\n"
        "Reponds UNIQUEMENT en JSON valide sans texte ni markdown autour, avec cette structure exacte :\n"
        "{\n"
        '  "resume_journee": "Synthese factuelle en 2 a 4 phrases des sujets traites a l\'antenne.",\n'
        '  "themes_principaux": [\n'
        '    {"theme": "Nom court du theme", "occurrences": 2, "mots_cles": ["mot1", "mot2"]}\n'
        "  ],\n"
        '  "sujets_marquants": ["Titre ou fait marquant 1", "Titre ou fait marquant 2"],\n'
        '  "observation_editoriale": "Observation factuelle sur les thematiques et le ton des bandeaux."\n'
        "}\n\n"
        f"Bandeaux captures :\n{joined}"
    )


def _extract_json_object(raw_text: str) -> dict:
    """Extrait l'objet JSON meme si le modele entoure la reponse de blocs markdown."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def call_gemini_daily_analysis(
    target_date: str,
    day_records: list[BannerRecord],
    settings: Settings,
) -> dict | None:
    """Appelle l'API Gemini gratuite avec repli multi-modeles (cycle de 5 minutes)."""
    if not settings.gemini_api_key or not day_records:
        return None

    models_to_try = [settings.gemini_model] + [
        m for m in GEMINI_FALLBACK_MODELS if m != settings.gemini_model
    ]
    now_local = datetime.now(ZoneInfo(settings.timezone))
    label = f"{target_date} à {now_local.strftime('%H:%M:%S')}"
    prompt = _build_gemini_prompt(label, day_records)

    for model_name in models_to_try:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent?key={settings.gemini_api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        try:
            resp = requests.post(url, json=payload, timeout=25)
            if resp.status_code in (404, 429, 503):
                logger.warning("Modele %s indisponible (HTTP %s), essai suivant...", model_name, resp.status_code)
                continue
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = _extract_json_object(raw_text)
            parsed["date"] = f"{target_date} {now_local.strftime('%H:%M')}"
            parsed["day_only"] = target_date
            parsed["model_used"] = model_name
            parsed["banners_analyzed"] = len(day_records)
            parsed["last_banner_id"] = day_records[-1].id
            parsed["generated_at"] = now_local.isoformat(timespec="seconds")
            return parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning("Echec appel Gemini (%s): %s", model_name, exc)
    return None


def should_trigger_periodic_gemini(
    existing_analyses: list[dict],
    records: list[BannerRecord],
    interval_s: int,
    tz_name: str,
) -> bool:
    """Verifie s'il faut lancer une nouvelle analyse Gemini (toutes les 5 min si nouveau contenu)."""
    if not records:
        return False
    if not existing_analyses:
        return True

    latest = existing_analyses[0]
    last_gen_str = latest.get("generated_at")
    last_banner_id = latest.get("last_banner_id")

    if last_banner_id == records[-1].id and latest.get("banners_analyzed") == len(records):
        return False

    if not last_gen_str:
        return True

    try:
        last_dt = datetime.fromisoformat(last_gen_str)
        now_dt = datetime.now(ZoneInfo(tz_name))
        elapsed = (now_dt - last_dt).total_seconds()
        return elapsed >= interval_s
    except Exception:  # noqa: BLE001
        return True


def update_stats_and_daily_report(
    store: BannerStore,
    target_date: str | None = None,
    run_gemini: bool = False,
    auto_periodic_gemini: bool = False,
    docs_data_dir: Path = DOCS_DATA_DIR,
) -> dict:
    """Met a jour docs/data/stats.json et declenche Gemini (manuel ou cycle auto 5 min)."""
    records = store.load_records()
    metrics = store.load_metrics()
    stats = compute_deterministic_stats(records, metrics)
    now_local = datetime.now(ZoneInfo(store.settings.timezone))
    stats["updated_at"] = now_local.isoformat(timespec="seconds")
    stats["analysis_interval_seconds"] = store.settings.analysis_interval_s

    stats_path = docs_data_dir / "stats.json"
    existing_analyses: list[dict] = []
    if stats_path.exists():
        try:
            prev = json.loads(stats_path.read_text(encoding="utf-8"))
            existing_analyses = prev.get("daily_ai_analyses", [])
        except Exception:  # noqa: BLE001
            existing_analyses = []

    chosen_date = target_date or (
        records[-1].date_local if records else now_local.strftime("%Y-%m-%d")
    )
    day_records = [r for r in records if r.date_local == chosen_date] or records[-50:]

    trigger_now = run_gemini or (
        auto_periodic_gemini
        and should_trigger_periodic_gemini(
            existing_analyses,
            day_records,
            store.settings.analysis_interval_s,
            store.settings.timezone,
        )
    )

    if trigger_now and day_records:
        analysis = call_gemini_daily_analysis(chosen_date, day_records, store.settings)
        if analysis:
            existing_analyses = [
                a for a in existing_analyses if a.get("date") != analysis.get("date")
            ]
            existing_analyses.insert(0, analysis)
            existing_analyses = existing_analyses[:48]
            store.sync_daily_analysis_to_sheets(analysis)

    stats["daily_ai_analyses"] = existing_analyses
    docs_data_dir.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    _sync_docs_to_hf_space(
        docs_data_dir=docs_data_dir,
        settings=store.settings,
        force=trigger_now,
    )
    return stats


def _sync_docs_to_hf_space(docs_data_dir: Path, settings: Settings, force: bool = False) -> bool:
    """Publie data/banners.json et data/stats.json sur le Space statique Hugging Face."""
    global _LAST_HF_PUSH_MONOTONIC
    if not settings.hf_token or not settings.hf_space_repo:
        return False

    now_mono = time.monotonic()
    if not force and _LAST_HF_PUSH_MONOTONIC > 0 and (now_mono - _LAST_HF_PUSH_MONOTONIC) < 60.0:
        return False

    banners_file = docs_data_dir / "banners.json"
    stats_file = docs_data_dir / "stats.json"
    if not banners_file.exists() or not stats_file.exists():
        return False

    try:
        from huggingface_hub import CommitOperationAdd, HfApi

        api = HfApi(token=settings.hf_token)
        api.create_commit(
            repo_id=settings.hf_space_repo,
            repo_type="space",
            operations=[
                CommitOperationAdd(
                    path_in_repo="data/banners.json",
                    path_or_fileobj=banners_file.read_bytes(),
                ),
                CommitOperationAdd(
                    path_in_repo="data/stats.json",
                    path_or_fileobj=stats_file.read_bytes(),
                ),
            ],
            commit_message="chore: sync live CNews banners & Gemini stats",
        )
        _LAST_HF_PUSH_MONOTONIC = now_mono
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Echec synchro Hugging Face Space: %s", exc)
        return False
