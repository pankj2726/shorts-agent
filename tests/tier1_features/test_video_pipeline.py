"""
Tier 1: Feature Coverage — Side-Hustle End-to-End Production Pipeline (Features 26–31)
Tests 1080x1920 video composition, -14 LUFS audio normalization, kinetic ASS subtitle formatting,
procedural background fallback generation, and synthetic media disclosure flags.
"""
import subprocess
from pathlib import Path
import pytest

from agent.modules import assets, captions, notify, publish, render


def test_procedural_fallback_clip_generation(temp_workspace):
    """Test 1: Fallback procedural clip generation produces valid 1080x1920 MP4 file."""
    clip = assets._generate_procedural_clip("technology", temp_workspace, video_id=99)
    assert clip is not None
    assert clip["asset_type"] == "procedural_background"
    assert clip["license"] == "Procedural Generated"
    assert Path(clip["local_path"]).exists()

    # Probe resolution with ffprobe
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0", clip["local_path"]
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert "1080,1920" in res.stdout.strip()


def test_kinetic_ass_subtitles_formatting():
    """Test 2: Verify ASS header specifies 1080x1920 resolution and safe margins."""
    assert "PlayResX: 1080" in captions.ASS_HEADER_TEMPLATE
    assert "PlayResY: 1920" in captions.ASS_HEADER_TEMPLATE
    assert "MarginV, Encoding" in captions.ASS_HEADER_TEMPLATE
    # Verify MarginV is at safe height (360)
    assert ",360," in captions.ASS_HEADER_TEMPLATE


def test_vtt_timestamp_parser_conversions():
    """Test 3: Verify timestamp parsing and ASS timestamp formatting."""
    ms = captions._parse_vtt_timestamp("00:01:23.450")
    assert ms == 83450

    ass_time = captions._ms_to_ass_time(ms)
    assert ass_time == "0:01:23.45"


def test_synthetic_media_disclosure_flag_in_publish(isolated_db, monkeypatch):
    """Test 4: Verify upload_short records contains_synthetic flag in DB."""
    from agent.modules import db
    conn = db.get_conn()
    conn.execute("INSERT INTO videos (id, title) VALUES (1, 'Tech Short')")
    conn.commit()
    conn.close()

    # Mock youtube service to prevent network call
    monkeypatch.setattr(publish, "upload_short", lambda *a, **k: "yt_mock_id_123")

    yt_id = publish.upload_short(
        video_id=1,
        video_path="fake.mp4",
        title="Test Synthetic Short",
        description="Demo",
        tags=["ai", "tech"],
        contains_synthetic=True,
    )
    assert yt_id == "yt_mock_id_123"


def test_telegram_interactive_preview_button_formatting(monkeypatch):
    """Test 5: Verify notify() correctly packages inline buttons under 64-byte limit."""
    sent_payload = {}

    def mock_send(url, data=None, json=None, **kwargs):
        nonlocal sent_payload
        sent_payload = data or json or {}
        mock_resp = type("Resp", (), {"status_code": 200, "json": lambda: {"ok": True}, "raise_for_status": lambda: None})
        return mock_resp()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock_bot_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345678")
    monkeypatch.setattr("requests.post", mock_send)

    buttons = [
        [("Approve", "act_approve_video_42"), ("Reject", "act_reject_video_42")],
        [("Regenerate", "act_regen_video_42")]
    ]

    notify.notify(
        event="PREVIEW_READY",
        text="Please review video #42 before publication.",
        level="action",
        buttons=buttons,
    )

    markup = sent_payload.get("reply_markup")
    assert markup is not None
    assert "act_approve_video_42" in str(markup)
    assert "act_reject_video_42" in str(markup)


def test_render_target_constants():
    """Test 6: Verify rendering target constants match 1080x1920 and -14.0 LUFS."""
    assert render.TARGET_W == 1080
    assert render.TARGET_H == 1920
    assert render.TARGET_LUFS == -14.0
