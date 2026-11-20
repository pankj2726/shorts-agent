"""
captions.py — Subtitle parsing, styling, kinetic formatting, and fallback transcription.

Capabilities:
  1. Converts edge-tts word-boundary WebVTT into styled ASS format with kinetic word highlighting.
  2. Fallback transcription via Groq free whisper-large-v3 API (2,000 req/day free).
  3. Caption sync validation for QA gates.
"""
import itertools
import logging
import os
import re
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ASS Subtitle Header template for 1080x1920 portrait Shorts
ASS_HEADER_TEMPLATE = """[Script Info]
Title: Shorts Kinetic Captions
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans,70,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,80,80,360,1
Style: Highlight,Noto Sans,74,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,2,80,80,360,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ms_to_ass_time(ms: int) -> str:
    """Format milliseconds into ASS timestamp H:MM:SS.cc"""
    hours = ms // 3_600_000
    ms %= 3_600_000
    minutes = ms // 60_000
    ms %= 60_000
    seconds = ms // 1000
    centis = (ms % 1000) // 10
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"


def _parse_vtt_timestamp(ts_str: str) -> int:
    """Convert HH:MM:SS.mmm or HH:MM:SS,mmm or MM:SS.mmm into milliseconds."""
    ts_str = ts_str.strip().replace(",", ".")
    parts = ts_str.split(":")
    if len(parts) == 3:
        h, m, rest = parts
        s, ms = rest.split(".")
        return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms[:3].ljust(3, "0"))
    elif len(parts) == 2:
        m, rest = parts
        s, ms = rest.split(".")
        return int(m) * 60000 + int(s) * 1000 + int(ms[:3].ljust(3, "0"))
    return 0


def vtt_to_ass(vtt_path: Path | str, ass_path: Path | str, chunk_words: int = 4) -> Path:
    """
    Convert a WebVTT word-timestamp file into a styled ASS subtitle file
    with kinetic chunking (3-5 words at a time) and current-word highlight.
    """
    vtt_path = Path(vtt_path)
    ass_path = Path(ass_path)

    if not vtt_path.exists():
        raise FileNotFoundError(f"VTT file not found: {vtt_path}")

    content = vtt_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    words: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            start_str, end_str = line.split("-->")
            start_ms = _parse_vtt_timestamp(start_str)
            end_ms = _parse_vtt_timestamp(end_str)
            text = ""
            if i + 1 < len(lines):
                text = lines[i + 1].strip()
                i += 1
            if text:
                words.append({"word": text, "start_ms": start_ms, "end_ms": end_ms})
        i += 1

    events: list[str] = []
    # Group words into short readable chunks for Shorts
    for start_idx in range(0, len(words), chunk_words):
        chunk = words[start_idx : start_idx + chunk_words]
        if not chunk:
            continue
        chunk_start_ms = chunk[0]["start_ms"]
        chunk_end_ms = chunk[-1]["end_ms"]

        # For each word in chunk, create an event highlighting that specific word
        for active_word in chunk:
            w_start = _ms_to_ass_time(max(chunk_start_ms, active_word["start_ms"]))
            w_end = _ms_to_ass_time(min(chunk_end_ms, active_word["end_ms"]))

            formatted_words = []
            for w in chunk:
                if w is active_word:
                    formatted_words.append(r"{\c&H0000FFFF\b1\fscx108\fscy108}" + w["word"] + r"{\r}")
                else:
                    formatted_words.append(w["word"])

            display_text = " ".join(formatted_words)
            event_line = f"Dialogue: 0,{w_start},{w_end},Default,,0,0,0,,{display_text}"
            events.append(event_line)

    ass_text = ASS_HEADER_TEMPLATE + "\n".join(events) + "\n"
    ass_path.write_text(ass_text, encoding="utf-8")
    logger.info("captions: generated kinetic ASS subtitles -> %s (%d events)", ass_path, len(events))
    return ass_path


def transcribe_audio_groq(audio_path: Path | str) -> list[dict[str, Any]]:
    """
    Fallback transcription using Groq free whisper-large-v3 API (2,000 req/day free).
    Returns word timings list: [{'word': str, 'start_ms': int, 'end_ms': int}]
    """
    audio_path = Path(audio_path)
    groq_api_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_api_key:
        logger.warning("captions: GROQ_API_KEY not set, cannot perform Groq Whisper transcription")
        return []

    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {groq_api_key}"}

    with open(audio_path, "rb") as f:
        files = {"file": (audio_path.name, f, "audio/mpeg")}
        data = {
            "model": "whisper-large-v3",
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
        }
        try:
            resp = requests.post(url, headers=headers, files=files, data=data, timeout=60)
            resp.raise_for_status()
            res_json = resp.json()
            words = []
            for w in res_json.get("words", []):
                words.append({
                    "word": w.get("word", "").strip(),
                    "start_ms": int(float(w.get("start", 0)) * 1000),
                    "end_ms": int(float(w.get("end", 0)) * 1000),
                })
            logger.info("captions: Groq Whisper transcribed %d words", len(words))
            return words
        except Exception as e:
            logger.error("captions: Groq Whisper transcription failed: %s", e)
            return []


VTT_SYNC_TOLERANCE_S = 1.0
"""
How far past the end of the audio a caption may run and still pass.

