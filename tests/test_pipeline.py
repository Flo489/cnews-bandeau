"""Tests unitaires pour le filtrage anti-publicité, la correction typographique OCR, l'analyse politique et le stockage."""

from __future__ import annotations

import numpy as np

from src.banner_detector import confirm_logo_ocr, inspect_frame_layout, is_ad_disclaimer
from src.gemini_analyzer import (
    compute_deterministic_stats,
    extract_top_keywords,
    should_trigger_periodic_gemini,
)
from src.ocr_engine import (
    ExtractedBanner,
    clean_ocr_text,
    clean_subject_surtitre,
    clean_subject_title,
    clean_ticker_text,
)
from src.storage import BannerStore


def _make_synthetic_frame(
    with_logo: bool,
    with_dark_ticker: bool,
    with_white_subject: bool,
) -> np.ndarray:
    """Génère une image BGR 720x1280 simulant une pub ou un direct CNews."""
    frame = np.full((720, 1280, 3), 140, dtype=np.uint8)

    if with_logo:
        frame[34:65, 58:90] = (10, 10, 10)
        frame[34:65, 90:178] = (15, 15, 215)

    if with_dark_ticker:
        frame[630:715, 0:1056] = (5, 5, 5)
        frame[630:715, 1056:1280] = (95, 25, 8)

    if with_white_subject:
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


def test_ocr_typographical_corrections() -> None:
    assert (
        clean_ticker_text(
            "CArBurants Près de 19% des stations-service du Grand-Est manquaient d'au moins un carburant mercredi matin (gouvernement)."
        )
        == "CARBURANTS Près de 19% des stations-service du Grand-Est manquaient d'au moins un carburant mercredi matin (gouvernement)."
    )
    assert (
        clean_ticker_text("YvELiınEs L'auteur de l'attentat raciste avait une liste.")
        == "YVELINES L'auteur de l'attentat raciste avait une liste."
    )
    assert (
        clean_subject_title("L'INQUIÊTANTE HAUSSE DES ACTES ANTICHRÊTIENS")
        == "L'INQUIÉTANTE HAUSSE DES ACTES ANTICHRÉTIENS"
    )
    assert (
        clean_subject_title("UNE RELIQUE DE SAINT NICOLAS BRÜLEE ET VOLÉE")
        == "UNE RELIQUE DE SAINT NICOLAS BRÛLÉE ET VOLÉE"
    )
    assert (
        clean_subject_title('TAXE "ÊMEUTES": UNE HAUSSE DE 235€ POUR LES ASSURÊS ?')
        == 'TAXE "ÉMEUTES" : UNE HAUSSE DE 235€ POUR LES ASSURÉS ?'
    )
    assert clean_ticker_text("servent à lancer des attaques.") == ""
    assert clean_subject_surtitre("ONE >") == ""


def test_store_deduplicates_and_computes_political_stats(tmp_path) -> None:
    store = BannerStore(
        data_dir=tmp_path / "data",
        docs_data_dir=tmp_path / "docs_data",
    )
    b1 = ExtractedBanner(
        accepted=True,
        ticker_text="ITALie 312 écoles ont été évacuées ce matin.",
        subject_surtitre="JEAN-LUC MELENCHON CANDIDAT LFI À L'ELECTION PRÉSIDENTIELLE",
        subject_title='T. RINER, "UN CANDIDAT QUI RESSEMBLE A QUELQUE CHOSE"',
        show_name="FACE A L'INFO",
    )
    b2_minor_ocr_diff = ExtractedBanner(
        accepted=True,
        ticker_text="ITALIE 312 ecoles ont ete evacuees ce matin",
        subject_surtitre="JEAN-LUC MÉLENCHON CANDIDAT LFI À L'ÉLECTION PRÉSIDENTIELLE",
        subject_title='T. RINER, "UN CANDIDAT QUI RESSEMBLE A QUELQUE CHOSE"',
        show_name="FACE A L'INFO",
    )
    rec1, is_new_1 = store.upsert_banner(b1, "2026-11-11T19:01:00+00:00", "x3b68jn")
    rec2, is_new_2 = store.upsert_banner(b2_minor_ocr_diff, "2026-11-11T19:02:00+00:00", "x3b68jn")

    assert is_new_1 is True
    assert is_new_2 is False
    assert rec1.id == rec2.id
    assert rec2.sample_count == 2

    records = store.load_records()
    stats = compute_deterministic_stats(records, store.load_metrics())
    assert stats["total_unique_banners"] == 1
    assert "political_cursor" in stats
    lfi_study = next(p for p in stats["parties_study"] if p["party_id"] == "LFI")
    assert lfi_study["total_mentions"] == 1
    assert lfi_study["critique_count"] == 1

    keywords = extract_top_keywords(records)
    kw_set = {k["keyword"] for k in keywords}
    assert "matin" not in kw_set
    assert "ont" not in kw_set
    assert "été" not in kw_set
    assert clean_ocr_text("•") == ""


def test_five_minute_gemini_trigger_logic(tmp_path) -> None:
    store = BannerStore(
        data_dir=tmp_path / "data",
        docs_data_dir=tmp_path / "docs_data",
    )
    ext = ExtractedBanner(
        accepted=True,
        ticker_text="ITALIE 312 écoles ont été évacuées ce matin.",
        subject_title="TAXE ÉMEUTES",
        show_name="FACE A L'INFO",
    )
    rec, _ = store.upsert_banner(ext, "2026-11-11T18:25:00+00:00", "x3b68jn")
    records = store.load_records()

    assert should_trigger_periodic_gemini([], records, 300, "Europe/Paris") is True

    same_banner_analysis = [
        {
            "generated_at": "2026-11-11T10:00:00+01:00",
            "last_banner_id": rec.id,
            "banners_analyzed": 1,
            "score_axe_politique": 55,
        }
    ]
    assert (
        should_trigger_periodic_gemini(same_banner_analysis, records, 300, "Europe/Paris")
        is False
    )
