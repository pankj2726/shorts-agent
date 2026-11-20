"""
Bounded stock-clip downloads.

_download() used to stream straight into the destination path with only a
60-second read-inactivity timeout: no total deadline, no byte cap, no atomic
completion. A slow-but-alive server could hold the pipeline indefinitely, and a
failure left a truncated file that the idempotency check would then treat as a
valid clip on every later run.

All responses here are faked. No network access is required.
"""
from __future__ import annotations

import time

import pytest

from agent.modules import assets


class _FakeResponse:
    """Minimal stand-in for a streaming requests response."""

    def __init__(self, chunks, headers=None, status=200):
        self._chunks = chunks
        self.headers = headers or {}
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=65536):
        yield from self._chunks


@pytest.fixture
def dest(tmp_path):
    return tmp_path / "clip.mp4"


def _patch(monkeypatch, response):
    monkeypatch.setattr(assets._SESSION, "get", lambda *a, **k: response)


# ─── Happy path ──────────────────────────────────────────────────────────────

def test_download_below_the_limit_succeeds(dest, monkeypatch):
    _patch(monkeypatch, _FakeResponse([b"a" * 1024, b"b" * 1024]))
    assets._download("https://example.test/clip.mp4", dest)
    assert dest.read_bytes() == b"a" * 1024 + b"b" * 1024


def test_successful_download_is_atomically_promoted(dest, monkeypatch):
    """No .part file may survive, and dest must appear only when complete."""
    _patch(monkeypatch, _FakeResponse([b"x" * 10]))
    assets._download("https://example.test/clip.mp4", dest)
    assert dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_existing_file_is_left_untouched(dest, monkeypatch):
    dest.write_bytes(b"already here")
    _patch(monkeypatch, _FakeResponse([b"new"]))
    assets._download("https://example.test/clip.mp4", dest)
    assert dest.read_bytes() == b"already here"


# ─── Size limits ─────────────────────────────────────────────────────────────

def test_download_exceeding_the_streamed_limit_aborts(dest, monkeypatch):
    monkeypatch.setenv("SHORTS_MAX_CLIP_BYTES", "2000")
    _patch(monkeypatch, _FakeResponse([b"z" * 1500, b"z" * 1500]))
    with pytest.raises(RuntimeError, match="byte limit"):
        assets._download("https://example.test/clip.mp4", dest)
    assert not dest.exists()


def test_oversize_content_length_is_rejected_before_streaming(dest, monkeypatch):
    monkeypatch.setenv("SHORTS_MAX_CLIP_BYTES", "100")
    called = []

    def fake_iter(chunk_size=65536):
        called.append(True)
        yield b"y" * 50

    resp = _FakeResponse([], headers={"Content-Length": "999999"})
    resp.iter_content = fake_iter
    _patch(monkeypatch, resp)

    with pytest.raises(RuntimeError, match="Content-Length"):
        assets._download("https://example.test/clip.mp4", dest)
    assert called == [], "must not stream a body already known to be too large"


def test_misleading_content_length_cannot_bypass_the_limit(dest, monkeypatch):
    """
    A server may understate Content-Length. The streamed byte count is the
    authority, so a small header must not let a large body through.
    """
    monkeypatch.setenv("SHORTS_MAX_CLIP_BYTES", "2000")
    _patch(monkeypatch, _FakeResponse(
        [b"q" * 1500, b"q" * 1500], headers={"Content-Length": "10"}))
    with pytest.raises(RuntimeError, match="byte limit"):
        assets._download("https://example.test/clip.mp4", dest)


def test_missing_content_length_is_still_enforced(dest, monkeypatch):
    monkeypatch.setenv("SHORTS_MAX_CLIP_BYTES", "1000")
    _patch(monkeypatch, _FakeResponse([b"w" * 800, b"w" * 800]))
    with pytest.raises(RuntimeError, match="byte limit"):
        assets._download("https://example.test/clip.mp4", dest)


def test_unparseable_content_length_falls_back_to_byte_counting(dest, monkeypatch):
    monkeypatch.setenv("SHORTS_MAX_CLIP_BYTES", "5000")
    _patch(monkeypatch, _FakeResponse(
        [b"v" * 100], headers={"Content-Length": "not-a-number"}))
    assets._download("https://example.test/clip.mp4", dest)
    assert dest.read_bytes() == b"v" * 100


# ─── Deadline ────────────────────────────────────────────────────────────────

def test_overall_deadline_aborts_a_slow_but_alive_stream(dest, monkeypatch):
    """
    The regression this whole change exists for: chunks keep arriving, so the
    60s read-inactivity timeout never fires, but total elapsed time is bounded.
    """
    monkeypatch.setenv("SHORTS_DOWNLOAD_DEADLINE_S", "0.05")

    def slow_iter(chunk_size=65536):
        for _ in range(200):
            time.sleep(0.02)          # each chunk well inside the read timeout
            yield b"s" * 64

    resp = _FakeResponse([])
    resp.iter_content = slow_iter
    _patch(monkeypatch, resp)

    with pytest.raises(RuntimeError, match="deadline"):
        assets._download("https://example.test/clip.mp4", dest)
    assert not dest.exists()


