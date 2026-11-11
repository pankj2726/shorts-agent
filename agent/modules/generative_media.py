"""
generative_media.py — Free Generative AI Scene Synthesis & Video Motion Engine.

Provides the Video Agent with free generative model privileges:
  1. Pollinations AI Flux (1080×1920 vertical stills, 100% free, zero API key).
  2. FFmpeg Cinematic Camera Motion: Converts static vertical frames into
     fluid, professional documentary camera pushes / pans (zoompan filter).
"""

from __future__ import annotations

import logging
import os
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def generate_scene_image(
    prompt: str,
    output_path: Path | str,
    width: int = 1080,
    height: int = 1920,
    model: str = "flux",
    timeout: int = 45,
) -> Path:
    """
    Synthesizes a vertical 9:16 cinematic scene using Pollinations AI's free Flux tier.
    Zero API key required; 100% free.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Encode prompt safely
    encoded_prompt = urllib.parse.quote_plus(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&model={model}&nologo=true"

    logger.info(f"generative_media: Generating {width}x{height} frame with {model}...")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ShortsForge-GenerativeMedia/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read()
        if len(content) < 1000:
            raise ValueError(f"Received invalid or truncated image payload ({len(content)} bytes)")
        with open(out, "wb") as f:
            f.write(content)

    logger.info(f"generative_media: Successfully saved frame to {out} ({len(content)} bytes)")
    return out


def create_cinematic_motion_clip(
    image_path: Path | str,
    output_mp4: Path | str,
    duration: float = 5.0,
    fps: int = 30,
    zoom_speed: float = 0.0012,
) -> Path:
    """
    Applies FFmpeg cinematic slow push-in zoom to a 1080×1920 frame,
    producing a broadcast-ready MP4 clip without relying on external stock videos.
    """
    img = Path(image_path)
    out = Path(output_mp4)
    out.parent.mkdir(parents=True, exist_ok=True)

    if not img.exists():
        raise FileNotFoundError(f"Input image does not exist: {img}")

    frames = int(duration * fps)
    # Slow, steady cinematic camera push-in
    filter_complex = (
        f"zoompan=z='min(zoom+{zoom_speed},1.20)':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s=1080x1920:fps={fps},"
        f"format=yuv420p"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-i", str(img),
        "-vf", filter_complex,
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        str(out),
    ]

    logger.info(f"generative_media: Rendering {duration}s cinematic motion clip...")
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if res.returncode != 0:
        logger.error(f"FFmpeg motion clip error: {res.stderr}")
        raise RuntimeError(f"FFmpeg failed with code {res.returncode}")

    logger.info(f"generative_media: Motion clip rendered successfully -> {out}")
    return out
