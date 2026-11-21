"""
Publishing must be impossible without a valid, specific, unexpired approval.

This is the regression suite for a hole found in review: approval used to be a
branch in run_pipeline(), so APPROVAL_REQUIRED=false fell straight through to
the YouTube upload, and PAUSE_PUBLISHING — which defaults to false — was the
only thing behind it. An environment variable was the entire approval
mechanism.

The tests below assert the structural property that replaced it: upload_short
consults the approvals table and no environment variable can substitute for a
row in it. The two env vars that used to decide this are set to their most
permissive values throughout, on purpose. If a future change reintroduces a
bypass, these fail rather than silently allowing publication.

No test contacts YouTube. The upload path is stubbed at the point where it
would build a client, so a refusal is proven by the stub never being reached.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from agent.modules import approvals, db, publish

IDENTITY = "operator@example.com"
TOKEN = "test-approval-token-not-a-real-secret"
OTHER_IDENTITY = "intruder@example.com"


# ─── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def state(tmp_path, monkeypatch):
    """An isolated SQLite state store, and a rendered file to approve."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state" / "db.sqlite")
    video_file = tmp_path / "clip.mp4"
    video_file.write_bytes(b"pretend this is an mp4")

    conn = db.get_conn()
    conn.execute(
        "INSERT INTO videos (id, title, render_path) VALUES (?, ?, ?)",
        (1, "A short about something", str(video_file)),
    )
    conn.commit()
    conn.close()
    return video_file


@pytest.fixture
def permissive_env(monkeypatch):
    """
    Every env var that historically could switch approval off, set permissively.

    This is the whole point of the suite: publication must stay blocked despite
    them. Kept as a named fixture so no test can quietly omit it.
    """
    monkeypatch.setenv("APPROVAL_REQUIRED", "false")
    monkeypatch.setenv("PAUSE_PUBLISHING", "false")


@pytest.fixture
def configured(monkeypatch):
    """An operator who is actually allowed to approve."""
    monkeypatch.setenv("SHORTS_APPROVER_ALLOWLIST", IDENTITY)
    monkeypatch.setenv("SHORTS_APPROVAL_TOKEN", TOKEN)


@pytest.fixture
def no_upload(monkeypatch):
    """
    Positive control on the YouTube boundary.

    Replaces the client builder with something that fails the test if reached,
    so "refused" means "never attempted" rather than "attempted and failed".
    """
    def boom(*_a, **_k):
        raise AssertionError("YouTube client was constructed — publishing was attempted")

    monkeypatch.setattr(publish, "_get_youtube_service", boom)
    # Belt and braces: the same guarantee if a future refactor moves the client
    # construction, by failing at the import site the upload actually uses.
    monkeypatch.setitem(
        __import__("sys").modules, "googleapiclient.http",
        type("_NoMedia", (), {"MediaFileUpload": staticmethod(boom)}),
    )
    return boom


@pytest.fixture
def uploadable(monkeypatch):
    """
    A stand-in for googleapiclient.http, which is not installed here.

    Only used by the happy path, to prove the gate is passable. The refusal
    tests use no_upload instead and never get this far.
    """
    monkeypatch.setitem(
        __import__("sys").modules, "googleapiclient.http",
        type("_Media", (), {"MediaFileUpload": staticmethod(
            lambda *a, **k: object())}),
    )


@pytest.fixture
def sent(monkeypatch):
    """Records notifications, so refusals can be asserted as reported, not silent."""
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(publish, "notify", lambda kind, msg, **_k: events.append((kind, msg)))
    return events


def _approve(video_id: int = 1, path: str | None = None, *, state_file) -> str:
    """Grant a real approval through the module's own grant path."""
    digest = approvals.file_hash(str(path or state_file))
    ok, reason = approvals.grant(video_id, IDENTITY, TOKEN, digest)
    assert ok, f"test setup could not grant approval: {reason}"
    return digest


# ─── the five cases called for in review ─────────────────────────────────────


def test_publishing_is_refused_when_approval_required_is_false(
        state, permissive_env, no_upload, sent):
    """APPROVAL_REQUIRED=false must not reach the upload. This was the hole."""
    assert publish.upload_short(
        video_id=1, video_path=str(state), title="t", description="d", tags=[]
    ) is None
    assert any(kind == "UPLOAD_BLOCKED" for kind, _ in sent)


