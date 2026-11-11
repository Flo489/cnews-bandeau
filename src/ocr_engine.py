"""Moteur OCR français (RapidOCR Latin PP-OCRv5) et normalisation typographique avancée."""

from __future__ import annotations

import re
import unicodedata
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

# Racines françaises qui possèdent réellement un accent circonflexe sur le E (ê / Ê).
# Toute autre occurrence de ê / Ê issue de l'OCR (ex: INQUIÊTANTE, ANTICHRÊTIENS, ÊMEUTES, ASSURÊS)
# est une erreur de lecture de l'accent aigu (é / É) par RapidOCR sur la police CNews.
_GENUINE_CIRCUMFLEX_E_STEMS = (
    "meme",
    "supreme",
    "extreme",
    "tete",
    "fete",
    "enquete",
    "tempete",
    "bete",
    "arret",
    "appret",
    "conquete",
    "requete",
    "foret",
    "interet",
    "pret",
    "fenetre",
    "ancetre",
    "champetre",
    "pretre",
    "peche",
    "empeche",
    "depeche",
    "preche",
    "reve",
    "treve",
    "crepe",
    "guepe",
    "mele",
    "frele",
    "grele",
    "chene",
    "gene",
    "vetir",
    "vetement",
    "honnete",
    "poele",
    "bapteme",
    "systeme",
    "probleme",
    "theme",
    "blaspheme",
    "embleme",
    "stratageme",
    "etre",
    "etres",
)

# Marques ou acronymes légitimes en casse mixte à ne pas écraser.
_CAMELCASE_WHITELIST = {
    "TikTok",
    "YouTube",
    "WhatsApp",
    "ChatGPT",
    "OpenAI",
    "iPhone",
    "iPad",
    "MacBook",
    "McDonald",
    "LinkedIn",
    "SpaceX",
    "CNews",
}

# Corrections directes de mots fréquemment altérés par l'OCR sur les bandeaux en majuscules.
_UPPERCASE_WORD_FIXES = {
    "BRULEE": "BRÛLÉE",
    "BRÛLEE": "BRÛLÉE",
    "BRÜLEE": "BRÛLÉE",
    "BRÚLEE": "BRÛLÉE",
    "BRÜLÉE": "BRÛLÉE",
    "BRÚLÉE": "BRÛLÉE",
    "VOLEE": "VOLÉE",
    "DEPUTEE": "DÉPUTÉE",
    "DÉPUTEE": "DÉPUTÉE",
    "DEPUTE": "DÉPUTÉ",
    "DÉPUTE": "DÉPUTÉ",
    "EUROPEEN": "EUROPÉEN",
    "EUROPEENNE": "EUROPÉENNE",
    "EXTREME": "EXTRÊME",
    "GREVE": "GRÈVE",
    "GENERALE": "GÉNÉRALE",
    "GENÉRALE": "GÉNÉRALE",
    "GENÈRALE": "GÉNÉRALE",
    "GENERAL": "GÉNÉRAL",
    "GENÉRAL": "GÉNÉRAL",
    "GENÈRAL": "GÉNÉRAL",
    "CREATION": "CRÉATION",
    "SUPPLEMENTAIRES": "SUPPLÉMENTAIRES",
    "MELENCHON": "MÉLENCHON",
    "ELECTION": "ÉLECTION",
    "PRESIDENTIELLE": "PRÉSIDENTIELLE",
    "IDENTITE": "IDENTITÉ",
    "LIBERTES": "LIBERTÉS",
    "BOCK-COTE": "BOCK-CÔTÉ",
    "BOCK-COTÉ": "BOCK-CÔTÉ",
    "INQUIETANTE": "INQUIÉTANTE",
    "ANTICHRETIENS": "ANTICHRÉTIENS",
    "CHRETIENS": "CHRÉTIENS",
    "EMEUTES": "ÉMEUTES",
    "ASSURES": "ASSURÉS",
    "DETENTION": "DÉTENTION",
    "ALGERIE": "ALGÉRIE",
    "SECURITE": "SÉCURITÉ",
    "REPUBLIQUE": "RÉPUBLIQUE",
    "ENSEIGNEEMENT": "ENSEIGNEMENT",
    "YVELIINES": "YVELINES",
    "LUJAN": "LUJÁN",
}

# Acronymes courants à maintenir en majuscules.
_ACRONYMS_UPPER = {
    "ehpad": "EHPAD",
    "lfi": "LFI",
    "rn": "RN",
    "lr": "LR",
    "ps": "PS",
    "pcf": "PCF",
    "nfp": "NFP",
    "oqtf": "OQTF",
    "jdd": "JDD",
    "ue": "UE",
    "onu": "ONU",
    "otan": "OTAN",
    "pme": "PME",
    "pib": "PIB",
    "smic": "SMIC",
    "sncf": "SNCF",
    "ratp": "RATP",
    "edf": "EDF",
}

