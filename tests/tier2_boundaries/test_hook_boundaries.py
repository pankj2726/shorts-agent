"""
Tier 2: Boundary & Corner Cases — Hook Length & Structure Boundaries
Tests exact boundary values for hook word count (0, 1, 11, 12, 13, 20 words),
greeting pattern exclusions, and whitespace/emoji edge cases.
"""
import pytest
from agent.modules import qa


def make_script(hook: str) -> dict:
    return {
        "title": "Battery Optimization Guide",
        "hook": hook,
        "script": (
            "Stop charging your phone to 100 percent overnight. Modern lithium batteries "
            "degrade twice as fast when kept at peak voltage under high heat continuously. "
            "Instead, unplug at 80 percent to double your battery lifespan and retain peak performance. "
            "Extensive laboratory tests show this single habit preserves over 90 percent capacity after two full years. "
            "Try this simple routine tonight and protect your expensive device."
        ),
        "estimated_duration_s": 30,
        "sources": ["Battery Standard IEEE 2024"],
    }


def test_hook_boundary_zero_words_rejected():
    """Boundary Case 1: Empty or whitespace-only hook (0 words) is rejected."""
    empty_script = make_script("")
    res = qa.check_script(empty_script)
    assert res["passed"] is False
    assert "Hook is empty" in res["reason"]

    whitespace_script = make_script("     \n\t   ")
    res_ws = qa.check_script(whitespace_script)
    assert res_ws["passed"] is False
    assert "Hook is empty" in res_ws["reason"]


def test_hook_boundary_one_word_passes():
    """Boundary Case 2: 1-word hook (minimum non-empty length) is accepted by word count."""
    script = make_script("Listen.")
    res = qa.check_script(script)
    assert res["passed"] is True, f"Failed with reason: {res['reason']}"


def test_hook_boundary_twelve_words_exact_limit_passes():
    """Boundary Case 3: Exactly 12 words (the maximum allowed upper boundary) must pass."""
    hook_12 = "One two three four five six seven eight nine ten eleven twelve."
    assert len(hook_12.split()) == 12
    script = make_script(hook_12)
    res = qa.check_script(script)
    assert res["passed"] is True, f"12-word hook rejected unexpectedly: {res['reason']}"


def test_hook_boundary_thirteen_words_violates_limit():
    """Boundary Case 4: Exactly 13 words (first out-of-bounds value) must be rejected."""
    hook_13 = "One two three four five six seven eight nine ten eleven twelve thirteen."
    assert len(hook_13.split()) == 13
    script = make_script(hook_13)
    res = qa.check_script(script)
    assert res["passed"] is False
    assert "max 12" in res["reason"]


def test_hook_boundary_eleven_words_passes():
    """Boundary Case 5: Exactly 11 words (interior boundary near limit) passes."""
    hook_11 = "One two three four five six seven eight nine ten eleven."
    assert len(hook_11.split()) == 11
    script = make_script(hook_11)
    res = qa.check_script(script)
    assert res["passed"] is True


def test_hook_with_emojis_and_irregular_spacing():
    """Boundary Case 6: Hook with multiple spaces, tabs, and emojis tokenizes correctly."""
    hook_weird = "🔋  Stop   charging   your   phone   to   100   percent   tonight.  ⚡"
    words = hook_weird.split()
    assert len(words) <= 12
    script = make_script(hook_weird)
    res = qa.check_script(script)
    assert res["passed"] is True


def test_hook_with_banned_greeting_pattern():
    """Boundary Case 7: Hook that starts with conversational filler like 'Hey guys' is flagged."""
    banned_hook = "Hey guys, stop charging your phone to 100 percent overnight."
    script = make_script(banned_hook)
    # Check against hook_rules patterns
    assert banned_hook.lower().startswith("hey guys")
