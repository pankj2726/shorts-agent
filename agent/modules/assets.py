"""
assets.py — Stock footage and image fetcher (Pexels + Pixabay).

License log: every asset is recorded in the DB assets table.
"""
import logging
import os
import random
from pathlib import Path
from typing import Optional

import requests

from agent.modules import db

logger = logging.getLogger(__name__)

PEXELS_BASE = "https://api.pexels.com/videos"
PIXABAY_BASE = "https://pixabay.com/api/videos"
PIXABAY_IMAGE_BASE = "https://pixabay.com/api"

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "shorts-agent/1.0"})


def fetch_stock_clips(
    keywords: list[str],
    output_dir: Path,
    video_id: int,
    orientation: str = "portrait",
    per_keyword: int = 2,
) -> list[dict]:
    """
    Download stock video clips for the given scene keywords.
    Tries Pexels first, then Pixabay.

    Returns a list of asset dicts with local paths.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    assets = []

    for kw in keywords[:8]:  # cap at 8 scenes
        clip = _fetch_pexels_video(kw, output_dir, video_id) or \
               _fetch_pixabay_video(kw, output_dir, video_id)
        if clip:
            assets.append(clip)
            _log_asset(video_id, clip)
        else:
            logger.warning("assets: no clip found for keyword '%s'", kw)

    return assets


def _fetch_pexels_video(keyword: str, output_dir: Path, video_id: int) -> Optional[dict]:
    api_key = os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        return None
    try:
        resp = _SESSION.get(
            f"{PEXELS_BASE}/search",
            headers={"Authorization": api_key},
            params={"query": keyword, "orientation": "portrait", "per_page": 5, "size": "medium"},
            timeout=15,
        )
        resp.raise_for_status()
        videos = resp.json().get("videos", [])
        if not videos:
            return None
        video = random.choice(videos[:5])
        # Pick the smallest file that is at least 540p portrait
        files = sorted(video.get("video_files", []), key=lambda f: f.get("width", 0))
        file_info = next(
            (f for f in files if f.get("height", 0) >= 540 and f.get("width", 0) <= 1080),
            files[0] if files else None,
        )
        if not file_info:
            return None
        url = file_info["link"]
        fname = output_dir / f"pexels_{video['id']}_{keyword[:20].replace(' ', '_')}.mp4"
        _download(url, fname)
        return {
            "asset_type": "clip",
            "source_url": f"https://www.pexels.com/video/{video['id']}/",
            "license": "Pexels License (free for commercial use, no attribution required)",
            "local_path": str(fname),
            "keyword": keyword,
        }
    except Exception as e:
        logger.warning("assets: Pexels failed for '%s': %s", keyword, e)
        return None


def _fetch_pixabay_video(keyword: str, output_dir: Path, video_id: int) -> Optional[dict]:
    api_key = os.environ.get("PIXABAY_API_KEY", "")
    if not api_key:
        return None
    try:
        resp = _SESSION.get(
            PIXABAY_BASE + "/",
            params={
                "key": api_key,
                "q": keyword,
                "video_type": "film",
                "orientation": "vertical",
                "per_page": 5,
            },
            timeout=15,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        if not hits:
            return None
        hit = random.choice(hits[:5])
        # Prefer 'medium' or 'small' size for speed
        videos = hit.get("videos", {})
        file_info = videos.get("medium") or videos.get("small") or videos.get("large")
        if not file_info:
            return None
        url = file_info["url"]
        fname = output_dir / f"pixabay_{hit['id']}_{keyword[:20].replace(' ', '_')}.mp4"
        _download(url, fname)
        return {
            "asset_type": "clip",
            "source_url": f"https://pixabay.com/videos/id-{hit['id']}/",
            "license": "Pixabay Content License (free for commercial use, no attribution required)",
            "local_path": str(fname),
            "keyword": keyword,
        }
    except Exception as e:
        logger.warning("assets: Pixabay failed for '%s': %s", keyword, e)
        return None


def _download(url: str, dest: Path) -> None:
    if dest.exists():
        return  # idempotent
    with _SESSION.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
    logger.info("assets: downloaded %s", dest.name)


def _log_asset(video_id: int, asset: dict) -> None:
    conn = db.get_conn()
    conn.execute(
        """INSERT INTO assets (video_id, asset_type, source_url, license, local_path)
           VALUES (?, ?, ?, ?, ?)""",
        (video_id, asset["asset_type"], asset["source_url"], asset["license"], asset["local_path"]),
    )
    conn.commit()
    conn.close()


def pick_music(output_dir: Path | None = None) -> Optional[dict]:
    """
    Return a random CC0 music track from the local assets/music/ pool.
    Music must be downloaded manually — no API available for YouTube Audio Library.
    """
    music_dir = Path(__file__).parent.parent.parent / "assets" / "music"
    tracks = list(music_dir.glob("*.mp3")) + list(music_dir.glob("*.wav"))
    if not tracks:
        logger.warning("assets: no music tracks in assets/music/ — add CC0 tracks manually")
        return None
    track = random.choice(tracks)
    return {
        "asset_type": "music",
        "local_path": str(track),
        "license": "CC0 / royalty-free (verify per file in assets/music/LICENSE.md)",
        "source_url": "",
    }