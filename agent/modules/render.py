"""
render.py — Video assembly: stock clips + TTS audio + kinetic captions + music.

Output: 1080x1920 MP4, ≤60s, −14 LUFS, captions burned in.
Requires: ffmpeg (system), moviepy, pillow.
"""
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from agent.modules import db, qa
from agent.modules.notify import notify

logger = logging.getLogger(__name__)

TARGET_W, TARGET_H = 1080, 1920
TARGET_LUFS = -14.0
FONT_PATH = str(Path(__file__).parent.parent.parent / "assets" / "fonts" / "NotoSans-Bold.ttf")


def render_short(
    video_id: int,
    script_data: dict,
    tts_data: dict,
    clips: list[dict],
    music: Optional[dict],
    output_dir: Path,
) -> dict:
    """
    Render the final Short.

    Returns render_info dict for QA Gate #2.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"video_{video_id}_final.mp4"

    if out_path.exists():
        logger.info("render: %s already exists, skipping re-render (idempotent)", out_path)
        return _make_render_info(out_path, video_id, tts_data, script_data)

    notify("RENDER_START", f"Assembling video_id={video_id}", level="info")

    # Step 1: Prepare clip list (pad/trim to fill duration)
    audio_path = tts_data["audio_path"]
    duration_s = tts_data["duration_s"]
    if duration_s < 1:
        duration_s = 30.0  # fallback

    clip_paths = [c["local_path"] for c in clips if Path(c["local_path"]).exists()]
    if not clip_paths:
        raise RuntimeError("render: no valid clips to render with")

    # Step 2: Build a concat list covering the full audio duration
    concat_file = output_dir / f"video_{video_id}_concat.txt"
    _write_concat(clip_paths, duration_s, concat_file)

    # Step 3: Assemble video + audio
    raw_video = output_dir / f"video_{video_id}_raw.mp4"
    _ffmpeg_assemble(concat_file, audio_path, raw_video, duration_s)

    # Step 4: Normalize loudness to −14 LUFS
    normalized = output_dir / f"video_{video_id}_norm.mp4"
    _ffmpeg_normalize(raw_video, music, normalized)

    # Step 5: Burn captions
    vtt_path = tts_data.get("vtt_path")
    if vtt_path and Path(vtt_path).exists():
        _ffmpeg_burn_captions(normalized, vtt_path, out_path)
    else:
        # No VTT — just copy
        normalized.rename(out_path)

    # Step 6: Clean up intermediates
    for tmp in [raw_video, normalized, concat_file]:
        try:
            Path(tmp).unlink(missing_ok=True)
        except Exception:
            pass

    render_info = _make_render_info(out_path, video_id, tts_data, script_data)
    logger.info("render: done: %s (duration=%.1fs)", out_path, render_info["duration_s"])
    notify(
        "RENDER_DONE",
        f"🎬 video_id={video_id}\nDuration: {render_info['duration_s']:.1f}s\n"
        f"Loudness: {render_info.get('loudness_lufs', '?')} LUFS",
        level="action",
        video_path=str(out_path),
    )
    return render_info


def _write_concat(clip_paths: list[str], duration_s: float, out_file: Path) -> None:
    """Write an ffmpeg concat demuxer file, looping clips to cover duration_s."""
    lines = []
    total = 0.0
    while total < duration_s + 2:
        for cp in clip_paths:
            lines.append(f"file '{cp}'\n")
            total += 5  # rough estimate per clip segment
            if total >= duration_s + 2:
                break
    out_file.write_text("".join(lines), encoding="utf-8")


def _ffmpeg_assemble(concat_file: Path, audio_path: str, out: Path, duration_s: float) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-t", str(duration_s + 0.5),
        "-vf", f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
               f"crop={TARGET_W}:{TARGET_H},setsar=1",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(out),
    ]
    _run_ffmpeg(cmd)


def _ffmpeg_normalize(video: Path, music: Optional[dict], out: Path) -> None:
    """Mix optional background music and normalize to −14 LUFS."""
    if music and music.get("local_path") and Path(music["local_path"]).exists():
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video),
            "-i", music["local_path"],
            "-filter_complex",
            f"[1:a]volume=0.08[bg];[0:a][bg]amix=inputs=2:duration=first[amix];"
            f"[amix]loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11[a_out]",
            "-map", "0:v", "-map", "[a_out]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            str(out),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", str(video),
            "-filter_complex", f"[0:a]loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11[a_out]",
            "-map", "0:v", "-map", "[a_out]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            str(out),
        ]
    _run_ffmpeg(cmd)


def _ffmpeg_burn_captions(video: Path, vtt_path: str, out: Path) -> None:
    """Burn subtitles from VTT into the video using ffmpeg subtitles filter."""
    # Build font style: large, bold, contrasting
    font_arg = f"FontName=Noto Sans,FontSize=18,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,MarginV=80"
    if Path(FONT_PATH).exists():
        font_arg = f"FontFile={FONT_PATH}," + font_arg
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-vf", f"subtitles={vtt_path}:force_style='{font_arg}'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        str(out),
    ]
    _run_ffmpeg(cmd)


def _run_ffmpeg(cmd: list[str]) -> None:
    logger.debug("render: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-2000:]}")


def _make_render_info(out_path: Path, video_id: int, tts_data: dict, script_data: dict) -> dict:
    """Probe the output file and build the render_info dict for QA Gate #2."""
    duration_s = _probe_duration(out_path)
    loudness = _probe_loudness(out_path)
    vtt_exists = bool(tts_data.get("vtt_path") and Path(tts_data["vtt_path"]).exists())

    # Check asset license completeness
    conn = db.get_conn()
    missing = conn.execute(
        "SELECT COUNT(*) FROM assets WHERE video_id=? AND license IS NULL", (video_id,)
    ).fetchone()[0]
    conn.close()

    return {
        "video_path": str(out_path),
        "width": TARGET_W,
        "height": TARGET_H,
        "duration_s": duration_s,
        "loudness_lufs": loudness,
        "has_captions": vtt_exists,
        "asset_licenses_complete": missing == 0,
    }


def _probe_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        return float(json.loads(result.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def _probe_loudness(path: Path) -> Optional[float]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", str(path), "-filter:a", "loudnorm=print_format=json", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
        import re
        m = re.search(r'"input_i"\s*:\s*"([\-\d.]+)"', result.stderr)
        return float(m.group(1)) if m else None
    except Exception:
        return None