def test_publishing_is_refused_when_pause_publishing_is_false(
        state, permissive_env, configured, no_upload, sent):
    """
    PAUSE_PUBLISHING=false is not permission to publish.

    Even with an approver configured and no approval granted, the kill switch
    being off changes nothing.
    """
    assert publish.upload_short(
        video_id=1, video_path=str(state), title="t", description="d", tags=[]
    ) is None
    assert any("no approval record" in msg for _, msg in sent)


def test_missing_approval_is_refused(state, configured, no_upload):
    allowed, reason = approvals.verify(1, approvals.file_hash(str(state)))
    assert allowed is False
    assert "no approval record" in reason


def test_expired_approval_is_refused(state, configured, no_upload):
    digest = _approve(state_file=state)
    # Move the stored expiry into the past rather than faking the clock:
    # approvals.time IS the time module, so patching it replaces the global and
    # recurses. Same comparison branch, no monkeypatching of stdlib.
    # Age both timestamps together. Moving only expires_at would make
    # granted_at > expires_at, which is a malformed record and trips the
    # consistency check instead — a real expired approval is well-formed.
    now = datetime.now(UTC)
    conn = db.get_conn()
    conn.execute(
        "UPDATE approvals SET granted_at=?, expires_at=? WHERE video_id=?",
        ((now - timedelta(hours=48)).isoformat(),
         (now - timedelta(hours=24)).isoformat(), 1),
    )
    conn.commit()
    conn.close()

    allowed, reason = approvals.verify(1, digest)
    assert allowed is False
    assert "expired" in reason
    assert publish.upload_short(
        video_id=1, video_path=str(state), title="t", description="d", tags=[]
    ) is None


def test_approval_for_a_different_video_is_refused(state, configured, no_upload):
    _approve(video_id=1, state_file=state)

    allowed, reason = approvals.verify(2, approvals.file_hash(str(state)))
    assert allowed is False
    assert "no approval record" in reason


# ─── the rest of the fail-closed surface ─────────────────────────────────────


def test_granted_approval_allows_the_upload(state, configured, uploadable, monkeypatch):
    """The gate must be passable, or it proves nothing but that nothing works."""
    digest = _approve(state_file=state)

    allowed, reason = approvals.verify(1, digest)
    assert allowed is True
    assert IDENTITY in reason

    calls: list[dict] = []

    class _FakeVideos:
        def insert(self, **kw):
            calls.append(kw)
            return _FakeRequest()

    class _FakeRequest:
        def next_chunk(self):
            return None, {"id": "YTID123"}

    class _FakeSvc:
        def videos(self):
            return _FakeVideos()

    monkeypatch.setattr(publish, "_get_youtube_service", lambda: _FakeSvc())
    monkeypatch.setattr(publish.quota, "check", lambda _action: True)
    monkeypatch.setattr(publish.quota, "consume", lambda _action: None)

    yt_id = publish.upload_short(
        video_id=1, video_path=str(state), title="t", description="d", tags=[]
    )
    assert yt_id == "YTID123"
    assert len(calls) == 1


def test_revoked_approval_is_refused(state, configured, no_upload):
    digest = _approve(state_file=state)
    assert approvals.revoke(1) == 1

    allowed, reason = approvals.verify(1, digest)
    assert allowed is False
    assert "revoked" in reason


def test_rejection_does_not_authorise_publishing(state, configured, no_upload):
    digest = approvals.file_hash(str(state))
    ok, _ = approvals.grant(1, IDENTITY, TOKEN, digest, decision=approvals.REJECTED)
    assert ok

    allowed, reason = approvals.verify(1, digest)
    assert allowed is False
    assert "not approved" in reason


def test_changed_file_after_approval_is_refused(state, configured, no_upload, tmp_path):
    """An approval binds to bytes, not to a video_id."""
    _approve(state_file=state)
    state.write_bytes(b"a re-render that nobody approved")

    allowed, reason = approvals.verify(1, approvals.file_hash(str(state)))
    assert allowed is False
    assert "does not match the file" in reason


def test_unreadable_file_cannot_be_published(state, configured, no_upload, tmp_path):
    """A missing hash must not read as 'no constraint'."""
    _approve(state_file=state)
    missing = tmp_path / "gone.mp4"

    assert approvals.file_hash(str(missing)) == ""
    allowed, reason = approvals.verify(1, "")
    assert allowed is False
    assert "no content hash supplied" in reason


