"""Moteur OCR francais (RapidOCR Latin PP-OCRv5) et extraction structuree."""

from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

from .banner_detector import confirm_logo_ocr, inspect_frame_layout, is_ad_disclaimer
from .config import (
    ROI_LOGO_FULL,
    ROI_SHOW_BADGE,
    ROI_SUBJECT_MAIN,
    ROI_SUBJECT_SURTITRE,
    ROI_TICKER,
    crop_normalized,
)


@dataclass(frozen=True)
class ExtractedBanner:
    """Resultat complet de l'analyse OCR d'une image CNews."""

    accepted: bool
    rejection_reason: str | None = None
    ticker_text: str = ""
    subject_surtitre: str = ""
    subject_title: str = ""
    show_name: str = ""
    logo_texts: tuple[str, ...] = ()
    layout_metrics: dict[str, float | bool] | None = None


class BannerOCREngine:
    """Encapsule RapidOCR avec chargement paresseux pour le francais."""

    def __init__(self) -> None:
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

            self._engine = RapidOCR(
                params={
                    "Rec.lang_type": LangRec.LATIN,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.model_type": ModelType.MOBILE,
                }
            )
        return self._engine

    def read_crop(self, crop_bgr: np.ndarray, upscale: float = 1.0) -> tuple[str, ...]:
        """Lit les lignes de texte presentes dans une sous-image BGR."""
        if crop_bgr is None or crop_bgr.size == 0:
            return ()
        img = crop_bgr
        if upscale != 1.0:
            img = cv2.resize(
                crop_bgr,
                None,
                fx=upscale,
                fy=upscale,
                interpolation=cv2.INTER_CUBIC,
            )
        res = self._get_engine()(img)
        if not res or not getattr(res, "txts", None):
            return ()
        return tuple(str(t).strip() for t in res.txts if str(t).strip())

    def extract_from_frame(self, frame: np.ndarray, verify_logo_ocr: bool = True) -> ExtractedBanner:
        """Analyse une frame complete avec filtrage anti-publicite multi-niveaux."""
        layout = inspect_frame_layout(frame)
        metrics = {
            "logo_red_ratio": layout.logo_red_ratio,
            "logo_black_ratio": layout.logo_black_ratio,
            "ticker_dark_ratio": layout.ticker_dark_ratio,
            "subject_bar_ratio": layout.subject_bar_ratio,
            "has_ticker": layout.has_ticker,
            "has_subject_bar": layout.has_subject_bar,
        }
        if not layout.is_cnews_broadcast:
            return ExtractedBanner(
                accepted=False,
                rejection_reason=layout.rejection_reason,
                layout_metrics=metrics,
            )

        logo_texts: tuple[str, ...] = ()
        if verify_logo_ocr:
            logo_texts = self.read_crop(crop_normalized(frame, ROI_LOGO_FULL), upscale=1.5)
            if not confirm_logo_ocr(logo_texts):
                return ExtractedBanner(
                    accepted=False,
                    rejection_reason="logo_ocr_mismatch",
                    logo_texts=logo_texts,
                    layout_metrics=metrics,
                )

        ticker_text = ""
        if layout.has_ticker:
            ticker_lines = self.read_crop(crop_normalized(frame, ROI_TICKER))
            ticker_text = clean_ocr_text(" ".join(ticker_lines))

        subject_surtitre = ""
        subject_title = ""
        if layout.has_subject_bar:
            surtitre_lines = self.read_crop(crop_normalized(frame, ROI_SUBJECT_SURTITRE))
            main_lines = self.read_crop(crop_normalized(frame, ROI_SUBJECT_MAIN))
            subject_surtitre = clean_ocr_text(" ".join(surtitre_lines))
            subject_title = clean_ocr_text(" ".join(main_lines))

        show_lines = self.read_crop(crop_normalized(frame, ROI_SHOW_BADGE))
        show_name = clean_ocr_text(" ".join(show_lines))

        combined_check = f"{ticker_text} {subject_surtitre} {subject_title}".strip()
        if not combined_check:
            return ExtractedBanner(
                accepted=False,
                rejection_reason="empty_ocr_text",
                logo_texts=logo_texts,
                layout_metrics=metrics,
            )

        if is_ad_disclaimer(combined_check):
            return ExtractedBanner(
                accepted=False,
                rejection_reason="ad_disclaimer_text",
                logo_texts=logo_texts,
                layout_metrics=metrics,
            )

        return ExtractedBanner(
            accepted=True,
            ticker_text=ticker_text,
            subject_surtitre=subject_surtitre,
            subject_title=subject_title,
            show_name=show_name,
            logo_texts=logo_texts,
            layout_metrics=metrics,
        )


def clean_ocr_text(text: str) -> str:
    """Normalise les espaces, guillemets et filtre les artefacts isoles."""
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("« ", "«").replace(" »", "»")
    if len(re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ0-9]", "", text)) < 3:
        return ""
    return text
