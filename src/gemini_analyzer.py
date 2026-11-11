"""Analyse thématique impartiale, curseur Gauche-Droite, étude des partis (LFI, etc.) par heure et IA Gemini (cycle 5 min)."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter, defaultdict
from dataclasses import asdict
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

# Définition impartiale des macro-thèmes (grille d'analyse des médias audiovisuels)
# Chaque thème possède ses mots-clés et une valence d'agenda-setting en science politique
# (-1.0 = forte saillance Gauche / Social, 0.0 = Neutre / Institutionnel, +1.0 = forte saillance Droite / Identité-Sécurité).
THEME_DEFINITIONS: list[dict[str, object]] = [
    {
        "name": "Identité, Religion & Immigration",
        "color": "#991b1b",
        "orientation": "droite",
        "axis_weight": 0.80,
        "keywords": (
            "islam", "islamisme", "islamiste", "musulman", "antichretien", "antichretiens",
            "chretien", "chretiens", "catholique", "relique", "saint nicolas", "eglise",
            "pape", "laicite", "racisme antiblanc", "antiblanc", "identite", "immigration",
            "migrant", "migrants", "oqtf", "frontiere", "frontieres", "expulsion",
            "communautarisme", "voile", "abaya", "mosquee", "antisemitisme",
        ),
    },
    {
        "name": "Sécurité, Justice & Ordre public",
        "color": "#dc2626",
        "orientation": "droite",
        "axis_weight": 0.65,
        "keywords": (
            "emeute", "emeutes", "attentat", "police", "policier", "gendarmerie",
            "delinquance", "agression", "attaque", "attaques", "saccage", "saccages",
            "incendie", "incendies", "effraction", "vol", "volee", "brulee", "bombe",
            "alerte", "alertes", "evacuees", "parquet", "enquete", "examen", "prison",
            "detention", "pedophile", "pedophiles", "drogue", "narcotrafic", "insecurite",
        ),
    },
    {
        "name": "Politique & Partis",
        "color": "#1e3a8a",
        "orientation": "neutre",
        "axis_weight": 0.15,
        "keywords": (
            "melenchon", "lfi", "insoumis", "insoumise", "campagne politique",
            "presidentielle", "election", "candidat", "depute", "deputee", "parlement",
            "assemblee", "senat", "gouvernement", "macron", "le pen", "bardella",
            "extrême droite", "extreme droite", "gauche", "droite", "sopo",
        ),
    },
    {
        "name": "Économie, Fiscalité & Carburants",
        "color": "#d97706",
        "orientation": "droite",
        "axis_weight": 0.35,
        "keywords": (
            "taxe", "taxes", "impot", "impots", "assures", "assurance", "carburant",
            "carburants", "stations-service", "essence", "prix", "inflation",
            "pouvoir d'achat", "budget", "dette", "entreprise", "croissance",
        ),
    },
    {
        "name": "Social, Éducation & Services publics",
        "color": "#2563eb",
        "orientation": "gauche",
        "axis_weight": -0.60,
        "keywords": (
            "greve", "greve generale", "salaires", "salaire", "logement", "logements",
            "enseignement", "universite", "universites", "licence", "ecole", "ecoles",
            "etudiant", "etudiants", "ehpad", "hopital", "sante", "retraite", "retraites",
            "syndicat", "chomage", "smic", "service public",
        ),
    },
    {
        "name": "International & Géopolitique",
        "color": "#475569",
        "orientation": "neutre",
        "axis_weight": 0.05,
        "keywords": (
            "iran", "iranien", "bases americaines", "italie", "italiens", "espagne",
            "algerie", "lujan", "europe", "ukraine", "russie", "israel", "gaza",
            "etats-unis", "diplomatie", "otan", "onu",
        ),
    },
    {
        "name": "Écologie & Environnement",
        "color": "#059669",
        "orientation": "gauche",
        "axis_weight": -0.65,
        "keywords": (
            "climat", "climatique", "ecologie", "ecologique", "environnement",
            "biodiversite", "pollution", "transition energetique", "renouvelable",
        ),
    },
]

# Définition des partis et blocs politiques suivis pour l'étude horaire et l'analyse des critiques
PARTY_DEFINITIONS: list[dict[str, object]] = [
    {
        "id": "LFI",
        "name": "La France Insoumise (LFI)",
        "short_name": "LFI (France Insoumise)",
        "color": "#c2410c",
        "keywords": (
            "lfi", "france insoumise", "insoumis", "insoumise", "melenchon",
            "jean-luc melenchon", "panot", "bompard", "coquerel", "rima hassan",
            "hassan", "guette", "delogu", "boyard", "autain", "corbiere", "obono",
            "caron", "chikirou", "islamo-gauchisme", "extreme gauche",
        ),
    },
    {
        "id": "GAUCHE",
        "name": "Gauche (PS, Écologistes, PCF & Mouvements)",
        "short_name": "Gauche / PS / Écolos",
        "color": "#e11d48",
        "keywords": (
            "personnalites de gauche", "gauche", "socialiste", "parti socialiste",
            "faure", "glucksmann", "hollande", "ecologistes", "tondelier", "rousseau",
            "pcf", "roussel", "nfp", "nouveau front populaire", "sopo", "sos racisme",
            "greve generale",
        ),
    },
    {
        "id": "GOUV",
        "name": "Gouvernement & Camp présidentiel",
        "short_name": "Gouvernement / Macron",
        "color": "#0284c7",
        "keywords": (
            "gouvernement", "lecornu", "macron", "emmanuel macron", "bayrou",
            "retailleau", "darmanin", "attal", "borne", "barrot", "ministre",
            "majorite presidentielle", "renaissance", "elysee", "matignon",
        ),
    },
    {
        "id": "LR",
        "name": "Droite républicaine (LR)",
        "short_name": "Droite (LR)",
        "color": "#1d4ed8",
        "keywords": (
            "les republicains", "wauquiez", "larcher", "bellamy", "ciotti",
            "droite parlementaire",
        ),
    },
    {
        "id": "RN_DROITE_ID",
        "name": "RN, Identité-Libertés & Droite identitaire",
        "short_name": "RN / Droite identitaire",
        "color": "#1e293b",
        "keywords": (
            "rassemblement national", "le pen", "marine le pen", "bardella",
            "chenu", "tanguy", "identite libertes", "laurence trochu", "trochu",
            "reconquete", "zemmour", "marechal", "marion marechal", "extreme droite",
        ),
    },
]


def _norm_text(text: str) -> str:
    return strip_accents(text).lower()


def classify_banner_record(rec: BannerRecord) -> dict[str, object]:
    """Classifie un bandeau de manière impartiale : thème principal, score d'axe et partis détectés."""
    title_norm = _norm_text(f"{rec.subject_surtitre} {rec.subject_title}")
    ticker_norm = _norm_text(rec.ticker_text)
    full_norm = f"{title_norm} {ticker_norm}".strip()

    # 1. Détection des thèmes (le bandeau principal pèse 2.5x plus que le fil info)
    theme_scores: dict[str, float] = defaultdict(float)
    for tdef in THEME_DEFINITIONS:
        tname = str(tdef["name"])
        for kw in tdef["keywords"]:  # type: ignore[attr-defined]
            pattern = r"\b" + re.escape(kw) + r"\b"
            if re.search(pattern, title_norm):
                theme_scores[tname] += 2.5
            if re.search(pattern, ticker_norm):
                theme_scores[tname] += 1.0

    if theme_scores:
        sorted_themes = sorted(theme_scores.items(), key=lambda x: x[1], reverse=True)
        primary_theme = sorted_themes[0][0]
        secondary_theme = sorted_themes[1][0] if len(sorted_themes) > 1 else ""
    else:
        primary_theme = "Actualité générale & Société"
        secondary_theme = ""

    # 2. Calcul du score de positionnement thématique/cadrage du bandeau (-100 à +100)
    theme_lookup = {str(t["name"]): t for t in THEME_DEFINITIONS}
    if primary_theme in theme_lookup:
        base_weight = float(theme_lookup[primary_theme]["axis_weight"])  # type: ignore[arg-type]
        orientation = str(theme_lookup[primary_theme]["orientation"])
    else:
        base_weight = 0.0
        orientation = "neutre"

    # Affinement selon le cadrage spécifique du titre principal
    framing_bonus = 0.0
    if any(k in title_norm for k in ("antichretien", "antiblanc", "islam", "relique", "taxe \"emeutes\"", "trochu", "bock-cote", "ornellas")):
        framing_bonus += 0.20
    if any(k in title_norm for k in ("melenchon", "lfi")) and '"' in rec.subject_title:
        # Citation polémique sur un candidat de gauche
        framing_bonus += 0.25
    if any(k in title_norm for k in ("greve generale", "salaires, logements", "places supplementaires en licence")):
        framing_bonus -= 0.15

    banner_axis_score = max(-100, min(100, round((base_weight + framing_bonus) * 100)))

    # 3. Détection des partis politiques et qualification de la tonalité (Critique / Neutre / Tribune)
    try:
        hour_str = rec.first_seen[11:13] + "h"
    except Exception:  # noqa: BLE001
        hour_str = "00h"

    detected_parties: list[dict[str, str]] = []
    for pdef in PARTY_DEFINITIONS:
        pid = str(pdef["id"])
        pname = str(pdef["name"])
        pshort = str(pdef["short_name"])
        matched_in_title = False
        matched_in_surtitre = False
        matched_in_ticker = False

        surtitre_only_norm = _norm_text(rec.subject_surtitre)
        subject_only_norm = _norm_text(rec.subject_title)

        for kw in pdef["keywords"]:  # type: ignore[attr-defined]
            pattern = r"\b" + re.escape(kw) + r"\b"
            if re.search(pattern, surtitre_only_norm):
                matched_in_surtitre = True
            if re.search(pattern, subject_only_norm):
                matched_in_title = True
            if re.search(pattern, ticker_norm):
                matched_in_ticker = True

        if not (matched_in_surtitre or matched_in_title or matched_in_ticker):
            continue

        # Qualification impartiale de la tonalité du bandeau vis-à-vis du parti détecté
        stance = "Neutre / Factuel"
        reason = "Mention factuelle dans le fil d'information ou le titre."

        if pid == "LFI":
            if '"' in rec.subject_title or "ressemble a quelque chose" in subject_only_norm or any(
                w in full_norm for w in ("polemique", "accuse", "cible", "derapage", "critique", "condamne", "attaque")
            ):
                stance = "Critique / Polémique"
                reason = "Citation ironique ou angle critique sur la candidature / les prises de position de LFI."
        elif pid == "GAUCHE":
            if "antiblanc" in subject_only_norm or "cible" in subject_only_norm or "pas de projet" in ticker_norm:
                stance = "Critique / Polémique"
                reason = "Mise en cause sur le thème du racisme antiblanc ou relativisation dans le fil info."
            elif "greve generale" in subject_only_norm:
                stance = "Neutre / Factuel"
                reason = "Couverture factuelle d'une mobilisation sociale (salaires, logements)."
        elif pid == "GOUV":
            if any(w in full_norm for w in ("manquaient", "penurie", "hausse", "taxe", "colere", "echec", "critique")):
                stance = "Critique / Polémique"
                reason = "Angle soulignant une pénurie (carburants) ou une hausse de fiscalité."
            else:
                stance = "Neutre / Factuel"
                reason = "Annonce institutionnelle ou gouvernementale (ex: création de places en Licence)."
        elif pid == "RN_DROITE_ID":
            if matched_in_surtitre and any(w in surtitre_only_norm for w in ("deputee", "depute", "identite libertes")):
                stance = "Tribune / Invité"
                reason = "Représentant politique invité à s'exprimer en plateau."
            elif "cible l'extreme droite" in subject_only_norm:
                stance = "Tribune / Invité"
                reason = "Débat en plateau contestant la mise en cause de la droite par SOS Racisme."

        excerpt = (
            f"{rec.subject_surtitre + ' — ' if rec.subject_surtitre else ''}{rec.subject_title}"
            if (matched_in_surtitre or matched_in_title) and rec.subject_title
            else rec.ticker_text
        )

        detected_parties.append(
            {
                "party_id": pid,
                "party_name": pname,
                "party_short": pshort,
                "stance": stance,
                "reason": reason,
                "hour": hour_str,
                "excerpt": excerpt,
            }
        )

    return {
        "primary_theme": primary_theme,
        "secondary_theme": secondary_theme,
        "orientation": orientation,
        "axis_score": banner_axis_score,
        "hour": hour_str,
        "parties": detected_parties,
    }


