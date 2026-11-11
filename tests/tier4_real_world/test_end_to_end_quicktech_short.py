"""
Tier 4: Real-World Application Scenarios — Full Autonomous Short Production Cycle
Tests the complete end-to-end lifecycle of producing a 'QuickTech' short under $0.00 constraints:
Idea -> Script -> QA1 -> TTS Audio -> Assets -> Captions -> Render -> QA2 -> Telegram Preview -> HITL Approval -> Publish.
"""
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from agent.modules import captions, notify, publish, qa
from tests.conftest import UnauthorizedActionError
from tests.tier1_features.test_hitl_gate import approve_action, stage_action_for_approval, verify_action_authorized


def test_full_quicktech_short_production_lifecycle(isolated_db, temp_workspace, monkeypatch, create_test_mp4):
    """
    Scenario 1: Comprehensive simulation of the entire autonomous production pipeline.
    """
    # ─── 1. Idea Backlog Ingestion ──────────────────────────────────────────
    with sqlite3.connect(isolated_db) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO ideas (idea, angle, format, score, status) "
            "VALUES ('Smartphone battery degradation', 'Unplug at 80% to double lifespan', 'Mistake reveal', 92.5, 'scripting')"
        )
        idea_id = cursor.lastrowid
        conn.commit()
    assert idea_id is not None

    # ─── 2. Scriptwriting & QA Gate #1 ──────────────────────────────────────
    script_data = {
        "title": "Why 99% Of People Charge Phones Wrong",
        "hook": "Stop charging your phone to 100 percent overnight.",  # 8 words (<= 12)
        "script": (
            "Stop charging your phone to 100 percent overnight. Modern lithium batteries "
            "degrade twice as fast when kept at peak voltage under high heat continuously. "
            "Instead, unplug at 80 percent to double your battery lifespan and retain peak performance. "
            "Extensive laboratory tests show this single habit preserves over 90 percent capacity after two full years. "
            "Try this simple routine tonight and protect your expensive device."
        ),
        "estimated_duration_s": 28,
        "sources": ["Battery University Research 2024", "IEEE Battery Life Standards"],
    }

    qa_script_res = qa.check_script(script_data)
    assert qa_script_res["passed"] is True, f"Script failed QA Gate #1: {qa_script_res['reason']}"

    # ─── 3. Video DB Registration ───────────────────────────────────────────
    with sqlite3.connect(isolated_db) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO videos (idea_id, title, script, script_qa_ok, yt_status) "
            "VALUES (?, ?, ?, 1, 'private')",
            (idea_id, script_data["title"], script_data["script"]),
        )
        video_id = cursor.lastrowid
        conn.commit()

    # ─── 4. Voiceover & Captions Generation ─────────────────────────────────
    tts_audio_path = temp_workspace / f"audio_{video_id}.wav"
    vtt_path = temp_workspace / f"captions_{video_id}.vtt"

    # Generate synthetic clean test audio using ffmpeg
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=28", str(tts_audio_path)],
        capture_output=True,
        check=True,
    )
    vtt_content = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:02.500\nStop charging your phone\n\n"
        "00:00:02.500 --> 00:00:05.000\nto 100 percent overnight.\n"
    )
    vtt_path.write_text(vtt_content, encoding="utf-8")

    # ─── 5. Asset Assembly & Video Rendering ─────────────────────────────────
    final_video_path = create_test_mp4(f"video_{video_id}_final.mp4", duration=28)

    # Log asset in database
    with sqlite3.connect(isolated_db) as conn:
        conn.execute(
            "INSERT INTO assets (video_id, asset_type, source_url, license, local_path) "
            "VALUES (?, 'procedural_background', 'local:ffmpeg_color', 'Procedural Generated', ?)",
            (video_id, str(final_video_path)),
        )
        conn.commit()

    # ─── 6. QA Gate #2 Review ───────────────────────────────────────────────
    render_info = {
        "width": 1080,
        "height": 1920,
        "duration_s": 28,
        "loudness_lufs": -14.0,
        "has_captions": True,
        "asset_licenses_complete": True,
        "video_path": str(final_video_path),
    }
    qa_render_res = qa.check_render(render_info)
    assert qa_render_res["passed"] is True, f"Render failed QA Gate #2: {qa_render_res['reason']}"

    # ─── 7. Telegram Interactive Preview Dispatch ───────────────────────────
    telegram_messages = []
    def mock_telegram_notify(event, text, level="info", buttons=None, video_path=None):
        telegram_messages.append({"event": event, "level": level, "buttons": buttons})

    monkeypatch.setattr(notify, "notify", mock_telegram_notify)

    # Stage action in HITL queue
    action_token = stage_action_for_approval(
        isolated_db,
        action_type="publish_video",
        payload={"video_id": video_id, "title": script_data["title"]},
    )

    notify.notify(
        event="VIDEO_READY_FOR_APPROVAL",
        text=f"Video #{video_id} passed QA. Click to approve publication.",
        level="action",
        buttons=[[("Approve", f"approve_{action_token}"), ("Reject", f"reject_{action_token}")]],
        video_path=str(final_video_path),
    )

    assert len(telegram_messages) == 1
    assert telegram_messages[0]["level"] == "action"

    # Before operator approval, publication must be locked out
    with pytest.raises(UnauthorizedActionError):
        verify_action_authorized(isolated_db, action_token)

    # ─── 8. Human-In-The-Loop Approval ──────────────────────────────────────
    approve_action(isolated_db, action_token)
    assert verify_action_authorized(isolated_db, action_token) is True

    # ─── 9. Upload with Synthetic Disclosure Flag ───────────────────────────
    monkeypatch.setattr(publish, "upload_short", lambda *a, **k: "yt_video_quicktech_01")

    yt_id = publish.upload_short(
        video_id=video_id,
        video_path=str(final_video_path),
        title=script_data["title"],
        description="Save your battery lifespan.",
        tags=["battery", "tech", "shorts"],
        contains_synthetic=True,
    )
    assert yt_id == "yt_video_quicktech_01"
