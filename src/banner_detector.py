"""Detection des zones de bandeaux CNews et filtrage anti-publicite."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import cv2
import numpy as np

from .config import (
    AD_DISCLAIMER_PATTERNS,
    MIN_LOGO_BLACK_RATIO,
    MIN_LOGO_RED_RATIO,
    MIN_SUBJECT_BAR_RATIO,
    MIN_TICKER_DARK_RATIO,
    ROI_LOGO_BLACK,
    ROI_LOGO_RED,
    ROI_SUBJECT_BAR_CHECK,
    ROI_TICKER_BG_CHECK,
    crop_normalized,
)


@dataclass(frozen=True)
class FrameLayoutCheck:
    """Resultat de l'inspection visuelle d'une image capturee."""

    is_cnews_broadcast: bool
    has_ticker: bool
    has_subject_bar: bool
    logo_red_ratio: float
    logo_black_ratio: float
    ticker_dark_ratio: float
    subject_bar_ratio: float
    rejection_reason: str | None = None


def strip_accents(text: str) -> str:
    """Supprime les diacritiques pour les comparaisons insensibles aux accents."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _red_pixel_ratio(bgr_crop: np.ndarray) -> float:
    b, g, r = cv2.split(bgr_crop)
    mask = (r > 140) & (g < 60) & (b < 60)
    return float(mask.mean())


def _dark_pixel_ratio(bgr_crop: np.ndarray, threshold: int = 38) -> float:
    return float((bgr_crop.max(axis=2) < threshold).mean())


def _white_pixel_ratio(bgr_crop: np.ndarray, threshold: int = 200) -> float:
    return float((bgr_crop.min(axis=2) > threshold).mean())


def inspect_frame_layout(frame: np.ndarray) -> FrameLayoutCheck:
    """Verifie si la frame appartient bien au direct CNews (et non a une publicite)."""
    if frame is None or frame.size == 0:
        return FrameLayoutCheck(
            is_cnews_broadcast=False,
            has_ticker=False,
            has_subject_bar=False,
            logo_red_ratio=0.0,
            logo_black_ratio=0.0,
            ticker_dark_ratio=0.0,
            subject_bar_ratio=0.0,
            rejection_reason="empty_frame",
        )

    logo_red_ratio = _red_pixel_ratio(crop_normalized(frame, ROI_LOGO_RED))
    logo_black_ratio = _dark_pixel_ratio(crop_normalized(frame, ROI_LOGO_BLACK), threshold=48)
    ticker_dark_ratio = _dark_pixel_ratio(crop_normalized(frame, ROI_TICKER_BG_CHECK), threshold=38)

    subject_check_crop = crop_normalized(frame, ROI_SUBJECT_BAR_CHECK)
    subject_white_ratio = _white_pixel_ratio(subject_check_crop, threshold=200)
    subject_red_ratio = _red_pixel_ratio(subject_check_crop)
    subject_bar_ratio = max(subject_white_ratio, subject_red_ratio)

    has_logo = (
        logo_red_ratio >= MIN_LOGO_RED_RATIO
        and logo_black_ratio >= MIN_LOGO_BLACK_RATIO
    )
    has_ticker = ticker_dark_ratio >= MIN_TICKER_DARK_RATIO
    has_subject_bar = subject_bar_ratio >= MIN_SUBJECT_BAR_RATIO

    if not has_logo:
        reason = "no_cnews_logo_ad_or_jingle"
    elif not (has_ticker or has_subject_bar):
        reason = "no_cnews_banner_bar"
    else:
        reason = None

    return FrameLayoutCheck(
        is_cnews_broadcast=has_logo and (has_ticker or has_subject_bar),
        has_ticker=has_ticker,
        has_subject_bar=has_subject_bar,
        logo_red_ratio=round(logo_red_ratio, 4),
        logo_black_ratio=round(logo_black_ratio, 4),
        ticker_dark_ratio=round(ticker_dark_ratio, 4),
        subject_bar_ratio=round(subject_bar_ratio, 4),
        rejection_reason=reason,
    )


def confirm_logo_ocr(logo_texts: tuple[str, ...] | list[str]) -> bool:
    """Confirme que la zone logo en haut a gauche contient bien CNEWS / NEWS / DIRECT."""
    joined = strip_accents(" ".join(logo_texts)).upper()
    compact = joined.replace(" ", "")
    return ("NEWS" in compact) or ("CNEWS" in compact) or ("DIRECT" in compact)


def is_ad_disclaimer(text: str) -> bool:
    """Filtre de securite rejetant les mentions legales de publicites."""
    normalized = strip_accents(text).lower()
    return any(pattern in normalized for pattern in AD_DISCLAIMER_PATTERNS)