def test_latest_decision_wins(state, configured, no_upload):
    """A later rejection overrides an earlier approval, and vice versa."""
    digest = approvals.file_hash(str(state))
    approvals.grant(1, IDENTITY, TOKEN, digest)
    approvals.grant(1, IDENTITY, TOKEN, digest, decision=approvals.REJECTED)
    assert approvals.verify(1, digest)[0] is False

    approvals.grant(1, IDENTITY, TOKEN, digest)
    assert approvals.verify(1, digest)[0] is True


def test_unparseable_expiry_is_refused(state, configured, no_upload):
    _approve(state_file=state)
    conn = db.get_conn()
    conn.execute("UPDATE approvals SET expires_at=? WHERE video_id=?", ("not-a-date", 1))
    conn.commit()
    conn.close()

    assert approvals.verify(1, approvals.file_hash(str(state)))[0] is False


def test_granted_after_expiry_is_refused(state, configured, no_upload):
    """A row whose own timestamps contradict each other is not evidence."""
    digest = _approve(state_file=state)
    now = datetime.now(UTC)
    conn = db.get_conn()
    conn.execute(
        "UPDATE approvals SET granted_at=?, expires_at=? WHERE video_id=?",
        (now.isoformat(), (now - timedelta(hours=1)).isoformat(), 1),
    )
    conn.commit()
    conn.close()

    allowed, reason = approvals.verify(1, digest)
    assert allowed is False
    assert "inconsistent" in reason


def test_unreadable_approval_store_is_refused(state, configured, no_upload, monkeypatch):
    def explode():
        raise RuntimeError("state store unreachable")

    monkeypatch.setattr(db, "get_conn", explode)
    allowed, reason = approvals.verify(1, "anyhash")
    assert allowed is False
    assert "refusing" in reason


# ─── granting is itself gated ────────────────────────────────────────────────


def test_nothing_can_be_approved_without_an_allowlist(state):
    """The default configuration: no approvers, so no approvals, so no publishing."""
    ok, reason = approvals.grant(1, IDENTITY, TOKEN, "deadbeef")
    assert ok is False
    assert "no approvers are configured" in reason


def test_nothing_can_be_approved_without_a_token(state, monkeypatch):
    monkeypatch.setenv("SHORTS_APPROVER_ALLOWLIST", IDENTITY)
    ok, reason = approvals.grant(1, IDENTITY, TOKEN, "deadbeef")
    assert ok is False
    assert "no approval token is configured" in reason


def test_identity_off_the_allowlist_is_refused(state, configured):
    ok, reason = approvals.grant(1, OTHER_IDENTITY, TOKEN, "deadbeef")
    assert ok is False
    assert "not permitted to approve" in reason
    # The allowlist itself must not be disclosed in the refusal.
    assert IDENTITY not in reason


def test_blank_identity_is_refused(state, configured):
    assert approvals.grant(1, "   ", TOKEN, "deadbeef")[0] is False


def test_wrong_token_is_refused(state, configured):
    ok, reason = approvals.grant(1, IDENTITY, "wrong-token", "deadbeef")
    assert ok is False
    assert "token rejected" in reason


def test_empty_token_is_refused(state, configured):
    assert approvals.grant(1, IDENTITY, "", "deadbeef")[0] is False


def test_approval_without_a_content_hash_is_refused(state, configured):
    ok, reason = approvals.grant(1, IDENTITY, TOKEN, "")
    assert ok is False
    assert "content hash" in reason


def test_revoke_on_an_unapproved_video_is_a_no_op(state, configured):
    assert approvals.revoke(1) == 0


# ─── configuration cannot become a bypass ────────────────────────────────────


def test_ttl_is_clamped_so_it_cannot_become_permanent(monkeypatch):
    monkeypatch.setenv("SHORTS_APPROVAL_TTL_S", str(10 * 365 * 24 * 3600))
    assert approvals.ttl_seconds() == approvals.MAX_TTL_S

    monkeypatch.setenv("SHORTS_APPROVAL_TTL_S", "0")
    assert approvals.ttl_seconds() == 60


def test_verify_consults_no_env_var_that_could_disable_it(
        state, monkeypatch, no_upload):
    """
    Set every plausible switch to its most permissive value at once.

    If a future change lets an env var satisfy the approval requirement, this
    is what catches it.
    """
    for name in ("APPROVAL_REQUIRED", "PAUSE_PUBLISHING", "SHORTS_SKIP_APPROVAL",
                 "SHORTS_APPROVAL_OPTIONAL", "SHORTS_AUTO_APPROVE",
                 "SHORTS_TELEGRAM_VIDEO_ENABLED"):
        monkeypatch.setenv(name, "true")

    allowed, reason = approvals.verify(1, approvals.file_hash(str(state)))
    assert allowed is False
    assert "no approval record" in reason