def extract_top_keywords(records: list[BannerRecord], limit: int = 12) -> list[dict[str, object]]:
    """Calcule les sujets et termes clés les plus présents à l'antenne (sans mots vides ni doublons de boucle)."""
    counts: Counter[str] = Counter()
    display_form: dict[str, str] = {}

    # Agrège les occurrences réelles par texte unique (titre principal d'un côté, fil info de l'autre)
    title_samples: Counter[str] = Counter()
    title_raw: dict[str, str] = {}
    ticker_samples: Counter[str] = Counter()
    ticker_raw: dict[str, str] = {}

    for rec in records:
        if rec.subject_title:
            sig = _norm_text(rec.subject_title)
            title_samples[sig] += max(1, rec.sample_count)
            title_raw[sig] = rec.subject_title
        if rec.ticker_text:
            sig_t = _norm_text(rec.ticker_text)
            ticker_samples[sig_t] += 1
            ticker_raw[sig_t] = rec.ticker_text

    weighted_texts: list[tuple[str, int]] = []
    for sig, samples in title_samples.items():
        # Pondération par présence à l'antenne du sujet principal (plafonnée pour l'équilibre visuel)
        w = max(2, min(10, round(samples ** 0.5) * 2))
        weighted_texts.append((title_raw[sig], w))
    for sig_t, samples in ticker_samples.items():
        w = max(1, min(4, samples))
        weighted_texts.append((ticker_raw[sig_t], w))

    for text, weight in weighted_texts:
        seen_in_text: set[str] = set()
        for raw_word in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ-]{4,}", text):
            norm = strip_accents(raw_word).lower().strip("-")
            if len(norm) < 4 or norm in FRENCH_STOPWORDS or norm.isdigit():
                continue
            if norm not in display_form:
                display_form[norm] = raw_word.lower()
            if norm not in seen_in_text:
                counts[norm] += weight
                seen_in_text.add(norm)

    return [
        {"keyword": display_form[norm], "count": cnt}
        for norm, cnt in counts.most_common(limit)
    ]


