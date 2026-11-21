"""
Telegram delivery shutdown.

Delivery is permanently disabled by project policy. These tests pin the two
properties that make that meaningful:

* Nothing reaches api.telegram.org — not video, not text, not buttons. Each
  test asserts on the requests actually attempted, because a guard that logged
  a warning and sent anyway would pass any check that only read the log.
* No environment variable can reverse it. The refusal is unconditional in
  code, so there is no configuration to flip. The tests set every variable
  that used to matter and assert delivery still does not happen.
"""
from __future__ import annotations

import pytest

from agent.modules import notify


@pytest.fixture(autouse=True)
def _reset():
    notify._blocked_sends.clear()
    notify._blocked_videos.clear()
    yield
    notify._blocked_sends.clear()
    notify._blocked_videos.clear()


@pytest.fixture
def attempts(monkeypatch):
    """Capture every HTTP request notify() would make."""
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
def configured(monkeypatch):
    """Telegram fully configured, and every legacy switch turned on."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("WEBHOOK_URL", "https://example.invalid/hook")
    monkeypatch.setenv(notify.VIDEO_DELIVERY_ENV, "true")
    return True


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "short.mp4"
    path.write_bytes(b"x" * 1024)
    return str(path)


def _telegram_calls(sent):
    return [u for u in sent if "api.telegram.org" in u]


# ─── Nothing is delivered ────────────────────────────────────────────────────

def test_no_text_is_sent(attempts, configured):
    notify.notify("PIPELINE_ONLINE", "status text")
    assert _telegram_calls(attempts) == []


def test_no_video_is_sent(attempts, configured, video):
    notify.notify("RENDER_DONE", "status text", video_path=video)
    assert _telegram_calls(attempts) == []


def test_no_button_prompt_is_sent(attempts, configured):
    notify.notify("RENDER_DONE", "approve?",
                  buttons=[[("Approve", "approve:1"), ("Reject", "reject:1")]])
    assert _telegram_calls(attempts) == []


def test_refusals_are_recorded(attempts, configured, video):
    notify.notify("RENDER_DONE", "text", video_path=video)
    notify.notify("PIPELINE_ONLINE", "text")

    assert notify.blocked_sends() == ["RENDER_DONE", "PIPELINE_ONLINE"]
    # The video switch is on in this fixture, so the video guard let the path
    # through and the total shutdown stopped it. Recording it as a video block
    # would misattribute which control fired.
    assert notify.blocked_videos() == []


def test_both_controls_record_when_the_video_switch_is_off(attempts, monkeypatch, video):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.delenv(notify.VIDEO_DELIVERY_ENV, raising=False)

    notify.notify("RENDER_DONE", "text", video_path=video)

    assert notify.blocked_videos() == [video]
    assert notify.blocked_sends() == ["RENDER_DONE"]
    assert _telegram_calls(attempts) == []


def test_delivery_is_reported_as_disabled():
    assert notify.telegram_delivery_enabled() is False


def test_no_configuration_variable_can_re_enable_delivery(attempts, monkeypatch, video):
    """
    The refusal is unconditional in code. Every variable that used to gate
    delivery is set to its most permissive value and delivery still stops.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv(notify.VIDEO_DELIVERY_ENV, "true")
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("NOTIFY_ENABLED", "true")
    monkeypatch.setenv("PAUSE_PUBLISHING", "false")

    notify.notify("RENDER_DONE", "text", video_path=video,
                  buttons=[[("Approve", "approve:1")]])

    assert _telegram_calls(attempts) == []


def test_send_attempted_even_without_credentials_is_refused(attempts, video, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    notify.notify("RENDER_DONE", "text", video_path=video)

    assert _telegram_calls(attempts) == []
    assert notify.blocked_sends() == ["RENDER_DONE"]


# ─── The video guard is retained ─────────────────────────────────────────────

def test_video_guard_still_fails_closed(monkeypatch, video):
    """
    Preserved even though total shutdown makes it unreachable for delivery: it
    is the control that would apply if sending were ever re-opened.
    """
    monkeypatch.delenv(notify.VIDEO_DELIVERY_ENV, raising=False)
    assert notify._guard_video_delivery(video) is None
    assert notify.blocked_videos() == [video]


def test_video_guard_passes_through_only_when_explicitly_enabled(monkeypatch, video):
    monkeypatch.setenv(notify.VIDEO_DELIVERY_ENV, "true")
    assert notify._guard_video_delivery(video) == video


def test_video_guard_still_refuses_a_blank_value(monkeypatch, video):
    """A blank injected by CI must not re-enable it."""
    monkeypatch.setenv(notify.VIDEO_DELIVERY_ENV, "")
    assert notify._guard_video_delivery(video) is None


# ─── Non-Telegram paths are untouched ────────────────────────────────────────

def test_webhook_is_not_telegram_and_still_works(attempts, monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://example.invalid/hook")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    notify.notify("PIPELINE_ONLINE", "status text")

    assert [u for u in attempts if "example.invalid" in u]
    assert _telegram_calls(attempts) == []


# ─── Never raises ────────────────────────────────────────────────────────────

def test_notify_does_not_raise(attempts, configured, video):
    notify.notify("RENDER_DONE", "text", video_path=video,
                  buttons=[[("Approve", "approve:1")]])


def test_notify_does_not_raise_when_the_network_fails(monkeypatch, video):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("WEBHOOK_URL", "https://example.invalid/hook")
    monkeypatch.setattr(notify, "_rate_limit", lambda: None)

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(notify.requests, "post", boom)

    notify.notify("RENDER_DONE", "text", video_path=video)


# ─── Approval cannot be obtained through a dead channel ──────────────────────

def test_approval_waits_fail_closed_immediately():
    """
    Approval had exactly one channel. Polling it for two hours and then
    proceeding on timeout would be the opposite of an approval gate, so the
    wait now refuses at once.
    """
    import time

    from agent.main import _wait_for_approval

    started = time.monotonic()
    with pytest.raises(RuntimeError) as exc:
        _wait_for_approval(1, "/tmp/clip.mp4", {"title": "t", "hook": "h"})

    assert "approval unavailable" in str(exc.value)
    assert time.monotonic() - started < 1.0, "the wait did not fail closed"


def test_smoke_test_cannot_reach_telegram(attempts, configured):
    """The smoke path notifies on start; that must not leave the process."""
    notify.notify("PIPELINE_ONLINE", "Smoke test started", level="info")
    assert _telegram_calls(attempts) == []


# ─── No credential leakage ───────────────────────────────────────────────────

def test_refusal_log_carries_no_token(attempts, monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "leakable-token-value-77")

    with caplog.at_level("WARNING", logger="agent.modules.notify"):
        notify.notify("PIPELINE_ONLINE", "text")

    assert "leakable-token-value-77" not in caplog.text
    assert "api.telegram.org" not in caplog.text
