"""
test_generative_media.py — Tests for Pollinations generative media & motion clip builder.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from agent.modules.generative_media import generate_scene_image, create_cinematic_motion_clip


@patch("urllib.request.urlopen")
def test_generate_scene_image(mock_urlopen, tmp_path):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"\xff\xd8\xff" + b"A" * 1500  # Valid dummy JPEG bytes
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    output = tmp_path / "test_frame.jpg"
    res = generate_scene_image(
        prompt="Snow leopard in blizzard, 9:16 portrait",
        output_path=output,
        width=1080,
        height=1920,
        model="flux"
    )

    assert res == output
    assert output.exists()
    assert output.stat().st_size > 1000


@patch("subprocess.run")
def test_create_cinematic_motion_clip(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0)

    img = tmp_path / "dummy.jpg"
    img.write_bytes(b"dummy")
    out_mp4 = tmp_path / "motion.mp4"

    res = create_cinematic_motion_clip(
        image_path=img,
        output_mp4=out_mp4,
        duration=3.0,
        fps=30
    )

    assert res == out_mp4
    assert mock_run.called
    cmd = mock_run.call_args[0][0]
    assert "ffmpeg" in cmd
    assert "zoompan" in "".join(cmd)