def compute_political_and_thematic_analysis(records: list[BannerRecord]) -> dict[str, object]:
    """Calcule le curseur Gauche-Droite impartial, la répartition thématique et l'étude horaire des partis (dont LFI)."""
    theme_counts: Counter[str] = Counter()
    theme_weighted: Counter[str] = Counter()
    orientation_weighted: Counter[str] = Counter()

    total_weight = 0
    weighted_axis_sum = 0.0

    party_stats: dict[str, dict[str, object]] = {
        str(p["id"]): {
            "party_id": str(p["id"]),
            "party_name": str(p["name"]),
            "party_short": str(p["short_name"]),
            "color": str(p["color"]),
            "total_mentions": 0,
            "weighted_occurrences": 0,
            "critique_count": 0,
            "neutre_count": 0,
            "tribune_count": 0,
            "hours": Counter(),
            "excerpts": [],
        }
        for p in PARTY_DEFINITIONS
    }

    # Matrice horaire (00h..23h) par parti
    hourly_party_matrix: dict[str, dict[str, int]] = {
        f"{h:02d}h": {str(p["id"]): 0 for p in PARTY_DEFINITIONS}
        for h in range(24)
    }
    hourly_critiques_matrix: dict[str, dict[str, int]] = {
        f"{h:02d}h": {str(p["id"]): 0 for p in PARTY_DEFINITIONS}
        for h in range(24)
    }

    enriched_banners_meta: dict[str, dict[str, object]] = {}
    political_timeline: list[dict[str, str]] = []

    for rec in records:
        info = classify_banner_record(rec)
        enriched = {
            "primary_theme": info["primary_theme"],
            "secondary_theme": info["secondary_theme"],
            "orientation": info["orientation"],
            "axis_score": info["axis_score"],
            "parties": info["parties"],
        }
        enriched_banners_meta[rec.id] = enriched

        weight = max(1, rec.sample_count)
        ptheme = str(info["primary_theme"])
        orient = str(info["orientation"])
        axis_score = float(info["axis_score"])  # type: ignore[arg-type]

        theme_counts[ptheme] += 1
        theme_weighted[ptheme] += weight
        orientation_weighted[orient] += weight
        total_weight += weight
        weighted_axis_sum += axis_score * weight

        hour_key = str(info["hour"])
        for p_hit in info["parties"]:  # type: ignore[attr-defined]
            pid = p_hit["party_id"]
            stance = p_hit["stance"]
            p_entry = party_stats[pid]
            p_entry["total_mentions"] = int(p_entry["total_mentions"]) + 1
            p_entry["weighted_occurrences"] = int(p_entry["weighted_occurrences"]) + weight
            if stance == "Critique / Polémique":
                p_entry["critique_count"] = int(p_entry["critique_count"]) + 1
                if hour_key in hourly_critiques_matrix:
                    hourly_critiques_matrix[hour_key][pid] += 1
            elif stance == "Tribune / Invité":
                p_entry["tribune_count"] = int(p_entry["tribune_count"]) + 1
            else:
                p_entry["neutre_count"] = int(p_entry["neutre_count"]) + 1

            p_entry["hours"][hour_key] += 1  # type: ignore[index]
            if hour_key in hourly_party_matrix:
                hourly_party_matrix[hour_key][pid] += 1

            ex_list: list[dict[str, str]] = p_entry["excerpts"]  # type: ignore[assignment]
            if not any(e["excerpt"] == p_hit["excerpt"] for e in ex_list):
                ex_list.append(
                    {
                        "time": rec.first_seen[11:16] if len(rec.first_seen) >= 16 else hour_key,
                        "hour": hour_key,
                        "stance": stance,
                        "excerpt": p_hit["excerpt"],
                        "reason": p_hit["reason"],
                    }
                )

            political_timeline.append(
                {
                    "time": rec.first_seen[11:16] if len(rec.first_seen) >= 16 else hour_key,
                    "hour": hour_key,
                    "party_id": pid,
                    "party_short": p_hit["party_short"],
                    "stance": stance,
                    "excerpt": p_hit["excerpt"],
                    "reason": p_hit["reason"],
                }
            )

    computed_cursor = round(weighted_axis_sum / total_weight) if total_weight > 0 else 0
    right_pct = round(100 * orientation_weighted["droite"] / total_weight) if total_weight > 0 else 0
    left_pct = round(100 * orientation_weighted["gauche"] / total_weight) if total_weight > 0 else 0
    neutral_pct = max(0, 100 - right_pct - left_pct) if total_weight > 0 else 100

    if computed_cursor >= 45:
        cursor_label = "Droite conservatrice & identitaire"
    elif computed_cursor >= 18:
        cursor_label = "Centre-droit / Droite"
    elif computed_cursor > -18:
        cursor_label = "Centre / Équilibre thématique"
    elif computed_cursor > -45:
        cursor_label = "Centre-gauche / Social"
    else:
        cursor_label = "Gauche / Progressiste"

    theme_color_map = {str(t["name"]): str(t["color"]) for t in THEME_DEFINITIONS}
    theme_orient_map = {str(t["name"]): str(t["orientation"]) for t in THEME_DEFINITIONS}

    themes_breakdown = [
        {
            "theme": tname,
            "unique_banners": theme_counts[tname],
            "weighted_share_pct": round(100 * w / total_weight, 1) if total_weight > 0 else 0.0,
            "color": theme_color_map.get(tname, "#64748b"),
            "orientation": theme_orient_map.get(tname, "neutre"),
        }
        for tname, w in theme_weighted.most_common()
    ]

    parties_summary = []
    for p in PARTY_DEFINITIONS:
        pid = str(p["id"])
        st = party_stats[pid]
        hours_dict = dict(st["hours"])  # type: ignore[arg-type]
        parties_summary.append(
            {
                "party_id": pid,
                "party_name": st["party_name"],
                "party_short": st["party_short"],
                "color": st["color"],
                "total_mentions": st["total_mentions"],
                "weighted_occurrences": st["weighted_occurrences"],
                "critique_count": st["critique_count"],
                "neutre_count": st["neutre_count"],
                "tribune_count": st["tribune_count"],
                "active_hours": [f"{h} ({cnt})" for h, cnt in sorted(hours_dict.items())],
                "excerpts": st["excerpts"],
            }
        )

    hourly_party_list = [
        {
            "hour": f"{h:02d}h",
            **hourly_party_matrix[f"{h:02d}h"],
            "critiques": hourly_critiques_matrix[f"{h:02d}h"],
        }
        for h in range(24)
    ]

    return {
        "political_cursor": {
            "score": computed_cursor,
            "label": cursor_label,
            "right_themes_pct": right_pct,
            "neutral_themes_pct": neutral_pct,
            "left_themes_pct": left_pct,
            "methodology": (
                f"Indice calculé de manière impartiale sur {len(records)} bandeaux distincts "
                f"({total_weight} échantillons pondérés par leur durée à l'antenne) : "
                f"{right_pct}% portent sur des thématiques à forte saillance à droite (identité, religion, "
                f"sécurité, fiscalité), {neutral_pct}% sur l'actualité internationale ou institutionnelle factuelle, "
                f"et {left_pct}% sur des thématiques sociales ou de services publics (grèves, salaires, universités)."
            ),
        },
        "themes_breakdown": themes_breakdown,
        "parties_study": parties_summary,
        "hourly_party_activity": hourly_party_list,
        "political_timeline": political_timeline,
        "banners_enrichment": enriched_banners_meta,
    }


