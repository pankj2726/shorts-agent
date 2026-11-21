"""
assets.py — Stock footage and image fetcher (Pexels + Pixabay).

License log: every asset is recorded in the DB assets table.
"""
import logging
import os
import random
import time
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
        clip = (
            _fetch_pexels_video(kw, output_dir, video_id)
            or _fetch_pixabay_video(kw, output_dir, video_id)
            or _fetch_pollinations_motion_clip(kw, output_dir, video_id)
            or _generate_procedural_clip(kw, output_dir, video_id)
        )
        if clip:
            assets.append(clip)
            _log_asset(video_id, clip)
        else:
            logger.warning("assets: no clip found for keyword '%s'", kw)

    return assets


def _fetch_pollinations_motion_clip(keyword: str, output_dir: Path, video_id: int) -> Optional[dict]:
    """Generate an AI scene image via Pollinations.ai (no key needed) and convert to a 3s 1080x1920 motion MP4."""
    try:
        import subprocess
        import urllib.parse
        clean_kw = keyword.strip()
        safe_name = "".join(c if c.isalnum() else "_" for c in clean_kw[:15]).strip("_") or "scene"
        encoded = urllib.parse.quote(f"cinematic vertical photography, {clean_kw}, high quality, 9:16 portrait")
        img_url = f"https://image.pollinations.ai/prompt/{encoded}?width=1080&height=1920&nologo=true"
        img_path = output_dir / f"scene_{video_id}_{safe_name}.jpg"
        clip_path = output_dir / f"motion_{video_id}_{safe_name}.mp4"

        _download(img_url, img_path)
        if not img_path.exists() or img_path.stat().st_size < 1000:
            return None

        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(img_path),
            "-vf", "zoompan=z='min(zoom+0.0015,1.15)':d=90:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920",
            "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(clip_path)
        ]
        subprocess.run(cmd, capture_output=True, timeout=30)
        if clip_path.exists():
            return {
                "asset_type": "generated_image_motion",
                "source_url": img_url,
                "license": "CC0 / Free Public Domain (Pollinations.ai)",
                "local_path": str(clip_path),
                "keyword": keyword,
            }
    except Exception as e:
        logger.warning("assets: Pollinations motion clip failed for '%s': %s", keyword, e)
    return None


def _generate_procedural_clip(keyword: str, output_dir: Path, video_id: int) -> dict:
    """Fallback solid/gradient background clip via ffmpeg."""
    import subprocess
    safe_name = "".join(c if c.isalnum() else "_" for c in keyword[:15]).strip("_") or "scene"
    clip_path = output_dir / f"proc_{video_id}_{safe_name}.mp4"
    colors = ["#1e1e2e", "#0f172a", "#18181b", "#1a1a2e", "#16213e"]
    color = random.choice(colors)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c={color}:s=1080x1920:d=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(clip_path)
    ]
    subprocess.run(cmd, capture_output=True, timeout=15)
    return {
        "asset_type": "procedural_background",
        "source_url": "local:ffmpeg_color",
        "license": "Procedural Generated",
        "local_path": str(clip_path),
        "keyword": keyword,
    }


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


MAX_CLIP_BYTES_DEFAULT = 120 * 1024 * 1024
"""Default per-clip download cap (120 MiB). Env: SHORTS_MAX_CLIP_BYTES."""

DOWNLOAD_DEADLINE_S_DEFAULT = 120.0
"""Default overall transfer deadline in seconds. Env: SHORTS_DOWNLOAD_DEADLINE_S."""


def _positive_setting(env_name: str, default: float, cast) -> float:
    """
    Read a numeric setting, refusing to silently disable a protection.

    An unset variable yields the default. A present but invalid, zero or
    negative value raises ValueError — falling back to the default there would
    quietly ignore an operator's typo, and treating 0 as "unlimited" would turn
    a misconfiguration into the exact unbounded download this guards against.
    """
    raw = os.environ.get(env_name)
    if raw is None or not raw.strip():
        return default
    try:
        value = cast(raw.strip())
    except (TypeError, ValueError):
        raise ValueError(
            f"{env_name}={raw!r} is not a valid number; refusing to run with an "
            "unbounded download"
        ) from None
    if value <= 0:
        raise ValueError(
            f"{env_name}={raw!r} must be greater than zero; 0 or negative would "
            "disable the limit"
        )
    return value


def _download(url: str, dest: Path) -> None:
    """
    Download one clip to `dest`, atomically and within bounded time and size.

    Three independent limits, because they catch different failures:

    * the socket read timeout, which catches a fully stalled connection;
    * an overall monotonic deadline, which catches a slow-but-alive stream that
      keeps dribbling chunks and so never trips the read timeout;
    * a byte cap, enforced against both Content-Length and the bytes actually
      received, since the header is advisory and may be absent or wrong.

    The body is written to a temporary file in the destination directory and
    renamed only after a complete transfer, so a failure never leaves a
    truncated file at the final path. That matters more than it looks: the
    idempotency check above returns early when `dest` exists, so a partial file
    left behind would be treated as a valid clip on every later run and
    rendered into a video.

    Raises on any failure. Callers rely on the exception to avoid recording the
    clip as usable; an aborted download must never look like a successful one.
    """
    if dest.exists():
        return  # idempotent

    max_bytes = int(_positive_setting(
        "SHORTS_MAX_CLIP_BYTES", MAX_CLIP_BYTES_DEFAULT, float))
    deadline_s = _positive_setting(
        "SHORTS_DOWNLOAD_DEADLINE_S", DOWNLOAD_DEADLINE_S_DEFAULT, float)

    tmp = dest.with_name(f"{dest.name}.part")
    started = time.monotonic()
    written = 0

    try:
        with _SESSION.get(url, stream=True, timeout=60) as resp:
            resp.raise_for_status()

            # Reject early when the server declares an oversize body, but do not
            # trust the header alone — it is checked again against real bytes.
            declared = resp.headers.get("Content-Length")
            if declared:
                try:
                    if int(declared) > max_bytes:
                        raise RuntimeError(
                            f"assets: {dest.name} declared Content-Length "
                            f"{declared} exceeds limit {max_bytes}"
                        )
                except ValueError:
                    logger.warning(
                        "assets: unparseable Content-Length %r for %s; relying "
                        "on streamed byte count", declared, dest.name,
                    )

            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    # Checked before accepting the chunk, so the cap is never
                    # exceeded by more than one chunk and the deadline is never
                    # overshot by a full read.
                    if time.monotonic() - started > deadline_s:
                        raise RuntimeError(
                            f"assets: {dest.name} exceeded the "
                            f"{deadline_s:.0f}s download deadline after "
                            f"{written} bytes"
                        )
                    written += len(chunk)
                    if written > max_bytes:
                        raise RuntimeError(
                            f"assets: {dest.name} exceeded the {max_bytes} byte "
                            f"limit after {written} bytes"
                        )
                    f.write(chunk)

        os.replace(tmp, dest)
    except BaseException:
        # Every failure path removes the partial file. dest is deliberately not
        # touched: if a valid clip is already there, a failed refresh must not
        # delete it.
        try:
            tmp.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("assets: could not remove partial %s: %s", tmp.name, exc)
        raise

    logger.info(
        "assets: downloaded %s (%d bytes in %.1fs)",
        dest.name, written, time.monotonic() - started,
    )


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