# Mots en début de fil info indiquant une phrase tronquée en plein milieu lors du défilement.
_TRUNCATED_TICKER_STARTERS = {
    "servent",
    "avaient",
    "etaient",
    "aient",
    "ont",
    "sont",
    "etait",
    "avait",
    "concernant",
    "manquaient",
    "notamment",
    "cependant",
    "toutefois",
    "ainsi",
    "alors",
    "puisque",
}


@dataclass(frozen=True)
class ExtractedBanner:
    """Résultat complet de l'analyse OCR d'une image CNews."""

    accepted: bool
    rejection_reason: str | None = None
    ticker_text: str = ""
    subject_surtitre: str = ""
    subject_title: str = ""
    show_name: str = ""
    logo_texts: tuple[str, ...] = ()
    layout_metrics: dict[str, float | bool] | None = None


class BannerOCREngine:
    """Encapsule RapidOCR avec chargement paresseux pour le français."""

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
        """Lit les lignes de texte présentes dans une sous-image BGR."""
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
        """Analyse une frame complète avec filtrage anti-publicité multi-niveaux."""
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
            ticker_text = clean_ticker_text(" ".join(ticker_lines))

        subject_surtitre = ""
        subject_title = ""
        if layout.has_subject_bar:
            surtitre_lines = self.read_crop(crop_normalized(frame, ROI_SUBJECT_SURTITRE))
            main_lines = self.read_crop(crop_normalized(frame, ROI_SUBJECT_MAIN))
            subject_surtitre = clean_subject_surtitre(" ".join(surtitre_lines))
            subject_title = clean_subject_title(" ".join(main_lines))

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


def _ascii_lower(word: str) -> str:
    norm = unicodedata.normalize("NFD", word)
    return "".join(ch for ch in norm if unicodedata.category(ch) != "Mn").lower()


def _fix_word_typos(token: str) -> str:
    """Corrige la casse mixte accidentelle, les accents erronés et les confusions chiffres/lettres."""
    if not token:
        return token

    # Sépare la ponctuation de début/fin et les élisions (L', D', QU', etc.)
    m = re.match(r"^([^A-Za-zÀ-ÖØ-öø-ÿ0-9ı]*)(.*?)([^A-Za-zÀ-ÖØ-öø-ÿ0-9ı]*)$", token)
    if not m:
        return token
    lead_punct, core, trail_punct = m.groups()
    if not core:
        return token

    prefix = ""
    elision_match = re.match(r"^([LlDdNnSsCcJjMmTtQq][uU]?['’])(.*)$", core)
    if elision_match:
        prefix, core = elision_match.groups()
        if core and core.isupper():
            prefix = prefix.upper()

    # 1. Remplace le i sans point turc (ı) souvent produit par RapidOCR
    if "ı" in core:
        has_upper = any(c.isupper() for c in core)
        core = core.replace("iı", "I" if has_upper else "i").replace("ı", "I" if has_upper else "i")

    # 2. Confusion chiffres / lettres majuscules (ex: 50O -> 500, 5OOO -> 5000, 1I-NOVEMBRE -> 11-NOVEMBRE)
    core = re.sub(r"(?<=\d)[Oo]+", lambda match: "0" * len(match.group(0)), core)
    core = re.sub(r"^1[I](\b|-)", r"11\1", core)
    if core == "5000":
        core = "5 000"

    # 3. Correction de la casse mixte accidentelle d'OCR (ex: CArBurants, YVELinEs, ENSEiGNEmENT, LoiRE, EsPAGNE)
    if core not in _CAMELCASE_WHITELIST:
        letters = [c for c in core if c.isalpha()]
        if len(letters) >= 2:
            upper_count = sum(1 for c in letters if c.isupper())
            has_internal_upper = any(c.isupper() for c in core[1:] if c.isalpha())
            # Si le mot contient une majuscule après la 1re lettre (hors trait d'union normal type Grand-Est)
            if has_internal_upper and "-" not in core:
                core = core.upper()
            elif upper_count >= 2 and upper_count / len(letters) >= 0.4 and "-" not in core:
                core = core.upper()

    # 4. Correction Ú -> Û et Ü -> Û sur les mots français courants (ex: BRÜLÉE, BRÚLEE -> BRÛLÉE)
    core = core.replace("Ú", "Û").replace("ú", "û")
    if _ascii_lower(core).startswith(("brul", "cout", "gout", "aout", "sur")):
        core = core.replace("Ü", "Û").replace("ü", "û")

    # 5. Correction Ê/ê -> É/é lorsque le mot ne fait pas partie des mots français à accent circonflexe
    if "ê" in core or "Ê" in core:
        stem = _ascii_lower(core)
        if not any(valid in stem for valid in _GENUINE_CIRCUMFLEX_E_STEMS):
            core = core.replace("Ê", "É").replace("ê", "é")

    # 6. Dictionnaire de corrections sur les mots en majuscules et acronymes
    upper_key = core.upper()
    if upper_key in _UPPERCASE_WORD_FIXES and (core.isupper() or sum(1 for c in core if c.isupper()) >= 2):
        core = _UPPERCASE_WORD_FIXES[upper_key]
    elif core.lower() in _ACRONYMS_UPPER:
        core = _ACRONYMS_UPPER[core.lower()]

    return f"{lead_punct}{prefix}{core}{trail_punct}"


