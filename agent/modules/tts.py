"""
tts.py — Text-to-speech voiceover with word-level timestamps for captions.

Primary: edge-tts (unofficial Microsoft neural TTS, many EN/HI voices, free, no key).
Fallback: pyttsx3 (local, offline, MIT-safe) — install separately if edge-tts breaks.

Edge-tts voices:
  English: en-IN-NeerjaNeural, en-IN-PrabhatNeural, en-US-JennyNeural, en-US-GuyNeural
  Hindi:   hi-IN-SwaraNeural, hi-IN-MadhurNeural
"""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VOICE_MAP = {
    "en": os.environ.get("TTS_VOICE_EN", "en-IN-NeerjaNeural"),
    "hi": os.environ.get("TTS_VOICE_HI", "hi-IN-SwaraNeural"),
}


async def _edge_tts_generate(text: str, voice: str, out_audio: Path, out_vtt: Path) -> list[dict]:
    """Generate audio + word-boundary subtitle file using edge-tts."""
    import edge_tts  # type: ignore
    communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary")
    sub_maker = edge_tts.SubMaker()
    with open(out_audio, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                sub_maker.feed(chunk)

    srt_content = sub_maker.get_srt()
    vtt_content = "WEBVTT\n\n" + srt_content.replace(",", ".")
    with open(out_vtt, "w", encoding="utf-8") as vtt_file:
        vtt_file.write(vtt_content)

    # Return structured word timings directly from sub_maker cues
    timings = []
    for cue in sub_maker.cues:
        start_ms = int(cue.start.total_seconds() * 1000)
        end_ms = int(cue.end.total_seconds() * 1000)
        word = cue.content.strip()
        if word:
            timings.append({"word": word, "start_ms": start_ms, "end_ms": end_ms})
    return timings


def generate_voiceover(
    script: str,
    output_dir: Path,
    language: Optional[str] = None,
    video_id: Optional[int] = None,
) -> dict:
    """
    Generate voiceover MP3 + word-timestamp VTT for the given script.

    Returns:
        {
          "audio_path": str,
          "vtt_path": str,
          "word_timings": [{"word": str, "start_ms": int, "end_ms": int}],
          "duration_s": float,
        }
    """
    lang = language or os.environ.get("LANGUAGE", "en")
    voice = VOICE_MAP.get(lang, VOICE_MAP["en"])
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    prefix = f"video_{video_id}" if video_id else "voiceover"
    audio_path = output_dir / f"{prefix}_audio.mp3"
    vtt_path = output_dir / f"{prefix}_captions.vtt"

    logger.info("tts: generating with voice=%s", voice)
    word_timings = []
    try:
        word_timings = asyncio.run(_edge_tts_generate(script, voice, audio_path, vtt_path))
        logger.info("tts: edge-tts done: %s (%d word timings)", audio_path, len(word_timings))
    except Exception as e:
        logger.error("tts: edge-tts failed: %s — trying pyttsx3 fallback", e)
        _pyttsx3_fallback(script, audio_path)
        vtt_path = None  # pyttsx3 has no word timestamps

    # If VTT exists but word_timings was empty, fallback parse
    if not word_timings and vtt_path and Path(vtt_path).exists():
        word_timings = _parse_vtt(vtt_path)

    # Get audio duration
    duration_s = _get_audio_duration(audio_path)

    return {
        "audio_path": str(audio_path),
        "vtt_path": str(vtt_path) if vtt_path else None,
        "word_timings": word_timings,
        "duration_s": duration_s,
    }


def _pyttsx3_fallback(script: str, out_path: Path) -> None:
    """Local TTS fallback using pyttsx3 (no word timestamps)."""
    try:
        import pyttsx3  # type: ignore
        engine = pyttsx3.init()
        engine.save_to_file(script, str(out_path))
        engine.runAndWait()
        logger.info("tts: pyttsx3 fallback done: %s", out_path)
    except Exception as e:
        logger.error("tts: pyttsx3 fallback also failed: %s", e)
        raise


def _parse_vtt(vtt_path: Path) -> list[dict]:
    """Parse WebVTT file into a list of {word, start_ms, end_ms} dicts."""
    timings = []
    text = vtt_path.read_text(encoding="utf-8")
    import re
    # VTT cue: HH:MM:SS.mmm --> HH:MM:SS.mmm\nword
    pattern = re.compile(
        r"(\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}\.\d{3})\s*\n(.+)"
    )
    for m in pattern.finditer(text):
        start_ms = _vtt_time_to_ms(m.group(1))
        end_ms = _vtt_time_to_ms(m.group(2))
        word = m.group(3).strip()
        if word:
            timings.append({"word": word, "start_ms": start_ms, "end_ms": end_ms})
    return timings


def _vtt_time_to_ms(ts: str) -> int:
    """Convert HH:MM:SS.mmm to milliseconds."""
    h, m, rest = ts.split(":")
    s, ms = rest.split(".")
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)


def _get_audio_duration(path: Path) -> float:
    """Get audio duration in seconds using ffprobe."""
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        info = json.loads(result.stdout)
        return float(info["format"]["duration"])
    except Exception as e:
        logger.warning("tts: could not get duration: %s", e)
        return 0.0