"""Configuration, zones d'intérêt calibrées sur le flux CNews et lexiques d'analyse."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DOCS_DATA_DIR = BASE_DIR / "docs" / "data"

# Coordonnées normalisées (y1, y2, x1, x2) calibrées sur des captures réelles 1280x720.
ROI_LOGO_FULL = (0.040, 0.140, 0.040, 0.160)
ROI_LOGO_RED = (0.048, 0.090, 0.068, 0.138)
ROI_LOGO_BLACK = (0.048, 0.090, 0.045, 0.070)

ROI_SUBJECT_SURTITRE = (0.735, 0.788, 0.000, 0.825)
ROI_SUBJECT_MAIN = (0.785, 0.872, 0.000, 0.825)
ROI_SUBJECT_BAR_CHECK = (0.790, 0.865, 0.040, 0.780)

ROI_TICKER = (0.870, 0.995, 0.000, 0.825)
ROI_TICKER_BG_CHECK = (0.875, 0.990, 0.020, 0.780)
ROI_SHOW_BADGE = (0.865, 0.995, 0.825, 1.000)

# Seuils anti-publicité observés sur flux CNews vs écrans publicitaires.
MIN_LOGO_RED_RATIO = 0.12
MIN_LOGO_BLACK_RATIO = 0.25
MIN_TICKER_DARK_RATIO = 0.55
MIN_SUBJECT_BAR_RATIO = 0.45

# Mentions légales publicitaires connues rejetées en filet de sécurité.
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

# Liste étendue de mots vides français pour éviter tout bruit statistique (ex: "matin", "ont", "été").
FRENCH_STOPWORDS = {
    "a", "au", "aux", "avec", "ce", "ces", "cette", "cet", "dans", "de", "des",
    "du", "elle", "elles", "en", "et", "est", "ete", "etre", "eu", "fait", "faits",
    "faire", "fois", "font", "hors", "ici", "il", "ils", "je", "l", "la", "le",
    "les", "leur", "leurs", "lors", "lui", "ma", "mais", "me", "meme", "memes",
    "mes", "mien", "mieux", "moins", "mon", "mot", "ne", "ni", "nos", "notre",
    "nous", "on", "ont", "ou", "par", "parce", "pas", "peu", "peut", "peuvent",
    "plus", "plutot", "pour", "pourquoi", "pres", "puis", "qu", "quand", "que",
    "quel", "quelle", "quelles", "quels", "elton", "qui", "quoi", "sa", "sans",
    "se", "selon", "sera", "seront", "ses", "seulement", "si", "sien", "soi",
    "soit", "son", "sont", "sous", "soyez", "sujet", "sur", "ta", "tandis",
    "ant", "te", "tels", "tes", "toi", "ton", "tous", "tout", "toute", "toutes",
    "tres", "trop", "tu", "un", "une", "valeur", "vers", "via", "voici", "voila",
    "vont", "vos", "votre", "vous", "vu", "y", "d", "n", "s", "c", "j", "m", "t",
    # Auxiliaires, verbes fréquents et mots temporels / génériques de bandeaux TV
    "avait", "avoir", "avant", "apres", "alors", "annonce", "annoncent", "ans",
    "annee", "annees", "aujourd", "hui", "autre", "autres", "avoir", "bien",
    "cible", "comme", "contre", "concerne", "concernant", "courrier", "creation",
    "depuis", "deux", "doit", "donc", "dont", "debut", "deja", "dernier",
    "derniere", "entre", "environ", "etaient", "etait", "etant", "face", "faut",
    "fin", "grand", "grande", "grands", "grandes", "grace", "heure", "heures",
    "hier", "info", "infos", "jour", "jours", "journee", "jusqu", "lancer",
    "liste", "leurs", "lieu", "loin", "long", "lundi", "mardi", "mercredi",
    "jeudi", "vendredi", "samedi", "dimanche", "matin", "midi", "soir", "nuit",
    "mais", "malgre", "manquaient", "manque", "media", "medias", "mettre",
    "million", "millions", "mis", "mise", "mois", "monde", "non", "nouveau",
    "nouvelle", "nouveaux", "nouvelles", "novembre", "octobre", "decembre",
    "janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
    "septembre", "ouvre", "parle", "part", "parti", "partie", "partir", "passe",
    "pendant", "personne", "personnes", "place", "places", "plusieurs", "point",
    "points", "pourrait", "premier", "premiere", "prend", "prendre", "pris",
    "prise", "projet", "propos", "publique", "publics", "public", "quatre",
    "quelque", "quelques", "question", "questions", "reste", "ressemble",
    "savoir", "semaine", "semaines", "servent", "seul", "seule", "six", "sept",
    "huit", "neuf", "dix", "suite", "suivre", "supplementaires", "tant", "temps",
    "toujours", "toute", "trois", "trouve", "veut", "vient", "voir", " veulent",
    "cnews", "direct", "journaliste", "essayiste", "sociologue", "adresse",
}


@dataclass(frozen=True)
class Settings:
    """Paramètres d'exécution lus depuis l'environnement."""

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
    """Découpe une sous-image à partir de coordonnées normalisées (y1, y2, x1, x2)."""
    h, w = image.shape[:2]
    y1, y2, x1, x2 = roi
    return image[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
