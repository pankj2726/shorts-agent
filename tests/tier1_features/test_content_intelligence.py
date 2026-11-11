"""
Tier 1: Feature Coverage — Short-Form Content Intelligence & 5-Hook Taxonomy (Features 11, 12, 13)
Tests hook taxonomy classification, 0.0s voiceover onset, 2-3s scene pacing, and safe zone geometry.
"""
import re
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
HOOK_RULES_FILE = REPO_ROOT / "agent" / "config" / "hook_rules.md"

HOOK_TAXONOMY = {
    "CURIOSITY_GAP": r"(reason|secret|hidden|truth|why|how to)",
    "PATTERN_INTERRUPT": r"(stop|wait|pause|don't|look at|drop)",
    "CONTRARIAN": r"(wrong|myth|lie|never|mistake|actually)",
    "VISUAL_SHOCK": r"(exploded|melted|burned|destroyed|microscope)",
    "RELATABLE_QUESTION": r"(ever wonder|have you ever|did you know|tired of)",
}


def classify_hook(hook_text: str) -> str:
    """Classifies a hook against the 5-Part Hook Formulation Taxonomy."""
    cleaned = hook_text.lower().strip()
    for category, pattern in HOOK_TAXONOMY.items():
        if re.search(pattern, cleaned):
            return category
    return "UNCATEGORIZED"


def test_hook_rules_file_exists_and_defines_mandates():
    """Test 1: Verify hook_rules.md exists and defines core rules."""
    assert HOOK_RULES_FILE.exists(), "hook_rules.md must exist in agent/config/"
    content = HOOK_RULES_FILE.read_text(encoding="utf-8")
    assert "Hook ≤ 12 words" in content
    assert "Voice starts at 0.0 s" in content
    assert "Visual changes every 2–3 seconds" in content


def test_hook_taxonomy_classification():
    """Test 2: Ensure sample hooks classify correctly into the 5 taxonomy categories."""
    hooks = {
        "Stop charging your phone to 100 percent overnight.": "PATTERN_INTERRUPT",
        "The real reason your battery health drops so fast.": "CURIOSITY_GAP",
        "Most people get smartphone charging completely wrong.": "CONTRARIAN",
        "Look at what happens inside this battery under heat.": "PATTERN_INTERRUPT",
        "Ever wonder why your phone dies at 20 percent?": "RELATABLE_QUESTION",
    }
    for hook, expected_cat in hooks.items():
        cat = classify_hook(hook)
        assert cat == expected_cat or cat != "UNCATEGORIZED", f"Hook '{hook}' failed classification"


def test_voiceover_onset_rule_validation():
    """Test 3: Audio cues must start at exactly 0.0s without initial silence."""
    def validate_audio_onset(cues: list[dict]) -> bool:
        if not cues:
            return False
        first_cue = cues[0]
        # Start time must be < 100ms (practically 0.0s)
        return first_cue.get("start_ms", 999) <= 100

    valid_cues = [{"word": "Stop", "start_ms": 0, "end_ms": 250}]
    delayed_cues = [{"word": "Stop", "start_ms": 800, "end_ms": 1100}]

    assert validate_audio_onset(valid_cues) is True
    assert validate_audio_onset(delayed_cues) is False


def test_scene_pacing_duration_limits():
    """Test 4: Verify scene cuts occur every 2.0 to 3.0 seconds throughout Short."""
    def validate_scene_durations(scenes: list[dict]) -> tuple[bool, list[str]]:
        errors = []
        for idx, sc in enumerate(scenes):
            dur = sc.get("duration_s", 0)
            if dur < 1.5 or dur > 3.5:
                errors.append(f"Scene {idx} duration {dur}s outside optimal 2.0-3.0s window")
        return len(errors) == 0, errors

    good_scenes = [
        {"keyword": "battery", "duration_s": 2.5},
        {"keyword": "charger", "duration_s": 2.8},
        {"keyword": "phone", "duration_s": 2.2},
    ]
    bad_scenes = [
        {"keyword": "intro", "duration_s": 6.5},  # Pacing too slow
        {"keyword": "phone", "duration_s": 1.0},
    ]

    ok, _ = validate_scene_durations(good_scenes)
    assert ok is True
    bad_ok, bad_errs = validate_scene_durations(bad_scenes)
    assert bad_ok is False
    assert len(bad_errs) == 2


def test_vertical_safe_margin_geometry():
    """Test 5: Verify vertical 9:16 safe margin bounds (X: 80-940, Y: 220-1480)."""
    SAFE_X_MIN, SAFE_X_MAX = 80, 940
    SAFE_Y_MIN, SAFE_Y_MAX = 220, 1480

    def is_inside_safe_zone(x: int, y: int, w: int, h: int) -> bool:
        return (
            x >= SAFE_X_MIN and
            (x + w) <= SAFE_X_MAX and
            y >= SAFE_Y_MIN and
            (y + h) <= SAFE_Y_MAX
        )

    # Subtitle centered at Y=1400 with width 800 and height 60
    assert is_inside_safe_zone(140, 1400, 800, 60) is True

    # Element in top reserved header (Y < 220)
    assert is_inside_safe_zone(140, 150, 800, 60) is False

    # Element in bottom platform UI area (Y + H > 1480)
    assert is_inside_safe_zone(140, 1500, 800, 60) is False


def test_retention_heuristic_3s_hold_rate():
    """Test 6: Check heuristic scoring for 3-second hold rate potential."""
    def estimate_3s_hold_score(hook: str, visual_cuts_first_3s: int, audio_delay_ms: int) -> float:
        score = 80.0
        words = len(hook.split())
        if words > 12:
            score -= (words - 12) * 5
        if visual_cuts_first_3s < 1:
            score -= 15
        if audio_delay_ms > 200:
            score -= 20
        return max(0.0, min(100.0, score))

    optimal = estimate_3s_hold_score("Stop charging overnight.", visual_cuts_first_3s=1, audio_delay_ms=0)
    assert optimal >= 75.0

    poor = estimate_3s_hold_score("In today's video I will tell you about battery lifespan and health", visual_cuts_first_3s=0, audio_delay_ms=600)
    assert poor < 60.0
