"""
test_storyboard.py — Unit Tests for Storyboard & Scene Director Engine.
"""

import json
from pathlib import Path
from agent.modules.storyboard import StoryboardEngine


def test_generate_storyboard_basic():
    script = (
        "Stop scrolling if you use Windows every single day. "
        "Pressing Windows plus V unlocks your entire clipboard history. "
        "It saves screenshots, code snippets, and links forever. "
        "Pin your most used items and never retype them again. "
        "Subscribe for daily Windows hacks that save hours."
    )
    storyboard = StoryboardEngine.generate_storyboard(
        script_text=script,
        total_duration_s=30.0,
        title="Windows Clipboard Secret",
        niche="technology",
    )

    assert len(storyboard) >= 4
    assert storyboard[0]["phase"] == "HOOK"
    assert storyboard[-1]["phase"] == "CALL_TO_ACTION"

    for scene in storyboard:
        assert "start_s" in scene
        assert "end_s" in scene
        assert "visual_prompt" in scene
        assert "kinetic_typography" in scene
        assert "sound_effect" in scene
        assert scene["start_s"] < scene["end_s"]


def test_validate_storyboard_success():
    script = "Hook sentence here. Demonstration sentence one. Demonstration sentence two. Final call to action."
    storyboard = StoryboardEngine.generate_storyboard(script_text=script, total_duration_s=25.0)

    val = StoryboardEngine.validate_storyboard(storyboard, expected_duration_s=25.0)
    assert val["passed"] is True
    assert val["scene_count"] >= 3
    assert val["average_scene_duration_s"] <= StoryboardEngine.MAX_SCENE_DURATION_S + 0.5


def test_export_storyboard_json(tmp_path: Path):
    script = "Quick test hook. Second scene demo. Follow for more."
    storyboard = StoryboardEngine.generate_storyboard(script_text=script, total_duration_s=15.0)

    out_file = tmp_path / "test_storyboard.json"
    exported_path = StoryboardEngine.export_storyboard_json(storyboard, out_file)

    assert Path(exported_path).exists()
    with open(exported_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "storyboard" in data
    assert len(data["storyboard"]) == len(storyboard)
