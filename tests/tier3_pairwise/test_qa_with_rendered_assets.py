"""
Tier 3: Pairwise Combinatorial — QA Engine + Rendered Assets Integration
Tests cross-feature validation between QA Gate #2 and video/audio render outputs,
including resolution checks, caption presence, loudness compliance, and license tracking.
"""
from pathlib import Path
import pytest

from agent.modules import qa


def test_qa_passes_compliant_rendered_asset(create_test_mp4):
    """Pairwise 1: Rendered MP4 matching 1080x1920, -14 LUFS, and valid captions passes QA."""
    video_file = create_test_mp4("compliant_render.mp4", duration=25)
    render_info = {
        "width": 1080,
        "height": 1920,
        "duration_s": 25,
        "loudness_lufs": -14.0,
        "has_captions": True,
        "asset_licenses_complete": True,
        "video_path": str(video_file),
    }

    res = qa.check_render(render_info)
    assert res["passed"] is True
    assert res["reason"] is None


def test_qa_rejects_horizontal_or_wrong_resolution(create_test_mp4):
    """Pairwise 2: Horizontal video (1920x1080) or non-standard aspect ratio is rejected."""
    video_file = create_test_mp4("horizontal.mp4")
    render_info = {
        "width": 1920,
        "height": 1080,
        "duration_s": 30,
        "loudness_lufs": -14.0,
        "has_captions": True,
        "asset_licenses_complete": True,
        "video_path": str(video_file),
    }

    res = qa.check_render(render_info)
    assert res["passed"] is False
    assert "Resolution" in res["reason"]
    assert "1080x1920" in res["reason"]


def test_qa_rejects_render_missing_captions(create_test_mp4):
    """Pairwise 3: Video rendered without kinetic subtitles fails QA Gate #2."""
    video_file = create_test_mp4("no_captions.mp4")
    render_info = {
        "width": 1080,
        "height": 1920,
        "duration_s": 30,
        "loudness_lufs": -14.0,
        "has_captions": False,  # Missing captions
        "asset_licenses_complete": True,
        "video_path": str(video_file),
    }

    res = qa.check_render(render_info)
    assert res["passed"] is False
    assert "No captions detected" in res["reason"]


def test_qa_rejects_unacceptable_loudness(create_test_mp4):
    """Pairwise 4: Video with loudness outside [-16, -12] LUFS fails QA Gate #2."""
    video_file = create_test_mp4("loud_audio.mp4")
    render_info = {
        "width": 1080,
        "height": 1920,
        "duration_s": 30,
        "loudness_lufs": -10.5,  # Too loud (> -12)
        "has_captions": True,
        "asset_licenses_complete": True,
        "video_path": str(video_file),
    }

    res = qa.check_render(render_info)
    assert res["passed"] is False
    assert "Loudness -10.5 LUFS" in res["reason"]


def test_qa_rejects_incomplete_asset_licensing(create_test_mp4):
    """Pairwise 5: Render with unverified or unlogged asset licenses fails copyright gate."""
    video_file = create_test_mp4("unlicensed.mp4")
    render_info = {
        "width": 1080,
        "height": 1920,
        "duration_s": 30,
        "loudness_lufs": -14.0,
        "has_captions": True,
        "asset_licenses_complete": False,  # Missing license proof
        "video_path": str(video_file),
    }

    res = qa.check_render(render_info)
    assert res["passed"] is False
    assert "license log is incomplete" in res["reason"]


def test_qa_evaluates_procedural_fallback_clip(temp_workspace):
    """Pairwise 6: Procedural background clip passes resolution and existence checks."""
    from agent.modules import assets
    clip = assets._generate_procedural_clip("abstract_tech", temp_workspace, video_id=101)
    render_info = {
        "width": 1080,
        "height": 1920,
        "duration_s": 3,
        "loudness_lufs": -14.0,
        "has_captions": True,
        "asset_licenses_complete": True,
        "video_path": clip["local_path"],
    }
    # Duration < 15s check
    res = qa.check_render(render_info)
    # Clip alone is 3s (part of short), so check_render flags duration < 15s
    assert res["passed"] is False
    assert "Duration 3s is outside 15–60s" in res["reason"]