def compute_deterministic_stats(records: list[BannerRecord], metrics: dict) -> dict:
    """Agrège les statistiques thématiques, politiques et horaires à partir de l'historique."""
    by_hour: Counter[str] = Counter()
    by_day: dict[str, int] = defaultdict(int)
    subject_count = 0
    ticker_count = 0

    for rec in records:
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

    deep = compute_political_and_thematic_analysis(records)

    return {
        "total_unique_banners": len(records),
        "subject_banners_count": subject_count,
        "ticker_banners_count": ticker_count,
        "capture_metrics": metrics,
        "political_cursor": deep["political_cursor"],
        "themes_breakdown": deep["themes_breakdown"],
        "parties_study": deep["parties_study"],
        "hourly_party_activity": deep["hourly_party_activity"],
        "political_timeline": deep["political_timeline"],
        "hourly_distribution": [
            {"hour": f"{h:02d}h", "count": by_hour.get(f"{h:02d}h", 0)}
            for h in range(24)
        ],
        "daily_counts": [
            {"date": d, "count": by_day[d]}
            for d in sorted(by_day.keys())[-30:]
        ],
        "top_keywords": extract_top_keywords(records, limit=12),
        "_banners_enrichment": deep["banners_enrichment"],
    }


def _build_gemini_prompt(target_label: str, recent_records: list[BannerRecord]) -> str:
    lines: list[str] = []
    for idx, rec in enumerate(recent_records[-100:], start=1):
        heure = rec.first_seen[11:19] if len(rec.first_seen) >= 19 else rec.first_seen[11:16]
        parts = []
        if rec.subject_surtitre:
            parts.append(f"Intervenant/Surtitre: {rec.subject_surtitre}")
        if rec.subject_title:
            parts.append(f"Titre débat: {rec.subject_title}")
        if rec.ticker_text:
            parts.append(f"Fil info: {rec.ticker_text}")
        lines.append(f"{idx}. ({heure}, durée={rec.sample_count}) " + " | ".join(parts))

    joined = "\n".join(lines)
    return (
        f"Tu es un politologue et analyste des médias rigoureusement impartial. "
        f"Voici {len(lines)} bandeaux TV capturés sur CNews ({target_label}, hors publicités).\n"
        "Analyse de façon méthodique, factuelle et neutre :\n"
        "1. Le positionnement sur l'axe Gauche (-100) ↔ Centre/Neutre (0) ↔ Droite (+100) selon le choix des thèmes (agenda-setting) et la formulation des titres (cadrage).\n"
        "2. Le traitement des partis politiques, avec une étude spécifique des critiques ou mentions visant La France Insoumise (LFI / Jean-Luc Mélenchon), la Gauche, le Gouvernement et la Droite/RN, en précisant à quelle heure chaque camp est abordé.\n"
        "Réponds UNIQUEMENT en JSON valide sans texte ni markdown autour, avec cette structure exacte :\n"
        "{\n"
        '  "score_axe_politique": 60,\n'
        '  "label_positionnement": "Droite conservatrice & identitaire",\n'
        '  "justification_impartiale": "Explication factuelle et neutre en 2-3 phrases justifiant la position du curseur par la proportion des thèmes (identité, sécurité, fiscalité vs social) et le choix des intervenants.",\n'
        '  "etude_lfi_et_partis": "Analyse précise et impartiale du traitement de La France Insoumise (LFI) et des autres partis (tonalité critique, ironique, factuelle ou tribune, et heures de passage).",\n'
        '  "resume_journee": "Synthèse factuelle en 2 à 3 phrases des grands thèmes traités à l\'antenne.",\n'
        '  "themes_principaux": [\n'
        '    {"theme": "Nom du thème", "occurrences": 4, "orientation": "droite", "mots_cles": ["mot1", "mot2"]}\n'
        "  ],\n"
        '  "sujets_marquants": ["Fait ou bandeau marquant 1", "Fait ou bandeau marquant 2"],\n'
        '  "observation_editoriale": "Observation méthodologique neutre sur la hiérarchie de l\'information."\n'
        "}\n\n"
        f"Bandeaux capturés :\n{joined}"
    )