One second covers transcription boundary rounding and the fact that a final
word's timestamp is often slightly long. Anything larger means the subtitles are
talking after the voiceover has stopped.
"""

# HH:MM:SS.mmm or MM:SS.mmm (comma accepted as the decimal separator).
_VTT_TS = re.compile(
    r"^(?:(\d{1,3}):)?([0-5]?\d):([0-5]\d)[.,](\d{1,3})$"
)
_CUE_LINE = "-->"


def _parse_timestamp_strict(ts: str) -> int:
    """
    Parse one WebVTT timestamp into milliseconds.

    Raises ValueError on anything malformed. This is deliberately stricter than
    _parse_vtt_timestamp(), which returns 0 for a format it does not recognise —
    a silent 0 would make a broken cue look like a valid one at time zero.
    """
    m = _VTT_TS.match(ts.strip())
    if not m:
        raise ValueError(f"malformed timestamp {ts!r}")
    hours, minutes, seconds, frac = m.groups()
    ms = int(frac.ljust(3, "0")[:3])
    total = int(minutes) * 60_000 + int(seconds) * 1_000 + ms
    if hours:
        total += int(hours) * 3_600_000
    return total


def parse_cues(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Extract cues from WebVTT text.

    Returns (cues, errors). Each cue is {start_ms, end_ms, text}. Parsing
    problems are collected rather than raised so one bad cue does not hide the
    others from the report.
    """
    cues: list[dict[str, Any]] = []
    errors: list[str] = []
    lines = text.splitlines()

    for idx, line in enumerate(lines, start=1):
        if _CUE_LINE not in line:
            continue
        left, _, right = line.partition(_CUE_LINE)
        # A cue line may carry position/alignment settings after the end time.
        end_part = right.strip().split(" ", 1)[0]
        try:
            start_ms = _parse_timestamp_strict(left)
            end_ms = _parse_timestamp_strict(end_part)
        except ValueError as exc:
            errors.append(f"line {idx}: {exc}")
            continue
        # The text is whatever follows, up to the next blank line.
        body: list[str] = []
        for following in lines[idx:]:
            if not following.strip():
                break
            body.append(following.strip())
        cues.append({
            "line": idx,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": " ".join(body),
        })
    return cues, errors