def test_approval_check_precedes_the_idempotency_shortcut(
        state, configured, no_upload, sent, monkeypatch):
    """
    An already-uploaded video is still refused once its approval lapses.

    The order matters: returning the cached yt_video_id would let the caller
    mark the run 'done' and report success for a video whose approval was
    withdrawn.
    """
    conn = db.get_conn()
    conn.execute("UPDATE videos SET yt_video_id=? WHERE id=?", ("OLD_YT_ID", 1))
    conn.commit()
    conn.close()

    assert publish.upload_short(
        video_id=1, video_path=str(state), title="t", description="d", tags=[]
    ) is None
    assert any(kind == "UPLOAD_BLOCKED" for kind, _ in sent)


def test_file_hash_is_stable_and_content_sensitive(state, tmp_path):
    digest = approvals.file_hash(str(state))
    assert len(digest) == 64
    assert digest == approvals.file_hash(str(state))
    assert digest == hashlib.sha256(b"pretend this is an mp4").hexdigest()

    other = tmp_path / "other.mp4"
    other.write_bytes(b"different bytes")
    assert approvals.file_hash(str(other)) != digest


def test_approvals_table_exists_in_both_dialects(tmp_path, monkeypatch):
    from agent.modules import db_pg

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "db.sqlite")
    conn = db.get_conn()
    try:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        conn.close()
    assert "approvals" in names
    assert "approvals" in db_pg.EXPECTED_TABLES
    assert "shorts.approvals" in db_pg.SCHEMA_DDL


# ─── preflight must not hide the publishing state behind READY ───────────────


def _preflight_approval():
    """The publish_approval section of a preflight run."""
    import importlib
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    preflight = importlib.import_module("preflight")
    return preflight._check_publish_approval()


def test_preflight_reports_that_nothing_can_be_published_by_default():
    """
    The default configuration must say so plainly.

    "RESULT: READY" otherwise reads as "ready to publish". The pipeline can
    research, script, render and pass QA while publication stays impossible,
    and an operator should be able to see that without reading the code.
    """
    report = _preflight_approval()
    assert report["can_grant"] is False
    assert report["approvers"] == 0
    assert report["token_configured"] is False
    assert "nothing can be published" in report["note"]


def test_preflight_reports_a_partially_configured_allowlist(monkeypatch):
    """Half a configuration is still no configuration."""
    monkeypatch.setenv("SHORTS_APPROVER_ALLOWLIST", IDENTITY)
    report = _preflight_approval()
    assert report["can_grant"] is False
    assert report["approvers"] == 1
    assert "SHORTS_APPROVAL_TOKEN" in report["note"]


def test_preflight_reports_a_fully_configured_approver(monkeypatch):
    monkeypatch.setenv("SHORTS_APPROVER_ALLOWLIST", f"{IDENTITY}, second@example.com")
    monkeypatch.setenv("SHORTS_APPROVAL_TOKEN", TOKEN)
    report = _preflight_approval()
    assert report["can_grant"] is True
    assert report["approvers"] == 2
    assert "approve_video.py" in report["note"]


def test_approval_readiness_never_blocks_preflight(monkeypatch):
    """
    It must stay informational.

    Making it blocking would fail CI on the default configuration, where no
    approvers are set — which is the intended safe state, not a fault.
    """
    import importlib
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    preflight = importlib.import_module("preflight")

    report = preflight.run()
    assert "publish_approval" in report
    assert report["publish_approval"]["can_grant"] is False
    for item in report["blocking"]:
        assert "approval" not in item.lower()


# ─── a refused publish must not be recorded as a successful run ──────────────


