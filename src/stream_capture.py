"""Resolution du flux officiel CNews (Dailymotion) et extraction d'images HLS."""

from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import urljoin

import cv2
import numpy as np
import requests
import yt_dlp

logger = logging.getLogger(__name__)

DAILYMOTION_CNEWS_LIVE_API = (
    "https://api.dailymotion.com/user/CNEWS/videos"
    "?fields=id,title,mode,onair,url&flags=live_onair&limit=5"
)


@dataclass(frozen=True)
class CapturedFrame:
    """Image capturee depuis le flux avec horodatage."""

    frame: np.ndarray
    captured_at: str
    source_id: str
    segment_name: str


def discover_cnews_live_ids(preferred_id: str = "x3b68jn") -> list[str]:
    """Recupere la liste des flux directs CNews actifs via l'API publique Dailymotion."""
    candidates: list[str] = []
    if preferred_id:
        candidates.append(preferred_id)
    try:
        resp = requests.get(DAILYMOTION_CNEWS_LIVE_API, timeout=8)
        resp.raise_for_status()
        items = resp.json().get("list", [])
        # Priorise le flux principal ("Live CNEWS") devant Backup ou Prime.
        sorted_items = sorted(
            items,
            key=lambda item: (
                0 if item.get("title", "").strip().lower() == "live cnews" else 1,
                0 if "backup" not in item.get("title", "").lower() else 2,
            ),
        )
        for item in sorted_items:
            vid = item.get("id")
            if vid and vid not in candidates:
                candidates.append(vid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Decouverte API Dailymotion indisponible (%s), utilisation du repli.", exc)

    for fallback in ("x3b68jn", "x5idxxp"):
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates


def resolve_hls_url(dailymotion_id: str) -> str:
    """Resout l'URL HLS (m3u8) <= 720p video seule via yt-dlp."""
    video_url = (
        dailymotion_id
        if dailymotion_id.startswith("http")
        else f"https://www.dailymotion.com/video/{dailymotion_id}"
    )
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestvideo[height<=720]/best[height<=720]/best",
        "geo_bypass": True,
        "geo_bypass_country": "FR",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        if not info or not info.get("url"):
            raise RuntimeError(f"Impossible d'obtenir le flux HLS pour {video_url}")
        return str(info["url"])


def resolve_active_cnews_stream(preferred_id: str = "x3b68jn") -> tuple[str, str]:
    """Tente les IDs directs CNews jusqu'a obtenir une URL HLS exploitable."""
    errors: list[str] = []
    for vid in discover_cnews_live_ids(preferred_id):
        try:
            hls_url = resolve_hls_url(vid)
            return vid, hls_url
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{vid}: {exc}")
    raise RuntimeError(
        "Aucun flux direct CNews accessible (verifier blocage geographique / IP datacenter): "
        + " | ".join(errors)
    )


def _parse_hls_playlist(playlist_text: str) -> tuple[str | None, list[tuple[str, str | None]]]:
    """Extrait l'init map MP4 et les segments (uri, program_date_time)."""
    init_uri: str | None = None
    segments: list[tuple[str, str | None]] = []
    pending_pdt: str | None = None

    for raw_line in playlist_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MAP:"):
            match = re.search(r'URI="([^"]+)"', line)
            if match:
                init_uri = match.group(1)
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            pending_pdt = line.split(":", 1)[1].strip()
        elif not line.startswith("#"):
            segments.append((line, pending_pdt))
            pending_pdt = None
    return init_uri, segments


def _decode_segment_first_frame(init_bytes: bytes, seg_bytes: bytes) -> np.ndarray | None:
    """Lit la premiere frame d'un segment fMP4/TS via OpenCV."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(init_bytes)
        tmp.write(seg_bytes)
        tmp_path = tmp.name
    try:
        cap = cv2.VideoCapture(tmp_path)
        ok, frame = cap.read()
        cap.release()
        return frame if ok and frame is not None else None
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def fetch_latest_hls_frames(
    preferred_id: str = "x3b68jn",
    max_segments: int = 3,
) -> list[CapturedFrame]:
    """Recupere les dernieres frames en direct (une par segment HLS recent)."""
    vid, hls_url = resolve_active_cnews_stream(preferred_id)
    resp = requests.get(hls_url, timeout=10)
    resp.raise_for_status()
    init_uri, segments = _parse_hls_playlist(resp.text)
    if not segments:
        raise RuntimeError("Playlist HLS vide.")

    init_bytes = b""
    if init_uri:
        init_resp = requests.get(urljoin(hls_url, init_uri), timeout=10)
        init_resp.raise_for_status()
        init_bytes = init_resp.content

    frames: list[CapturedFrame] = []
    for seg_uri, pdt in segments[-max_segments:]:
        seg_resp = requests.get(urljoin(hls_url, seg_uri), timeout=10)
        seg_resp.raise_for_status()
        frame = _decode_segment_first_frame(init_bytes, seg_resp.content)
        if frame is not None:
            timestamp = pdt or datetime.now(timezone.utc).isoformat()
            frames.append(
                CapturedFrame(
                    frame=frame,
                    captured_at=timestamp,
                    source_id=vid,
                    segment_name=seg_uri,
                )
            )
    return frames


def iter_live_frames(
    preferred_id: str = "x3b68jn",
    poll_interval_s: float = 4.0,
    duration_s: float | None = None,
) -> Iterator[CapturedFrame]:
    """Surveille le flux HLS en continu en ne telechargeant que les nouveaux segments."""
    start = time.monotonic()
    seen_segments: set[str] = set()
    vid, hls_url = resolve_active_cnews_stream(preferred_id)
    init_bytes = b""

    while duration_s is None or (time.monotonic() - start) < duration_s:
        try:
            resp = requests.get(hls_url, timeout=10)
            if resp.status_code in (403, 410):
                vid, hls_url = resolve_active_cnews_stream(preferred_id)
                init_bytes = b""
                resp = requests.get(hls_url, timeout=10)
            resp.raise_for_status()

            init_uri, segments = _parse_hls_playlist(resp.text)
            if init_uri and not init_bytes:
                init_resp = requests.get(urljoin(hls_url, init_uri), timeout=10)
                init_resp.raise_for_status()
                init_bytes = init_resp.content

            for seg_uri, pdt in segments[-2:]:
                if seg_uri in seen_segments:
                    continue
                seen_segments.add(seg_uri)
                if len(seen_segments) > 200:
                    seen_segments = set(list(seen_segments)[-100:])

                seg_resp = requests.get(urljoin(hls_url, seg_uri), timeout=10)
                seg_resp.raise_for_status()
                frame = _decode_segment_first_frame(init_bytes, seg_resp.content)
                if frame is not None:
                    yield CapturedFrame(
                        frame=frame,
                        captured_at=pdt or datetime.now(timezone.utc).isoformat(),
                        source_id=vid,
                        segment_name=seg_uri,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Erreur lecture flux (%s), nouvelle tentative...", exc)

        time.sleep(poll_interval_s)