def validate_captions(
    vtt_or_ass_path: Path | str,
    audio_duration_s: float | None,
    tolerance_s: float = VTT_SYNC_TOLERANCE_S,
    require_sync: bool = True,
) -> dict[str, Any]:
    """
    Validate a WebVTT caption file against the audio it belongs to.

    Checks structure, every cue's timestamps, cue ordering, and whether the last
    cue runs past the end of the audio by more than `tolerance_s`.

    Overlapping cues are permitted: word-level kinetic captions overlap by
    design, and overlap is a styling choice rather than a correctness error.
    Ordering is still enforced on cue start times, so a cue that begins before
    its predecessor is reported.

    The result always reports whether synchronization was actually checked.
    It never claims captions are in sync when the audio duration was unknown —
    the previous implementation returned "Captions valid and in sync" after
    checking only that the file existed and exceeded 20 bytes.

    Returns {passed, reason, sync_checked, cues, last_cue_end_s,
             audio_duration_s, tolerance_s}.
    """
    result: dict[str, Any] = {
        "passed": False,
        "reason": "",
        "sync_checked": False,
        "cues": 0,
        "last_cue_end_s": None,
        "audio_duration_s": audio_duration_s,
        "tolerance_s": tolerance_s,
    }

    path = Path(vtt_or_ass_path)
    if path.suffix.lower() == ".ass":
        result["reason"] = (
            "sync validation is implemented for WebVTT only; an ASS file cannot "
            "be validated here - validate the source VTT before converting"
        )
        return result

    if not path.exists():
        result["reason"] = f"caption file does not exist: {path}"
        return result

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        result["reason"] = f"caption file is not readable: {type(exc).__name__}: {exc}"
        return result

    if not text.strip():
        result["reason"] = "caption file is empty"
        return result

    # The header may be followed by a note or settings on the same line.
    first = text.lstrip("\ufeff").splitlines()[0].strip()
    if not first.startswith("WEBVTT"):
        result["reason"] = (
            f"missing WebVTT header (first line is {first[:40]!r})"
        )
        return result

    cues, parse_errors = parse_cues(text)
    result["cues"] = len(cues)

    if parse_errors:
        result["reason"] = f"malformed cue timestamp(s): {'; '.join(parse_errors[:3])}"
        return result

    if not cues:
        result["reason"] = "no caption cues found in file"
        return result

    for cue in cues:
        if cue["start_ms"] < 0:
            result["reason"] = (
                f"cue at line {cue['line']} starts before zero "
                f"({cue['start_ms']}ms)"
            )
            return result
        if cue["end_ms"] <= cue["start_ms"]:
            result["reason"] = (
                f"cue at line {cue['line']} ends at or before it starts "
                f"({cue['start_ms']}ms -> {cue['end_ms']}ms)"
            )
            return result

    for prev, cur in itertools.pairwise(cues):
        if cur["start_ms"] < prev["start_ms"]:
            result["reason"] = (
                f"cue at line {cur['line']} starts before the previous cue "
                f"({cur['start_ms']}ms < {prev['start_ms']}ms); overlapping is "
                "allowed but cues must not go backwards"
            )
            return result

    last_end_s = cues[-1]["end_ms"] / 1000.0
    result["last_cue_end_s"] = round(last_end_s, 3)

    if audio_duration_s is None:
        if require_sync:
            result["reason"] = (
                "audio duration is unknown, so synchronization could not be "
                f"checked (last caption ends at {last_end_s:.3f}s)"
            )
            return result
        result["passed"] = True
        result["reason"] = (
            "captions are structurally valid; synchronization not checked "
            "(audio duration unavailable)"
        )
        return result

    try:
        duration = float(audio_duration_s)
    except (TypeError, ValueError):
        result["reason"] = f"audio duration is not a number: {audio_duration_s!r}"
        return result

    if duration <= 0:
        result["reason"] = f"audio duration is not positive: {duration}"
        return result

    result["sync_checked"] = True
    overrun = last_end_s - duration
    if overrun > tolerance_s:
        result["reason"] = (
            f"captions run {overrun:.3f}s past the end of the audio "
            f"(last caption {last_end_s:.3f}s, audio {duration:.3f}s, "
            f"tolerance {tolerance_s:.3f}s)"
        )
        return result

    result["passed"] = True
    result["reason"] = (
        f"{len(cues)} cue(s) valid and synchronized "
        f"(last caption {last_end_s:.3f}s, audio {duration:.3f}s, "
        f"tolerance {tolerance_s:.3f}s)"
    )
    return result
