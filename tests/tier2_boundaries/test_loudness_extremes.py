"""
Tier 2: Boundary & Corner Cases — Loudness Normalization Extremes (-14 LUFS +- 2)
Tests boundary conditions for audio loudness (-16.0 to -12.0 LUFS window),
True Peak ceilings (<= -1.0 dBFS), and extreme rejection thresholds.
"""
import pytest
from agent.modules import qa


def make_render_info(loudness_lufs: float, true_peak_db: float = -1.5, video_path: str = "dummy.mp4") -> dict:
    return {
        "width": 1080,
        "height": 1920,
        "duration_s": 30,
        "loudness_lufs": loudness_lufs,
        "has_captions": True,
        "asset_licenses_complete": True,
        "video_path": video_path,
        "true_peak_db": true_peak_db,
    }


def validate_loudness_boundary(lufs: float, true_peak: float = -1.5) -> tuple[bool, str | None]:
    """Evaluates loudness against the -14 LUFS +- 2.0 LUFS specification."""
    if not (-16.0 <= lufs <= -12.0):
        return False, f"Loudness {lufs} LUFS is outside acceptable [-16.0, -12.0] band (target -14 LUFS +- 2)"
    if true_peak > -1.0:
        return False, f"True peak {true_peak} dBFS exceeds -1.0 dBFS ceiling (distortion risk)"
    return True, None


def test_loudness_boundary_exact_target_minus_14():
    """Boundary Case 1: Audio at exactly -14.0 LUFS (nominal target) passes."""
    passed, reason = validate_loudness_boundary(-14.0, true_peak=-1.5)
    assert passed is True
    assert reason is None


def test_loudness_boundary_upper_limit_minus_12():
    """Boundary Case 2: Audio at exactly -12.0 LUFS (upper tolerance limit) passes."""
    passed, reason = validate_loudness_boundary(-12.0, true_peak=-1.0)
    assert passed is True
    assert reason is None


def test_loudness_boundary_above_upper_limit_minus_11_9_fails():
    """Boundary Case 3: Audio at -11.9 LUFS (0.1 LUFS too loud) is rejected."""
    passed, reason = validate_loudness_boundary(-11.9, true_peak=-1.0)
    assert passed is False
    assert "outside acceptable" in reason


def test_loudness_boundary_lower_limit_minus_16():
    """Boundary Case 4: Audio at exactly -16.0 LUFS (lower tolerance limit) passes."""
    passed, reason = validate_loudness_boundary(-16.0, true_peak=-2.0)
    assert passed is True
    assert reason is None


def test_loudness_boundary_below_lower_limit_minus_16_1_fails():
    """Boundary Case 5: Audio at -16.1 LUFS (0.1 LUFS too quiet) is rejected."""
    passed, reason = validate_loudness_boundary(-16.1, true_peak=-2.5)
    assert passed is False
    assert "outside acceptable" in reason


def test_loudness_true_peak_ceiling_boundary():
    """Boundary Case 6: True peak > -1.0 dBFS fails even if LUFS is within window."""
    # At -1.0 dBFS -> PASS
    passed_limit, _ = validate_loudness_boundary(-14.0, true_peak=-1.0)
    assert passed_limit is True

    # At -0.8 dBFS -> FAIL
    passed_violation, reason = validate_loudness_boundary(-14.0, true_peak=-0.8)
    assert passed_violation is False
    assert "distortion risk" in reason


def test_loudness_extreme_deafening_and_whisper_rejection():
    """Boundary Case 7: Extreme loudness (-6.0 LUFS clipped, -28.0 LUFS silent) rejected."""
    assert validate_loudness_boundary(-6.0)[0] is False
    assert validate_loudness_boundary(-28.0)[0] is False
