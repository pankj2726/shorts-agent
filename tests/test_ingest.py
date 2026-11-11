"""
test_ingest.py — Unit Tests for Multi-Modal Reference & Visual Ingestion Engine.
"""

from pathlib import Path
from agent.modules.ingest import MultiModalIngestEngine


def test_extract_youtube_video_id():
    test_urls = [
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ]
    for url, expected_id in test_urls:
        assert MultiModalIngestEngine.extract_youtube_video_id(url) == expected_id

    assert MultiModalIngestEngine.extract_youtube_video_id("https://google.com") is None
    assert MultiModalIngestEngine.extract_youtube_video_id("invalid-url") is None
    assert MultiModalIngestEngine.extract_youtube_video_id("") is None


def test_ingest_youtube_reference():
    res = MultiModalIngestEngine.ingest_youtube_reference("https://youtube.com/shorts/dQw4w9WgXcQ")
    assert res["success"] is True
    assert res["video_id"] == "dQw4w9WgXcQ"
    assert "url" in res
    assert "hook_formula" in res

    bad_res = MultiModalIngestEngine.ingest_youtube_reference("invalid-url")
    assert bad_res["success"] is False


def test_ingest_brand_asset(tmp_path: Path):
    dummy_img = tmp_path / "brand_dark.png"
    dummy_img.write_bytes(b"dummy image bytes")

    res = MultiModalIngestEngine.ingest_brand_asset(dummy_img)
    assert res["success"] is True
    assert res["file_name"] == "brand_dark.png"
    assert res["format"] == "PNG"
    assert len(res["recommended_color_palette"]) >= 3

    missing_res = MultiModalIngestEngine.ingest_brand_asset("non_existent_file.jpg")
    assert missing_res["success"] is False


def test_fuse_inputs(tmp_path: Path):
    dummy_img = tmp_path / "warm_logo.jpg"
    dummy_img.write_bytes(b"12345")

    fused = MultiModalIngestEngine.fuse_inputs(
        topic="Quick Windows Clipboard Trick",
        reference_url="https://youtube.com/shorts/dQw4w9WgXcQ",
        brand_asset_path=str(dummy_img),
        target_niche="tech productivity",
    )

    assert fused["core_topic"] == "Quick Windows Clipboard Trick"
    assert "reference_video" in fused
    assert "brand_asset" in fused
    assert len(fused["palette"]) > 0
