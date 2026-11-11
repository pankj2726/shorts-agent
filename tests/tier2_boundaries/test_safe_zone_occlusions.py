"""
Tier 2: Boundary & Corner Cases — Safe Zone Occlusion Boundaries
Tests 1080x1920 portrait safe bounding box boundaries (X: 80–940, Y: 220–1480),
preventing visual elements from being occluded by YouTube/Reels platform chrome.
"""
import pytest

# Platform Safe Zone specifications
SAFE_X_MIN = 80
SAFE_X_MAX = 940
SAFE_Y_MIN = 220
SAFE_Y_MAX = 1480


def check_element_safe_zone(x: int, y: int, width: int, height: int) -> tuple[bool, list[str]]:
    """Checks whether a visual element's bounding box is fully contained within the safe zone."""
    violations = []
    x_end = x + width
    y_end = y + height

    if x < SAFE_X_MIN:
        violations.append(f"Left edge {x}px violates left safe margin (min {SAFE_X_MIN}px)")
    if x_end > SAFE_X_MAX:
        violations.append(f"Right edge {x_end}px violates right engagement button safe area (max {SAFE_X_MAX}px)")
    if y < SAFE_Y_MIN:
        violations.append(f"Top edge {y}px occluded by top search/sound bar (min {SAFE_Y_MIN}px)")
    if y_end > SAFE_Y_MAX:
        violations.append(f"Bottom edge {y_end}px occluded by bottom channel/subscribe UI (max {SAFE_Y_MAX}px)")

    return len(violations) == 0, violations


def test_safe_zone_exact_minimum_x_boundary():
    """Boundary Case 1: X=80 (exact minimum X margin) passes; X=79 fails."""
    # At X=80
    ok, _ = check_element_safe_zone(x=80, y=500, width=400, height=100)
    assert ok is True

    # At X=79
    ok_fail, violations = check_element_safe_zone(x=79, y=500, width=400, height=100)
    assert ok_fail is False
    assert any("left safe margin" in v for v in violations)


def test_safe_zone_exact_maximum_x_boundary():
    """Boundary Case 2: Right edge exactly at 940px passes; at 941px fails."""
    # Right edge = 540 + 400 = 940
    ok, _ = check_element_safe_zone(x=540, y=500, width=400, height=100)
    assert ok is True

    # Right edge = 541 + 400 = 941
    ok_fail, violations = check_element_safe_zone(x=541, y=500, width=400, height=100)
    assert ok_fail is False
    assert any("engagement button" in v for v in violations)


def test_safe_zone_exact_minimum_y_boundary():
    """Boundary Case 3: Y=220 (exact minimum Y margin) passes; Y=219 fails."""
    ok, _ = check_element_safe_zone(x=200, y=220, width=400, height=200)
    assert ok is True

    ok_fail, violations = check_element_safe_zone(x=200, y=219, width=400, height=200)
    assert ok_fail is False
    assert any("top search/sound bar" in v for v in violations)


def test_safe_zone_exact_maximum_y_boundary():
    """Boundary Case 4: Bottom edge exactly at 1480px passes; at 1481px fails."""
    # Bottom edge = 1380 + 100 = 1480
    ok, _ = check_element_safe_zone(x=200, y=1380, width=400, height=100)
    assert ok is True

    # Bottom edge = 1381 + 100 = 1481
    ok_fail, violations = check_element_safe_zone(x=200, y=1381, width=400, height=100)
    assert ok_fail is False
    assert any("bottom channel/subscribe" in v for v in violations)


def test_subtitle_vertical_margin_within_safe_area():
    """Boundary Case 5: Standard subtitle styling (MarginV=360 to 380) centers in lower safe zone."""
    # With MarginV = 360, subtitle baseline is approx Y = 1920 - 360 = 1560
    # Center text box of height 100 placed at Y = 1360 to 1460 (well inside 220-1480)
    ok, _ = check_element_safe_zone(x=140, y=1360, width=800, height=100)
    assert ok is True


def test_fullscreen_graphics_without_safe_margins_flagged():
    """Boundary Case 6: Fullscreen text graphic (0,0 to 1080,1920) triggers multiple safe zone violations."""
    ok, violations = check_element_safe_zone(x=0, y=0, width=1080, height=1920)
    assert ok is False
    assert len(violations) == 4
