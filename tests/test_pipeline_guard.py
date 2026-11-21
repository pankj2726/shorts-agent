"""
Pipeline approval gate.

Approval has no working channel: Telegram was the only one and its delivery is
permanently disabled. Without a gate the scheduled pipeline ran research,
strategy, script, TTS, assets, render and QA, then failed at the approval step
and recorded `status='failed'` — which reads as a pipeline fault rather than
what it is, a missing approval mechanism.

The property that matters here is ordering: the gate must fire *before* any
expensive stage. So the tests stub every work function to fail the test if it
is reached, rather than merely asserting on the final status.
"""
from __future__ import annotations

import pytest

from agent import main as agent_main


class _FakeConn:
    """Records SQL so tests can assert what was written without a database."""

    def __init__(self, sink):
        self.sink = sink
        self.lastrowid = 1

    def execute(self, sql, params=()):
        self.sink.append((sql, params))
        return self

    def commit(self):
        pass

    def close(self):
        pass


@pytest.fixture
def sink(monkeypatch):
    from agent.modules import db

    recorded = []
    monkeypatch.setattr(db, "get_conn", lambda: _FakeConn(recorded))
    return recorded


@pytest.fixture
def no_work(monkeypatch):
    """Every expensive stage fails the test if the gate lets it be reached."""
    from agent.modules import (
        assets,
        notify,
        publish,
        qa,
        render,
        research,
        script,
        strategy,
        tts,
    )

    def forbidden(name):
        def _raise(*a, **k):
            raise AssertionError(f"{name} ran despite the approval gate")
        return _raise

    monkeypatch.setattr(research, "run_research", forbidden("research"))
    monkeypatch.setattr(strategy, "run_strategy", forbidden("strategy"))
    monkeypatch.setattr(script, "run_script", forbidden("script"))
    monkeypatch.setattr(tts, "generate_voiceover", forbidden("tts"))
    monkeypatch.setattr(assets, "fetch_stock_clips", forbidden("fetch_stock_clips"))
    monkeypatch.setattr(assets, "pick_music", forbidden("pick_music"))
    monkeypatch.setattr(render, "render_short", forbidden("render"))
    monkeypatch.setattr(qa, "check_render", forbidden("qa"))
    monkeypatch.setattr(publish, "upload_short", forbidden("upload_short"))
    monkeypatch.setattr(publish, "send_for_manual_review",
                        forbidden("send_for_manual_review"))
    monkeypatch.setattr(notify, "notify", lambda *a, **k: None)
    return True


# ─── The gate predicate ──────────────────────────────────────────────────────

def test_no_approval_channel_is_available():
    available, detail = agent_main.approval_channel_available()
    assert available is False
    assert "permanently disabled" in detail


def test_gate_blocks_by_default(monkeypatch):
    monkeypatch.delenv("APPROVAL_REQUIRED", raising=False)
    proceed, reason = agent_main.approval_gate()
    assert proceed is False
    assert "approval channel unavailable" in reason


@pytest.mark.parametrize("value", ["false", "0", "no", "off"])
def test_gate_allows_when_approval_is_not_required(monkeypatch, value):
    monkeypatch.setenv("APPROVAL_REQUIRED", value)
    proceed, reason = agent_main.approval_gate()
    assert proceed is True
    assert reason == "approval not required"


def test_gate_follows_the_notification_module_not_a_constant(monkeypatch):
    """
    Derived from notify.telegram_delivery_enabled(), so wiring up a real
    channel updates this instead of leaving a stale hardcoded False.
    """
    from agent.modules import notify

    monkeypatch.setattr(notify, "telegram_delivery_enabled", lambda: True)
    available, detail = agent_main.approval_channel_available()
    assert available is True
    assert detail == "telegram"
    assert agent_main.approval_gate()[0] is True


# ─── Ordering: nothing expensive runs ────────────────────────────────────────

def test_pipeline_stops_before_any_work(sink, no_work, monkeypatch):
    monkeypatch.delenv("APPROVAL_REQUIRED", raising=False)

    agent_main.run_pipeline()  # must not raise

    # Only the blocked-run insert may have been written.
    assert len(sink) == 1, f"unexpected statements: {sink}"
    sql, params = sink[0]
    assert "INSERT INTO runs" in sql
    assert "'blocked'" in sql
    assert "blocked: approval channel unavailable" in params[0]


def test_blocked_run_is_not_recorded_as_a_failure(sink, no_work, monkeypatch):
    """A missing approval mechanism must not be indistinguishable from a fault."""
    monkeypatch.delenv("APPROVAL_REQUIRED", raising=False)

    agent_main.run_pipeline()

    assert all("'failed'" not in sql for sql, _ in sink)
    assert all("status='failed'" not in sql for sql, _ in sink)


def test_gate_does_not_bypass_approval(sink, no_work, monkeypatch):
    """
    Blocking must not become a shortcut to publishing. With approval required
    and no channel, nothing may be uploaded or sent for review.
    """
    monkeypatch.delenv("APPROVAL_REQUIRED", raising=False)
    agent_main.run_pipeline()
    assert all("videos" not in sql for sql, _ in sink)


def test_gate_does_not_re_enable_telegram(sink, no_work, monkeypatch):
    from agent.modules import notify

    monkeypatch.delenv("APPROVAL_REQUIRED", raising=False)
    agent_main.run_pipeline()

    assert notify.telegram_delivery_enabled() is False


def test_pipeline_proceeds_when_approval_is_not_required(monkeypatch):
    """
    The gate must be the only thing stopping the run: with approval off, the
    pipeline continues into the work stages (which this test stubs to halt it).
    """
    from agent.modules import db, research

    monkeypatch.setenv("APPROVAL_REQUIRED", "false")
    monkeypatch.setattr(db, "get_conn", lambda: _FakeConn([]))

    reached = []

    class _Halt(Exception):
        pass

    def halt(*a, **k):
        reached.append("research")
        raise _Halt

    monkeypatch.setattr(research, "run_research", halt)

    # run_pipeline catches every exception, records a failed run and exits 1,
    # so the halt surfaces as SystemExit. What proves the gate let the run
    # through is that research was reached at all.
    with pytest.raises(SystemExit):
        agent_main.run_pipeline()

    assert reached == ["research"]


# ─── The approval wait itself still fails closed ─────────────────────────────

def test_wait_for_approval_still_refuses():
    import time

    started = time.monotonic()
    with pytest.raises(RuntimeError) as exc:
        agent_main._wait_for_approval(1, "/tmp/clip.mp4", {"title": "t", "hook": "h"})

    assert "approval unavailable" in str(exc.value)
    assert "permanently disabled" in str(exc.value)
    assert time.monotonic() - started < 1.0


def test_wait_for_approval_does_not_claim_success_without_an_implementation(monkeypatch):
    """
    If a channel is ever reported available but no implementation exists for it,
    that must be an error rather than a silent pass.
    """
    from agent.modules import notify

    monkeypatch.setattr(notify, "telegram_delivery_enabled", lambda: True)

    with pytest.raises(RuntimeError) as exc:
        agent_main._wait_for_approval(1, "/tmp/clip.mp4", {"title": "t", "hook": "h"})

    assert "no implementation" in str(exc.value)


# ─── Schema documents the new status ─────────────────────────────────────────

def test_both_dialects_document_the_blocked_status():
    """Comment-only change; no migration and no destructive change required."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for rel in ("agent/modules/db.py", "agent/modules/db_pg.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "blocked" in text, f"{rel} does not document the blocked status"