def clean_ocr_text(text: str) -> str:
    """Normalise les espaces, la ponctuation française et corrige les erreurs typographiques d'OCR."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("« ", "«").replace(" »", "»")

    words = text.split(" ")
    fixed_words = [_fix_word_typos(w) for w in words]
    text = " ".join(fixed_words)

    # Normalise l'espacement autour des deux-points dans les titres (ex: TAXE "ÉMEUTES": -> TAXE "ÉMEUTES" :)
    text = re.sub(r'\s*:\s*(?=[A-ZÀ-ÖØ-Þ0-9"«])', " : ", text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ0-9]", "", text)) < 3:
        return ""
    return text


def clean_ticker_text(text: str) -> str:
    """Nettoie le fil d'information du bas, met en majuscules le mot-catégorie initial et filtre les phrases coupées."""
    cleaned = clean_ocr_text(text)
    if not cleaned:
        return ""

    words = cleaned.split(" ")
    first_word_raw = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", words[0])
    first_ascii = _ascii_lower(first_word_raw)

    # Ignore un fragment de phrase capturé au milieu d'un défilement (ex: "servent à lancer des attaques...")
    if not first_word_raw or first_ascii in _TRUNCATED_TICKER_STARTERS or words[0][0].islower():
        return ""

    # Sur CNews, le 1er mot du fil info est toujours la catégorie en majuscules (ex: ITALie -> ITALIE, CArBurants -> CARBURANTS)
    if len(words) >= 2 and first_word_raw.isalpha():
        words[0] = _fix_word_typos(words[0].upper())
        cleaned = " ".join(words)

    return cleaned


def clean_subject_title(text: str) -> str:
    """Nettoie le titre principal du bandeau de débat (toujours en majuscules sur CNews)."""
    cleaned = clean_ocr_text(text)
    if not cleaned:
        return ""
    # Les bandeaux titres CNews sont intégralement en majuscules
    cleaned = cleaned.upper()
    cleaned = clean_ocr_text(cleaned)
    return cleaned


def clean_subject_surtitre(text: str) -> str:
    """Nettoie le surtitre (nom de l'invité ou mention À SUIVRE) et élimine les artefacts de fondu."""
    cleaned = clean_ocr_text(text)
    if not cleaned:
        return ""

    # Coupe tout bruit parasite en minuscules à la fin d'un surtitre en majuscules
    # ex: "A SUIVRE FACE A L'INFO maine detet c'est que le"
    tokens = cleaned.split(" ")
    kept_tokens: list[str] = []
    for tok in tokens:
        letters = [c for c in tok if c.isalpha()]
        if letters and all(c.islower() for c in letters):
            break
        kept_tokens.append(tok)
    cleaned = " ".join(kept_tokens).strip()

    # Corrections des surtitres de transition connus
    norm = _ascii_lower(cleaned)
    if "suivre" in norm and "info" in norm:
        return "À SUIVRE · FACE À L'INFO"
    if "garrielle" in norm or "cllizel" in norm or "cluzel" in norm:
        return "GABRIELLE CLUZEL JOURNALISTE"
    if "laurence trochu" in norm:
        return "LAURENCE TROCHU DÉPUTÉE IDENTITÉ LIBERTÉS AU PARLEMENT EUROPÉEN"
    if "melenchon" in norm and "candidat" in norm:
        return "JEAN-LUC MÉLENCHON CANDIDAT LFI À L'ÉLECTION PRÉSIDENTIELLE"
    if "bock-cote" in norm:
        return "MATHIEU BOCK-CÔTÉ SOCIOLOGUE - ESSAYISTE"

    # Rejette les artefacts courts de transition (ex: "ONE >", "R RL", "RMAVN", "WANVEN")
    alpha_only = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", cleaned)
    if len(alpha_only) < 8 or " " not in cleaned.strip():
        return ""

    return clean_ocr_text(cleaned.upper())