def _extract_json_object(raw_text: str) -> dict:
    """Extrait l'objet JSON même si le modèle entoure la réponse de blocs markdown."""
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
    """Appelle l'API Gemini gratuite avec repli multi-modèles (cycle de 5 minutes)."""
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
                "temperature": 0.15,
                "responseMimeType": "application/json",
            },
        }
        try:
            resp = requests.post(url, json=payload, timeout=28)
            if resp.status_code in (404, 429, 503):
                logger.warning("Modèle %s indisponible (HTTP %s), essai suivant...", model_name, resp.status_code)
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
            logger.warning("Échec appel Gemini (%s): %s", model_name, exc)
    return None


def should_trigger_periodic_gemini(
    existing_analyses: list[dict],
    records: list[BannerRecord],
    interval_s: int,
    tz_name: str,
) -> bool:
    """Vérifie s'il faut lancer une nouvelle analyse Gemini (toutes les 5 min si nouveau contenu)."""
    if not records:
        return False
    if not existing_analyses:
        return True

    latest = existing_analyses[0]
    last_gen_str = latest.get("generated_at")
    last_banner_id = latest.get("last_banner_id")

    if (
        last_banner_id == records[-1].id
        and latest.get("banners_analyzed") == len(records)
        and "score_axe_politique" in latest
    ):
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
    """Met à jour docs/data/stats.json et docs/data/banners.json avec enrichissement politique/thématique et IA."""
    records = store.load_records()
    metrics = store.load_metrics()
    stats = compute_deterministic_stats(records, metrics)
    enrichment_map = stats.pop("_banners_enrichment", {})

    # Enrichit docs/data/banners.json avec le thème et les partis détectés pour chaque bandeau
    docs_banners = []
    for r in reversed(records[-500:]):
        d = asdict(r)
        meta = enrichment_map.get(r.id, {})
        d["primary_theme"] = meta.get("primary_theme", "Actualité générale")
        d["orientation"] = meta.get("orientation", "neutre")
        d["axis_score"] = meta.get("axis_score", 0)
        d["parties"] = meta.get("parties", [])
        docs_banners.append(d)

    docs_data_dir.mkdir(parents=True, exist_ok=True)
    (docs_data_dir / "banners.json").write_text(
        json.dumps(docs_banners, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

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

    # Si Gemini a fourni un score d'axe politique récent, on réalise une moyenne pondérée (50% mesure objective / 50% IA)
    if existing_analyses and isinstance(existing_analyses[0].get("score_axe_politique"), (int, float)):
        ai_score = int(existing_analyses[0]["score_axe_politique"])
        det_score = int(stats["political_cursor"]["score"])
        blended = max(-100, min(100, round(0.5 * det_score + 0.5 * ai_score)))
        stats["political_cursor"]["score"] = blended
        stats["political_cursor"]["deterministic_score"] = det_score
        stats["political_cursor"]["ai_score"] = ai_score
        if existing_analyses[0].get("label_positionnement"):
            stats["political_cursor"]["label"] = existing_analyses[0]["label_positionnement"]
        if existing_analyses[0].get("justification_impartiale"):
            stats["political_cursor"]["ai_justification"] = existing_analyses[0]["justification_impartiale"]
        if existing_analyses[0].get("etude_lfi_et_partis"):
            stats["political_cursor"]["ai_parties_study"] = existing_analyses[0]["etude_lfi_et_partis"]

    stats["daily_ai_analyses"] = existing_analyses
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    _sync_docs_to_hf_space(
        docs_data_dir=docs_data_dir,
        settings=store.settings,
        force=trigger_now,
    )
    return stats


def _sync_docs_to_hf_space(docs_data_dir: Path, settings: Settings, force: bool = False) -> bool:
    """Publie l'interface et les données (index.html, styles.css, app.js, data/*.json) sur le Space Hugging Face."""
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
        docs_root = docs_data_dir.parent
        ops = [
            CommitOperationAdd(
                path_in_repo="data/banners.json",
                path_or_fileobj=banners_file.read_bytes(),
            ),
            CommitOperationAdd(
                path_in_repo="data/stats.json",
                path_or_fileobj=stats_file.read_bytes(),
            ),
        ]
        for asset in ("index.html", "styles.css", "app.js"):
            asset_path = docs_root / asset
            if asset_path.exists():
                ops.append(
                    CommitOperationAdd(
                        path_in_repo=asset,
                        path_or_fileobj=asset_path.read_bytes(),
                    )
                )

        api.create_commit(
            repo_id=settings.hf_space_repo,
            repo_type="space",
            operations=ops,
            commit_message="chore: sync live CNews political & thematic analysis",
        )
        _LAST_HF_PUSH_MONOTONIC = now_mono
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Échec synchro Hugging Face Space: %s", exc)
        return False