def test_read_inactivity_timeout_is_still_passed_to_the_session(dest, monkeypatch):
    """The socket timeout must survive; it catches a fully stalled connection."""
    seen = {}

    def fake_get(url, stream=None, timeout=None):
        seen["timeout"] = timeout
        seen["stream"] = stream
        return _FakeResponse([b"t"])

    monkeypatch.setattr(assets._SESSION, "get", fake_get)
    assets._download("https://example.test/clip.mp4", dest)
    assert seen["timeout"] == 60
    assert seen["stream"] is True


# ─── Cleanup ─────────────────────────────────────────────────────────────────

def test_partial_file_is_removed_after_a_size_failure(dest, monkeypatch):
    monkeypatch.setenv("SHORTS_MAX_CLIP_BYTES", "100")
    _patch(monkeypatch, _FakeResponse([b"p" * 500]))
    with pytest.raises(RuntimeError):
        assets._download("https://example.test/clip.mp4", dest)
    part = dest.with_name(dest.name + ".part")
    assert not part.exists(), "partial file must not survive"
    assert not dest.exists()


def test_partial_file_is_removed_after_a_network_error(dest, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("connection reset mid-transfer")

    monkeypatch.setattr(assets._SESSION, "get", boom)
    with pytest.raises(ConnectionError):
        assets._download("https://example.test/clip.mp4", dest)
    assert not dest.with_name(dest.name + ".part").exists()


def test_existing_valid_destination_survives_a_failed_refresh(tmp_path, monkeypatch):
    """
    A failed download must not delete a clip that is already good. dest is never
    opened for writing, so this holds even if a valid file is present.
    """
    dest = tmp_path / "clip.mp4"
    dest.write_bytes(b"good existing footage")
    monkeypatch.setenv("SHORTS_MAX_CLIP_BYTES", "10")

    # Bypass the idempotency early-return to exercise the failure path.
    monkeypatch.setattr(assets.Path, "exists", lambda self: False)
    _patch(monkeypatch, _FakeResponse([b"n" * 500]))

    with pytest.raises(RuntimeError):
        assets._download("https://example.test/clip.mp4", dest)
    assert dest.read_bytes() == b"good existing footage"


def test_http_error_does_not_create_a_file(dest, monkeypatch):
    _patch(monkeypatch, _FakeResponse([], status=500))
    with pytest.raises(RuntimeError):
        assets._download("https://example.test/clip.mp4", dest)
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


# ─── Configuration ───────────────────────────────────────────────────────────

def test_unset_configuration_uses_the_safe_defaults():
    import os

    os.environ.pop("SHORTS_MAX_CLIP_BYTES", None)
    os.environ.pop("SHORTS_DOWNLOAD_DEADLINE_S", None)
    assert assets._positive_setting(
        "SHORTS_MAX_CLIP_BYTES", assets.MAX_CLIP_BYTES_DEFAULT, float
    ) == assets.MAX_CLIP_BYTES_DEFAULT


@pytest.mark.parametrize("bad", ["0", "-1", "-0.5", "abc", "", "  "])
def test_invalid_configuration_fails_safe_rather_than_disabling(bad, monkeypatch):
    """
    A zero, negative or unparseable value must raise. Silently falling back to
    the default would ignore an operator typo, and treating 0 as unlimited would
    reintroduce the unbounded download this guards against.
    """
    if bad.strip() == "":
        monkeypatch.setenv("SHORTS_MAX_CLIP_BYTES", bad)
        # blank is treated as unset, which is the documented behaviour
        assert assets._positive_setting(
            "SHORTS_MAX_CLIP_BYTES", assets.MAX_CLIP_BYTES_DEFAULT, float
        ) == assets.MAX_CLIP_BYTES_DEFAULT
        return
    monkeypatch.setenv("SHORTS_MAX_CLIP_BYTES", bad)
    with pytest.raises(ValueError):
        assets._positive_setting(
            "SHORTS_MAX_CLIP_BYTES", assets.MAX_CLIP_BYTES_DEFAULT, float)


def test_invalid_config_raises_from_the_download_itself(dest, monkeypatch):
    monkeypatch.setenv("SHORTS_MAX_CLIP_BYTES", "0")
    with pytest.raises(ValueError):
        assets._download("https://example.test/clip.mp4", dest)
    assert not dest.exists()


def test_valid_custom_config_is_honoured(dest, monkeypatch):
    monkeypatch.setenv("SHORTS_MAX_CLIP_BYTES", "4096")
    monkeypatch.setenv("SHORTS_DOWNLOAD_DEADLINE_S", "30")
    _patch(monkeypatch, _FakeResponse([b"ok" * 10]))
    assets._download("https://example.test/clip.mp4", dest)
    assert dest.exists()
