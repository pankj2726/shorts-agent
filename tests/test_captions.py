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


# ─── Caption validation ──────────────────────────────────────────────────────

def _vtt(tmp_path, body, name="caps.vtt"):
    f = tmp_path / name
    f.write_text(body, encoding="utf-8")
    return f


def test_valid_synchronized_captions_pass(tmp_path):
    f = _vtt(tmp_path, VTT)                       # last cue ends at 1.10s
    r = captions.validate_captions(f, audio_duration_s=1.2)
    assert r["passed"] is True
    assert r["sync_checked"] is True
    assert r["cues"] == 3
    assert r["last_cue_end_s"] == 1.1


def test_caption_ending_within_tolerance_passes(tmp_path):
    f = _vtt(tmp_path, VTT)                       # ends 1.10s
    r = captions.validate_captions(f, audio_duration_s=1.0)   # 0.10s overrun
    assert r["passed"] is True
    assert r["sync_checked"] is True


def test_caption_ending_exactly_at_tolerance_passes(tmp_path):
    f = _vtt(tmp_path, VTT)
    r = captions.validate_captions(f, audio_duration_s=0.10, tolerance_s=1.0)
    assert r["passed"] is True


def test_caption_extending_beyond_tolerance_fails(tmp_path):
    f = _vtt(tmp_path, VTT)                       # ends 1.10s
    r = captions.validate_captions(f, audio_duration_s=0.05)  # 1.05s overrun
    assert r["passed"] is False
    assert "past the end of the audio" in r["reason"]


def test_the_documented_scenario_30s_audio_58s_captions_fails(tmp_path):
    body = "WEBVTT\n\n00:00:01.000 --> 00:00:58.000\nstill talking\n"
    r = captions.validate_captions(_vtt(tmp_path, body), audio_duration_s=30.0)
    assert r["passed"] is False
    assert "28.000s" in r["reason"]


def test_negative_start_timestamp_fails(tmp_path):
    body = "WEBVTT\n\n-00:00:01.000 --> 00:00:03.000\nnope\n"
    r = captions.validate_captions(_vtt(tmp_path, body), audio_duration_s=5.0)
    assert r["passed"] is False


def test_end_before_start_fails(tmp_path):
    body = "WEBVTT\n\n00:00:06.000 --> 00:00:03.000\nbackwards\n"
    r = captions.validate_captions(_vtt(tmp_path, body), audio_duration_s=10.0)
    assert r["passed"] is False
    assert "ends at or before it starts" in r["reason"]


def test_end_equal_to_start_fails(tmp_path):
    body = "WEBVTT\n\n00:00:03.000 --> 00:00:03.000\nzero length\n"
    r = captions.validate_captions(_vtt(tmp_path, body), audio_duration_s=10.0)
    assert r["passed"] is False


def test_malformed_timestamp_fails(tmp_path):
    body = "WEBVTT\n\n00:00:01 --> 00:00:03.000\nno millis\n"
    r = captions.validate_captions(_vtt(tmp_path, body), audio_duration_s=10.0)
    assert r["passed"] is False
    assert "malformed" in r["reason"]


def test_malformed_timestamp_is_not_silently_read_as_zero(tmp_path):
    """
    _parse_vtt_timestamp() returns 0 for a format it does not recognise, which
    would make a broken cue look like a valid one at time zero. The validator
    must reject it instead.
    """
    body = "WEBVTT\n\nbanana --> 00:00:03.000\ngarbage\n"
    r = captions.validate_captions(_vtt(tmp_path, body), audio_duration_s=10.0)
    assert r["passed"] is False
    assert r["cues"] == 0


def test_out_of_order_cues_fail(tmp_path):
    body = (
        "WEBVTT\n\n"
        "00:00:05.000 --> 00:00:06.000\nlater first\n\n"
        "00:00:01.000 --> 00:00:02.000\nearlier second\n"
    )
    r = captions.validate_captions(_vtt(tmp_path, body), audio_duration_s=10.0)
    assert r["passed"] is False
    assert "must not go backwards" in r["reason"]


def test_legitimate_overlapping_cues_pass(tmp_path):
    """Word-level kinetic captions overlap by design; overlap is not an error."""
    body = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.000\nHello everyone.\n\n"
        "00:00:02.500 --> 00:00:04.000\nWelcome back.\n"
    )
    r = captions.validate_captions(_vtt(tmp_path, body), audio_duration_s=5.0)
    assert r["passed"] is True
    assert r["cues"] == 2


def test_empty_caption_file_fails(tmp_path):
    r = captions.validate_captions(_vtt(tmp_path, ""), audio_duration_s=1.0)
    assert r["passed"] is False
    assert "empty" in r["reason"]


def test_file_without_cues_fails(tmp_path):
    r = captions.validate_captions(_vtt(tmp_path, "WEBVTT\n\n"), audio_duration_s=1.0)
    assert r["passed"] is False
    assert "no caption cues" in r["reason"]


def test_missing_caption_file_fails(tmp_path):
    r = captions.validate_captions(tmp_path / "absent.vtt", audio_duration_s=1.0)
    assert r["passed"] is False
    assert "does not exist" in r["reason"]


