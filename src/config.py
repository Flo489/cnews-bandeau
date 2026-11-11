"""Configuration et zones d'interet calibrees sur le flux CNews."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DOCS_DATA_DIR = BASE_DIR / "docs" / "data"

# Coordonnees normalisees (y1, y2, x1, x2) calibrees sur des captures reelles 1280x720.
ROI_LOGO_FULL = (0.040, 0.140, 0.040, 0.160)
ROI_LOGO_RED = (0.048, 0.090, 0.068, 0.138)
ROI_LOGO_BLACK = (0.048, 0.090, 0.045, 0.070)

ROI_SUBJECT_SURTITRE = (0.735, 0.788, 0.000, 0.825)
ROI_SUBJECT_MAIN = (0.785, 0.872, 0.000, 0.825)
ROI_SUBJECT_BAR_CHECK = (0.790, 0.865, 0.040, 0.780)

ROI_TICKER = (0.870, 0.995, 0.000, 0.825)
ROI_TICKER_BG_CHECK = (0.875, 0.990, 0.020, 0.780)
ROI_SHOW_BADGE = (0.865, 0.995, 0.825, 1.000)

# Seuils anti-publicite observes sur flux CNews vs ecrans publicitaires.
MIN_LOGO_RED_RATIO = 0.12
MIN_LOGO_BLACK_RATIO = 0.25
MIN_TICKER_DARK_RATIO = 0.55
MIN_SUBJECT_BAR_RATIO = 0.45

# Mentions legales publicitaires connues rejetees en filet de securite.
AD_DISCLAIMER_PATTERNS = (
    "pour votre sante",
    "mangerbouger",
    "l'energie est notre avenir",
    "economisons-la",
    "appel non surtaxe",
    "offre soumise a conditions",
    "voir conditions sur",
    "un credit vous engage",
    "l'abus d'alcool est dangereux",
    "application par jour",
    "etude clinique",
)

FRENCH_STOPWORDS = {
    "a", "au", "aux", "avec", "ce", "ces", "cette", "cet", "dans", "de", "des",
    "du", "elle", "elles", "en", "et", "est", "etre", "il", "ils", "je", "l",
    "la", "le", "les", "leur", "leurs", "lui", "ma", "mais", "me", "mes", "mon",
    "ne", "nos", "notre", "nous", "on", "ou", "par", "pas", "plus", "pour",
    "qu", "que", "qui", "quoi", "sa", "sans", "se", "ses", "son", "sont",
    "sur", "ta", "te", "tes", "toi", "ton", "tous", "tout", "toute", "toutes",
    "tu", "un", "une", "vos", "votre", "vous", "y", "d", "n", "s", "c", "j",
    "apres", "avant", "contre", "entre", "face", "fait", "faits", "selon",
    "sous", "vers", "direct", "cnews", "info", "infos", "medias", "france",
}


@dataclass(frozen=True)
class Settings:
    """Parametres d'execution lus depuis l'environnement."""

    dailymotion_id: str = os.getenv("CNEWS_DAILYMOTION_ID", "x3b68jn")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip()
    analysis_interval_s: int = int(os.getenv("ANALYSIS_INTERVAL_SECONDS", "300"))
    hf_token: str = os.getenv("HF_TOKEN", "").strip()
    hf_space_repo: str = os.getenv("HF_SPACE_REPO", "flo6259/cnews-bandeau").strip()
    apps_script_url: str = os.getenv("GOOGLE_APPS_SCRIPT_URL", "").strip()
    sheets_secret: str = os.getenv("SHEETS_WEBHOOK_SECRET", "").strip()
    timezone: str = os.getenv("TIMEZONE", "Europe/Paris").strip()


def crop_normalized(image, roi: tuple[float, float, float, float]):
    """Decoupe une sous-image a partir de coordonnees normalisees (y1, y2, x1, x2)."""
    h, w = image.shape[:2]
    y1, y2, x1, x2 = roi
    return image[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
