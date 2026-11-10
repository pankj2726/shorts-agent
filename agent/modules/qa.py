"""
qa.py — QA gates for scripts (Gate #1) and rendered videos (Gate #2).
"""
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_banned() -> list[str]:
    path = CONFIG_DIR / "banned_topics.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").lower()
    # Very simple extraction — ban words/phrases listed after '- '
    items = re.findall(r"^- (.+)$", text, re.MULTILINE)
    return [i.strip().lower() for i in items]


BANNED_PATTERNS = None  # lazy-loaded


def _get_banned() -> list[str]:
    global BANNED_PATTERNS
    if BANNED_PATTERNS is None:
        BANNED_PATTERNS = _load_banned()
    return BANNED_PATTERNS


def check_script(script: dict) -> dict:
    """
    QA Gate #1: validate a script dict from the Scriptwriter.

    Checks:
    - hook ≤ 12 words
    - estimated duration 20–45 s
    - no banned topics
    - no policy-risk phrases
    - every claim has a source
    - title < 60 chars

    Returns: {"passed": bool, "reason": str | None}
    """
    errors = []

    # Hook length
    hook = script.get("hook", "")
    word_count = len(hook.split())
    if word_count > 12:
        errors.append(f"Hook is {word_count} words (max 12): '{hook}'")
    if not hook.strip():
        errors.append("Hook is empty")

    # Duration
    duration = script.get("estimated_duration_s", 0)
    if not (20 <= duration <= 45):
        errors.append(f"Estimated duration {duration}s is outside 20–45s range")

    # Title
    title = script.get("title", "")
    if len(title) > 60:
        errors.append(f"Title is {len(title)} chars (max 60)")
    if not title.strip():
        errors.append("Title is empty")

    # Script
    script_text = script.get("script", "").lower()
    if not script_text.strip():
        errors.append("Script body is empty")

    # Banned topics
    for banned in _get_banned():
        if len(banned) > 4 and banned in script_text:
            errors.append(f"Banned topic detected: '{banned}'")

    # Policy risk phrases
    policy_risks = [
        "guaranteed to cure",
        "you should invest",
        "this is not financial advice",  # the disclaimer itself is a signal
        "as a doctor",
        "legally you must",
    ]
    for risk in policy_risks:
        if risk in script_text:
            errors.append(f"Policy-risk phrase detected: '{risk}'")

    # Sources
    sources = script.get("sources", [])
    # Count claims in script (rough heuristic: sentences with numbers or superlatives)
    claim_indicators = re.findall(r"\d+%|\d+ times|\d+ hours|\bmost\b|\bevery\b|\balways\b|\bnever\b",
                                  script_text)
    if claim_indicators and not sources:
        errors.append("Script contains quantified claims but no sources provided")

    # Word count sanity
    words = len(script.get("script", "").split())
    if words < 50:
        errors.append(f"Script is only {words} words — too short")
    if words > 150:
        errors.append(f"Script is {words} words — likely too long for 45s")

    if errors:
        return {"passed": False, "reason": "; ".join(errors)}
    return {"passed": True, "reason": None}


def check_render(render_info: dict) -> dict:
    """
    QA Gate #2: validate a rendered video.

    render_info keys:
      width, height, duration_s, loudness_lufs, has_captions,
      asset_licenses_complete, video_path
    """
    errors = []

    # Resolution
    if render_info.get("width") != 1080 or render_info.get("height") != 1920:
        errors.append(f"Resolution {render_info.get('width')}x{render_info.get('height')} (need 1080x1920)")

    # Duration
    dur = render_info.get("duration_s", 0)
    if not (15 <= dur <= 60):
        errors.append(f"Duration {dur}s is outside 15–60s range")

    # Loudness
    lufs = render_info.get("loudness_lufs")
    if lufs is not None and not (-16 <= lufs <= -12):
        errors.append(f"Loudness {lufs} LUFS (target: −14 LUFS ± 2)")

    # Captions
    if not render_info.get("has_captions"):
        errors.append("No captions detected in render")

    # Asset licenses
    if not render_info.get("asset_licenses_complete"):
        errors.append("Asset license log is incomplete — log all source URLs and licenses")

    # File exists
    video_path = render_info.get("video_path", "")
    if not video_path or not Path(video_path).exists():
        errors.append(f"Rendered video file not found: {video_path}")

    if errors:
        return {"passed": False, "reason": "; ".join(errors)}
    return {"passed": True, "reason": None}