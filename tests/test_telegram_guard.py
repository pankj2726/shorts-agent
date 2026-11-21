"""
Telegram video delivery guard.

Video delivery is paused by project directive. These tests verify the refusal
happens at the server boundary — the one function that can reach the sendVideo
endpoint — rather than depending on callers to remember not to pass a path.

The critical assertion in each "blocked" test is that no request is made to
sendVideo. A guard that logged a warning and sent the video anyway would pass
every check that only looked at the log.
"""
from __future__ import annotations

import pytest

from agent.modules import notify


@pytest.fixture(autouse=True)
def _reset_guard():
    notify._blocked_videos.clear()
    yield
    notify._blocked_videos.clear()


@pytest.fixture
def telegram(monkeypatch):
    """Telegram configured, rate limiter disabled, requests captured."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(notify, "_rate_limit", lambda: None)
    sent = []

    class _Resp:
        status_code = 200
        text = "ok"

        def json(self):
            return {"ok": True}

    def fake_post(url, data=None, files=None, timeout=None, **kw):
        sent.append(url)
        return _Resp()

    monkeypatch.setattr(notify.requests, "post", fake_post)
    return sent


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "short.mp4"
    path.write_bytes(b"x" * 1024)
    return str(path)


def _endpoints(sent):
    return [u.rsplit("/", 1)[-1] for u in sent]


# ─── Fails closed ────────────────────────────────────────────────────────────

def test_video_is_blocked_by_default(telegram, video, monkeypatch):
    monkeypatch.delenv(notify.VIDEO_DELIVERY_ENV, raising=False)

    notify.notify("TEST_EVENT", "status text", video_path=video)

    assert "sendVideo" not in _endpoints(telegram)
    assert notify.blocked_videos() == [video]


@pytest.mark.parametrize("value", ["", "   ", "false", "False", "0", "no", "off",
                                   "maybe", "TRUEISH", "enabled"])
def test_only_an_explicit_true_enables_delivery(telegram, video, monkeypatch, value):
    """A blank from CI, or anything unrecognised, must leave delivery paused."""
    monkeypatch.setenv(notify.VIDEO_DELIVERY_ENV, value)

    notify.notify("TEST_EVENT", "status text", video_path=video)

    assert "sendVideo" not in _endpoints(telegram)
    assert notify.video_delivery_enabled() is False


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_explicit_true_re_enables_delivery(telegram, video, monkeypatch, value):
    monkeypatch.setenv(notify.VIDEO_DELIVERY_ENV, value)

    notify.notify("TEST_EVENT", "status text", video_path=video)

    assert "sendVideo" in _endpoints(telegram)
    assert notify.blocked_videos() == []


# ─── Text is unaffected ──────────────────────────────────────────────────────

def test_text_notification_still_sent_while_video_is_paused(telegram, video, monkeypatch):
    monkeypatch.delenv(notify.VIDEO_DELIVERY_ENV, raising=False)

    notify.notify("TEST_EVENT", "status text", video_path=video)

    assert "sendMessage" in _endpoints(telegram)


def test_plain_notification_is_untouched_by_the_guard(telegram, monkeypatch):
    monkeypatch.delenv(notify.VIDEO_DELIVERY_ENV, raising=False)

    notify.notify("TEST_EVENT", "status text")

    assert "sendMessage" in _endpoints(telegram)
    assert notify.blocked_videos() == []


def test_guard_applies_even_without_telegram_configured(video, monkeypatch):
    """The refusal must be recorded even when no send could have happened."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv(notify.VIDEO_DELIVERY_ENV, raising=False)

    notify.notify("TEST_EVENT", "status text", video_path=video)

    assert notify.blocked_videos() == [video]


# ─── Never raises ────────────────────────────────────────────────────────────

def test_guard_does_not_raise_on_a_missing_file(monkeypatch):
    monkeypatch.delenv(notify.VIDEO_DELIVERY_ENV, raising=False)

    notify.notify("TEST_EVENT", "text", video_path="/nonexistent/clip.mp4")

    assert notify.blocked_videos() == ["/nonexistent/clip.mp4"]


def test_guard_does_not_raise_when_send_fails(monkeypatch, video):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(notify, "_rate_limit", lambda: None)
    monkeypatch.delenv(notify.VIDEO_DELIVERY_ENV, raising=False)

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(notify.requests, "post", boom)

    notify.notify("TEST_EVENT", "text", video_path=video)  # must not raise


# ─── Interaction with the existing size limit ────────────────────────────────

def test_oversize_video_still_falls_back_to_text_when_enabled(
    telegram, tmp_path, monkeypatch,
):
    monkeypatch.setenv(notify.VIDEO_DELIVERY_ENV, "true")
    big = tmp_path / "big.mp4"
    big.write_bytes(b"x" * (51 * 1024 * 1024))

    notify.notify("TEST_EVENT", "text", video_path=str(big))

    assert "sendVideo" not in _endpoints(telegram)
    assert "sendMessage" in _endpoints(telegram)
    # Refused by the size limit, not by the pause, so it is not double-counted.
    assert notify.blocked_videos() == []


# ─── No credential leakage ───────────────────────────────────────────────────

def test_block_log_carries_no_token(telegram, video, monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "leakable-token-value-77")
    monkeypatch.delenv(notify.VIDEO_DELIVERY_ENV, raising=False)

    with caplog.at_level("WARNING", logger="agent.modules.notify"):
        notify.notify("TEST_EVENT", "text", video_path=video)

    assert "leakable-token-value-77" not in caplog.text
