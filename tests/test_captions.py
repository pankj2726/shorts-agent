"""
Caption transcription workflow.

The renderer burns in subtitles from a VTT that edge-tts emits as a by-product;
when that is missing, captions.transcribe_audio_groq() recovers timings from the
audio. This file covers that recovery path and the VTT -> ASS conversion.

requests.post is mocked. No audio is sent anywhere and no Groq call is made.
"""
from __future__ import annotations

import pytest

from agent.modules import captions


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def audio(tmp_path):
    """transcribe_audio_groq() opens the file before posting, so it must exist."""
    f = tmp_path / "audio.mp3"
    f.write_bytes(b"\x00" * 64)
    return f


WORDS = {
    "words": [
        {"word": "Nine", "start": 0.0, "end": 0.42},
        {"word": "to", "start": 0.42, "end": 0.61},
        {"word": "five", "start": 0.61, "end": 1.10},
    ]
}


def test_transcription_returns_word_level_timings_in_milliseconds(monkeypatch, audio):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    captured = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        captured.update(url=url, headers=headers, data=data)
        return _Resp(WORDS)

    monkeypatch.setattr(captions.requests, "post", fake_post)

    result = captions.transcribe_audio_groq(audio)

    assert [w["word"] for w in result] == ["Nine", "to", "five"]
    assert result[0] == {"word": "Nine", "start_ms": 0, "end_ms": 420}
    assert result[2]["end_ms"] == 1100


def test_transcription_targets_the_documented_groq_endpoint(monkeypatch, audio):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    captured = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        captured.update(url=url, headers=headers, data=data)
        return _Resp(WORDS)

    monkeypatch.setattr(captions.requests, "post", fake_post)
    captions.transcribe_audio_groq(audio)

    assert captured["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert captured["data"]["model"] == "whisper-large-v3"
    # Word-level timestamps are what kinetic captions need.
    assert captured["data"]["timestamp_granularities[]"] == "word"
    assert captured["data"]["response_format"] == "verbose_json"


def test_the_api_key_is_sent_as_a_bearer_token_not_logged(monkeypatch, caplog, audio):
    monkeypatch.setenv("GROQ_API_KEY", "super-secret-key-value")
    seen = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        seen["headers"] = headers
        return _Resp(WORDS)

    monkeypatch.setattr(captions.requests, "post", fake_post)
    with caplog.at_level("DEBUG"):
        captions.transcribe_audio_groq(audio)

    assert seen["headers"]["Authorization"] == "Bearer super-secret-key-value"
    assert "super-secret-key-value" not in caplog.text


def test_missing_api_key_does_not_raise(monkeypatch, audio):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert captions.transcribe_audio_groq(audio) == []


def test_provider_error_is_contained(monkeypatch, audio):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    def boom(*a, **k):
        raise RuntimeError("HTTP 500 from Groq")

    monkeypatch.setattr(captions.requests, "post", boom)
    assert captions.transcribe_audio_groq(audio) == []


def test_empty_transcription_is_not_an_error(monkeypatch, audio):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(captions.requests, "post", lambda *a, **k: _Resp({"words": []}))
    assert captions.transcribe_audio_groq(audio) == []


def test_missing_word_fields_do_not_crash(monkeypatch, audio):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        captions.requests, "post",
        lambda *a, **k: _Resp({"words": [{"word": "ok"}]}),
    )
    result = captions.transcribe_audio_groq(audio)
    assert result == [{"word": "ok", "start_ms": 0, "end_ms": 0}]


# ─── VTT -> ASS conversion ───────────────────────────────────────────────────

VTT = """WEBVTT

00:00:00.000 --> 00:00:00.420
Nine

00:00:00.420 --> 00:00:00.610
to

00:00:00.610 --> 00:00:01.100
five
"""


def test_vtt_to_ass_produces_a_valid_ass_file(tmp_path):
    vtt = tmp_path / "caps.vtt"
    vtt.write_text(VTT)
    out = tmp_path / "caps.ass"

    captions.vtt_to_ass(vtt, out)

    text = out.read_text()
    assert "[Script Info]" in text
    assert "[Events]" in text
    assert "Nine" in text
    assert out.exists()


def test_ass_timestamps_are_in_centisecond_format(tmp_path):
    vtt = tmp_path / "caps.vtt"
    vtt.write_text(VTT)
    out = tmp_path / "caps.ass"

    captions.vtt_to_ass(vtt, out)
    text = out.read_text()
    # ASS uses H:MM:SS.cc, not the VTT H:MM:SS.mmm form.
    assert "0:00:00.00" in text
    assert "-->" not in text


# ─── Validation ──────────────────────────────────────────────────────────────

def test_validate_captions_accepts_a_well_formed_file(tmp_path):
    vtt = tmp_path / "caps.vtt"
    vtt.write_text(VTT)
    result = captions.validate_captions(vtt, audio_duration_s=1.2)
    assert result["passed"] is True


def test_validate_captions_does_not_actually_check_audio_sync(tmp_path):
    """
    PINS A KNOWN GAP. The docstring promises "captions match the audio length",
    but the implementation only checks existence and a 20-byte minimum, then
    returns "Captions valid and in sync". Captions twice as long as the audio
    pass validation. Asserted as current behaviour so a future fix is visible.
    """
    vtt = tmp_path / "caps.vtt"
    vtt.write_text(VTT)          # 1.1s of captions
    result = captions.validate_captions(vtt, audio_duration_s=0.2)
    assert result["passed"] is True, (
        "validate_captions now checks duration sync - update this test"
    )


def test_validate_captions_flags_a_missing_file(tmp_path):
    result = captions.validate_captions(tmp_path / "absent.vtt", audio_duration_s=1.0)
    assert result["passed"] is False
    assert "does not exist" in result["reason"]


def test_validate_captions_rejects_a_near_empty_file(tmp_path):
    vtt = tmp_path / "caps.vtt"
    vtt.write_text("tiny")
    result = captions.validate_captions(vtt, audio_duration_s=1.0)
    assert result["passed"] is False
