"""Tests unitaires pour le filtrage anti-publicite, le stockage et les statistiques."""

from __future__ import annotations

import numpy as np

from src.banner_detector import confirm_logo_ocr, inspect_frame_layout, is_ad_disclaimer
from src.gemini_analyzer import (
    compute_deterministic_stats,
    extract_top_keywords,
    should_trigger_periodic_gemini,
)
from src.ocr_engine import ExtractedBanner, clean_ocr_text
from src.storage import BannerStore


def _make_synthetic_frame(
    with_logo: bool,
    with_dark_ticker: bool,
    with_white_subject: bool,
) -> np.ndarray:
    """Genere une image BGR 720x1280 simulant une pub ou un direct CNews."""
    frame = np.full((720, 1280, 3), 140, dtype=np.uint8)  # fond neutre gris/bleu

    if with_logo:
        # Boite noire [C] en haut a gauche (BGR)
        frame[34:65, 58:90] = (10, 10, 10)
        # Boite rouge [NEWS] juste a droite (BGR: B=15, G=15, R=215)
        frame[34:65, 90:178] = (15, 15, 215)

    if with_dark_ticker:
        # Bandeau noir du bas (fil info)
        frame[630:715, 0:1056] = (5, 5, 5)
        # Cartouche bleu nuit d'emission a droite
        frame[630:715, 1056:1280] = (95, 25, 8)

    if with_white_subject:
        # Bandeau blanc de sujet / citation au-dessus du fil info
        frame[568:625, 0:1056] = (240, 240, 240)

    return frame


def test_ad_frame_rejected_even_with_bottom_white_text() -> None:
    ad_frame = _make_synthetic_frame(
        with_logo=False,
        with_dark_ticker=False,
        with_white_subject=True,
    )
    check = inspect_frame_layout(ad_frame)
    assert check.is_cnews_broadcast is False
    assert check.rejection_reason == "no_cnews_logo_ad_or_jingle"


def test_ad_frame_with_dark_bottom_but_no_logo_is_rejected() -> None:
    ad_frame = _make_synthetic_frame(
        with_logo=False,
        with_dark_ticker=True,
        with_white_subject=False,
    )
    check = inspect_frame_layout(ad_frame)
    assert check.is_cnews_broadcast is False
    assert check.rejection_reason == "no_cnews_logo_ad_or_jingle"


def test_broadcast_ticker_only_accepted_without_false_subject() -> None:
    frame = _make_synthetic_frame(
        with_logo=True,
        with_dark_ticker=True,
        with_white_subject=False,
    )
    check = inspect_frame_layout(frame)
    assert check.is_cnews_broadcast is True
    assert check.has_ticker is True
    assert check.has_subject_bar is False


def test_broadcast_with_subject_and_ticker_accepted() -> None:
    frame = _make_synthetic_frame(
        with_logo=True,
        with_dark_ticker=True,
        with_white_subject=True,
    )
    check = inspect_frame_layout(frame)
    assert check.is_cnews_broadcast is True
    assert check.has_ticker is True
    assert check.has_subject_bar is True


def test_logo_ocr_and_ad_disclaimer_filters() -> None:
    assert confirm_logo_ocr(("C", "NEWS", "19 : 23", "DIRECT")) is True
    assert confirm_logo_ocr(("GAZ DE", "BORDEAUX")) is False
    assert is_ad_disclaimer("L'énergie est notre avenir, économisons-la !") is True
    assert is_ad_disclaimer("312 écoles évacuées ce matin en Italie") is False


def test_store_deduplicates_and_computes_stats(tmp_path) -> None:
    store = BannerStore(
        data_dir=tmp_path / "data",
        docs_data_dir=tmp_path / "docs_data",
    )
    b1 = ExtractedBanner(
        accepted=True,
        ticker_text="ITALIE 312 écoles ont été évacuées ce matin.",
        subject_title="ON NE PRÉPARE PAS L'AVENIR EN EFFACANT LE PASSÉ",
        show_name="FACE A L'INFO",
    )
    b2_minor_ocr_diff = ExtractedBanner(
        accepted=True,
        ticker_text="ITALIE 312 ecoles ont ete evacuees ce matin",
        subject_title="ON NE PREPARE PAS L'AVENIR EN EFFACANT LE PASSE",
        show_name="FACE A L'INFO",
    )
    rec1, is_new_1 = store.upsert_banner(b1, "2026-11-11T18:25:00+00:00", "x3b68jn")
    rec2, is_new_2 = store.upsert_banner(b2_minor_ocr_diff, "2026-11-11T18:26:00+00:00", "x3b68jn")

    assert is_new_1 is True
    assert is_new_2 is False
    assert rec1.id == rec2.id
    assert rec2.sample_count == 2

    store.record_frame_check(True, None, "2026-11-11T18:25:00+00:00")
    store.record_frame_check(False, "no_cnews_logo_ad_or_jingle", "2026-11-11T18:21:00+00:00")

    records = store.load_records()
    stats = compute_deterministic_stats(records, store.load_metrics())
    assert stats["total_unique_banners"] == 1
    assert stats["capture_metrics"]["rejected_ads_or_jingles"] == 1
    keywords = extract_top_keywords(records)
    assert any(k["keyword"] == "écoles" for k in keywords)
    assert clean_ocr_text("•") == ""


def test_five_minute_gemini_trigger_logic(tmp_path) -> None:
    store = BannerStore(
        data_dir=tmp_path / "data",
        docs_data_dir=tmp_path / "docs_data",
    )
    ext = ExtractedBanner(
        accepted=True,
        ticker_text="ITALIE 312 écoles ont été évacuées ce matin.",
        subject_title="TAXE EMEUTES",
        show_name="FACE A L'INFO",
    )
    rec, _ = store.upsert_banner(ext, "2026-11-11T18:25:00+00:00", "x3b68jn")
    records = store.load_records()

    # Aucune analyse precedente -> declenche immediatement
    assert should_trigger_periodic_gemini([], records, 300, "Europe/Paris") is True

    # Analyse deja faite sur le meme dernier bandeau -> n'appelle pas Gemini inutilement
    same_banner_analysis = [
        {
            "generated_at": "2026-11-11T10:00:00+01:00",
            "last_banner_id": rec.id,
            "banners_analyzed": 1,
        }
    ]
    assert (
        should_trigger_periodic_gemini(same_banner_analysis, records, 300, "Europe/Paris")
        is False
    )