def test_refused_publish_is_not_recorded_as_done(monkeypatch, tmp_path):
    """
    The pipeline used to mark the run 'done' with yt_id=None.

    upload_short() correctly refused and returned None, but run_pipeline fell
    straight through to UPDATE runs SET status='done'. A run that produced a
    video nobody approved therefore read as a success — the same misreporting
    the approval gate fixed, inverted.

    This drives the real run_pipeline() end to end with every stage stubbed to
    succeed, so it exercises the shipped code path rather than a copy of it.
    """
    from agent import main as agent_main
    from agent.modules import (
        assets,
        db,
        notify,
        qa,
        render,
        research,
        strategy,
        tts,
    )
    from agent.modules import (
        script as script_mod,
    )

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "db.sqlite")
    recorded: list[tuple[str, tuple]] = []

    class _Conn:
        lastrowid = 7

        def execute(self, sql, params=()):
            recorded.append((sql, params))
            return self

        def commit(self):
            pass

        def close(self):
            pass

        def fetchone(self):
            return None

    monkeypatch.setattr(db, "get_conn", lambda: _Conn())

    # Every stage succeeds, so the pipeline reaches the publish step.
    monkeypatch.setattr(research, "run_research", lambda: {"topics": ["x"]})
    monkeypatch.setattr(strategy, "run_strategy", lambda _r: {"idea": "an idea"})
    monkeypatch.setattr(script_mod, "run_script",
                        lambda _i: {"video_id": 42, "title": "T", "description": "D",
                                    "script": "S", "tags": ["a"], "scene_list": []})
    monkeypatch.setattr(tts, "generate_voiceover",
                        lambda *a, **k: {"audio_path": str(tmp_path / "v.mp3"),
                                         "duration": 30.0})
    monkeypatch.setattr(assets, "fetch_stock_clips", lambda *a, **k: [])
    monkeypatch.setattr(assets, "pick_music", lambda *a, **k: None)
    monkeypatch.setattr(render, "render_short",
                        lambda *a, **k: {"video_path": str(tmp_path / "clip.mp4"),
                                         "duration": 30.0})
    monkeypatch.setattr(qa, "check_render", lambda _r: {"passed": True, "reason": ""})

    # The refusal under test: publishing is attempted and denied.
    refused: dict = {}

    def _refuse(**kwargs):
        refused.update(kwargs)

    monkeypatch.setattr(publish, "upload_short", _refuse)

    calls: list[str] = []
    monkeypatch.setattr(notify, "notify",
                        lambda kind, *_a, **_k: calls.append(kind))

    # No approval configured, so the real upload_short would refuse. Stubbed
    # above to the same effect; the assertion below is on what gets recorded.
    monkeypatch.setenv("APPROVAL_REQUIRED", "false")

    agent_main.run_pipeline()  # must not raise, must not exit

    assert refused, "upload_short was never attempted"
    assert refused["video_id"] == 42

    updates = [(sql, params) for sql, params in recorded if "UPDATE runs" in sql]
    assert updates, "the run was never finalised"

    final_sql, final_params = updates[-1]
    assert "'blocked'" in final_sql, (
        f"a refused publish was recorded as something other than blocked: {final_sql}"
    )
    assert "'done'" not in final_sql
    assert "refused" in str(final_params[1]).lower()

    # The operator must be told, not left to find it in the runs table.
    assert "PIPELINE_BLOCKED" in calls
    # And nothing may be routed to Telegram for manual scheduling.
    assert "RENDER_DONE" not in calls


def test_pipeline_source_marks_a_refused_publish_blocked_not_done():
    """
    Guard the source itself.

    The behavioural test above can only exercise the branch in isolation; this
    pins the actual code path in run_pipeline so the misreporting cannot be
    reintroduced without a test failing.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "agent" / "main.py").read_text()

    # The refusal branch must set blocked, and must come before the done update.
    blocked_at = src.find("status='blocked', finished_at=?")
    done_at = src.find("UPDATE runs SET status='done'")
    assert blocked_at != -1, "no blocked update in run_pipeline"
    assert done_at != -1, "no done update in run_pipeline"
    assert blocked_at < done_at, "blocked branch must precede the done branch"

    # The done update must be unreachable when yt_id is falsy.
    assert "if not yt_id:" in src
    assert "return" in src[blocked_at:done_at], "refusal branch must return"


def test_pipeline_no_longer_routes_a_refusal_to_telegram():
    """
    The old fallback sent the MP4 to Telegram for manual scheduling.

    Telegram delivery is permanently disabled, so that call could never reach
    anyone — keeping it would mean routing around a missing approval mechanism
    rather than respecting it. It is no longer called from the pipeline.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "agent" / "main.py").read_text()

    assert "send_for_manual_review(" not in src.replace(
        "# The previous fallback here was send_for_manual_review(), which", "")
    assert "from agent.modules.publish import upload_short, send_for_manual_review" not in src
