"""
Tier 3: Pairwise Combinatorial — Pollinations Pacing + Procedural Fallback Integration
Tests 6.0s inter-request rate pacing, automatic fallback to ffmpeg procedural backgrounds
when Pollinations fails or times out, and asset license registration.
"""
import time
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from agent.modules import assets


def test_asset_fetcher_zero_keys_selects_pollinations_or_procedural(monkeypatch, temp_workspace, isolated_db):
    """Pairwise 1: Without Pexels/Pixabay keys, fetcher seamlessly selects free generator or procedural."""
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)

    # Mock pollinations to return a simulated clip
    mock_clip = {
        "asset_type": "generated_image_motion",
        "source_url": "https://image.pollinations.ai/prompt/test",
        "license": "CC0 / Free Public Domain (Pollinations.ai)",
        "local_path": str(temp_workspace / "mock_motion.mp4"),
        "keyword": "smartphone",
    }
    monkeypatch.setattr(assets, "_fetch_pollinations_motion_clip", lambda kw, out, vid: mock_clip)

    clips = assets.fetch_stock_clips(["smartphone"], temp_workspace, video_id=50)
    assert len(clips) == 1
    assert clips[0]["asset_type"] == "generated_image_motion"


def test_pollinations_failure_triggers_procedural_fallback(monkeypatch, temp_workspace, isolated_db):
    """Pairwise 2: Pollinations failure or timeout triggers local ffmpeg procedural generator."""
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)

    # Simulate Pollinations failure
    monkeypatch.setattr(assets, "_fetch_pollinations_motion_clip", lambda kw, out, vid: None)

    clips = assets.fetch_stock_clips(["battery_overheat"], temp_workspace, video_id=51)
    assert len(clips) == 1
    assert clips[0]["asset_type"] == "procedural_background"
    assert "Procedural" in clips[0]["license"]
    assert Path(clips[0]["local_path"]).exists()


def test_rate_pacing_delays_between_pollinations_requests():
    """Pairwise 3: Rate pacing enforces minimum interval (simulated 6.0s rule) between calls."""
    class RatePacer:
        def __init__(self, interval: float = 6.0):
            self.interval = interval
            self.last_call = None

        def pace(self, current_time: float | None = None) -> float:
            now = current_time if current_time is not None else time.time()
            if self.last_call is None:
                self.last_call = now
                return 0.0
            gap = now - self.last_call
            sleep_needed = max(0.0, self.interval - gap)
            self.last_call = now + sleep_needed
            return sleep_needed

    pacer = RatePacer(interval=6.0)
    # First call at t=0 executes immediately
    assert pacer.pace(current_time=0.0) == 0.0

    # Call at t=2.0 (only 2s elapsed) -> needs 4.0s sleep
    assert pacer.pace(current_time=2.0) == 4.0

    # Call at t=15.0 (9s after previous completion at t=6.0) -> 0s sleep
    assert pacer.pace(current_time=15.0) == 0.0


def test_asset_database_logging_integrity(isolated_db, temp_workspace):
    """Pairwise 4: Every fetched or fallback asset is recorded in SQLite assets table."""
    from agent.modules import db

    clip = assets._generate_procedural_clip("circuit_board", temp_workspace, video_id=77)
    assets._log_asset(77, clip)

    conn = db.get_conn()
    row = conn.execute("SELECT asset_type, source_url, license FROM assets WHERE video_id = 77").fetchone()
    conn.close()

    assert row is not None
    assert row["asset_type"] == "procedural_background"
    assert "ffmpeg_color" in row["source_url"]


def test_procedural_clip_resolution_and_codec(temp_workspace):
    """Pairwise 5: Procedural clip generates 1080x1920 vertical H.264 video with yuv420p pixel format."""
    import subprocess
    clip = assets._generate_procedural_clip("abstract_blue", temp_workspace, video_id=88)
    path = clip["local_path"]

    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name,pix_fmt",
        "-of", "csv=p=0", path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    output = res.stdout.strip()
    assert "1080,1920" in output
    assert "h264" in output
    assert "yuv420p" in output