def test_missing_webvtt_header_fails(tmp_path):
    r = captions.validate_captions(
        _vtt(tmp_path, "00:00:01.000 --> 00:00:02.000\nno header\n"),
        audio_duration_s=5.0,
    )
    assert r["passed"] is False
    assert "WebVTT header" in r["reason"]


def test_missing_audio_duration_fails_when_sync_required(tmp_path):
    r = captions.validate_captions(_vtt(tmp_path, VTT), audio_duration_s=None)
    assert r["passed"] is False
    assert r["sync_checked"] is False
    assert "could not be checked" in r["reason"]


def test_missing_audio_duration_is_reported_not_faked_when_sync_optional(tmp_path):
    r = captions.validate_captions(
        _vtt(tmp_path, VTT), audio_duration_s=None, require_sync=False
    )
    assert r["passed"] is True
    assert r["sync_checked"] is False          # never claims it checked
    assert "synchronization not checked" in r["reason"]


def test_non_numeric_audio_duration_fails(tmp_path):
    r = captions.validate_captions(_vtt(tmp_path, VTT), audio_duration_s="thirty")
    assert r["passed"] is False


def test_zero_or_negative_audio_duration_fails(tmp_path):
    assert captions.validate_captions(_vtt(tmp_path, VTT), 0)["passed"] is False
    assert captions.validate_captions(_vtt(tmp_path, VTT), -3.0)["passed"] is False


def test_ass_file_is_refused_rather_than_falsely_passed(tmp_path):
    r = captions.validate_captions(_vtt(tmp_path, "[Events]\n", "caps.ass"), 5.0)
    assert r["passed"] is False
    assert "WebVTT only" in r["reason"]


def test_two_part_timestamp_without_hours_parses(tmp_path):
    body = "WEBVTT\n\n00:01.000 --> 00:03.000\nshort form\n"
    r = captions.validate_captions(_vtt(tmp_path, body), audio_duration_s=5.0)
    assert r["passed"] is True
    assert r["last_cue_end_s"] == 3.0


def test_cue_with_position_settings_still_parses(tmp_path):
    body = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000 position:10% align:start\nhi\n"
    r = captions.validate_captions(_vtt(tmp_path, body), audio_duration_s=5.0)
    assert r["passed"] is True


# ─── Rendering integration ───────────────────────────────────────────────────

def _render_inputs(tmp_path, vtt_body, duration_s=1.2):
    """Minimal stand-ins so render_short can be driven without ffmpeg."""
    from agent.modules import render

    video = tmp_path / "normalized.mp4"
    video.write_bytes(b"\x00" * 32)
    vtt = _vtt(tmp_path, vtt_body)
    return render, video, vtt


def test_invalid_captions_block_rendering(tmp_path, monkeypatch):
    """The core requirement: bad captions must never reach ffmpeg."""
    from agent.modules import render

    calls = []
    monkeypatch.setattr(
        render, "_ffmpeg_burn_captions",
        lambda *a, **k: calls.append(a) or None,
    )
    video = tmp_path / "normalized.mp4"
    video.write_bytes(b"\x00" * 32)
    out = tmp_path / "final.mp4"
    bad = _vtt(tmp_path, "WEBVTT\n\n00:00:01.000 --> 00:00:58.000\nrunaway\n")

    with pytest.raises(RuntimeError) as exc:
        render._burn_or_export(video, str(bad), out, duration_s=1.2, video_id="v1")

    assert "caption validation failed" in str(exc.value)
    assert calls == [], "ffmpeg must not be invoked for invalid captions"
    assert not out.exists()


def test_valid_captions_reach_the_renderer(tmp_path, monkeypatch):
    from agent.modules import render

    calls = []
    monkeypatch.setattr(
        render, "_ffmpeg_burn_captions",
        lambda v, p, o: calls.append((v, p, o)),
    )
    video = tmp_path / "normalized.mp4"
    video.write_bytes(b"\x00" * 32)
    out = tmp_path / "final.mp4"

    render._burn_or_export(video, str(_vtt(tmp_path, VTT)), out,
                           duration_s=1.2, video_id="v1")

    assert len(calls) == 1


def test_no_captions_requested_preserves_existing_behaviour(tmp_path, monkeypatch):
    from agent.modules import render

    monkeypatch.setattr(
        render, "_ffmpeg_burn_captions",
        lambda *a, **k: pytest.fail("must not burn captions when none requested"),
    )
    video = tmp_path / "normalized.mp4"
    video.write_bytes(b"\x00" * 32)
    out = tmp_path / "final.mp4"

    render._burn_or_export(video, None, out, duration_s=1.2, video_id="v1")

    assert out.exists()          # the copy-through path still works


def test_requested_but_missing_caption_file_is_an_error(tmp_path):
    """A caller that asked for subtitles must not get a silent no-caption video."""
    from agent.modules import render

    video = tmp_path / "normalized.mp4"
    video.write_bytes(b"\x00" * 32)
    out = tmp_path / "final.mp4"

    with pytest.raises(RuntimeError) as exc:
        render._burn_or_export(video, str(tmp_path / "absent.vtt"), out,
                               duration_s=1.2, video_id="v1")
    assert "does not exist" in str(exc.value)
    assert not out.exists()
