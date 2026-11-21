"""
P4.1 — approval records must be authenticated, single-use and auditable.

The safeguard merged earlier made publishing depend on a row in `approvals`.
That closed the env-var bypass but left three holes, each confirmed by running
the code rather than by reading it:

1. Single-use. One approval authorised unlimited uploads: verify() returned
   True on the second, third and nth call for the same record.
2. Record authentication. Only the *grant call* was authenticated. The stored
   row itself was plain data, so anything able to write to the table — a
   mis-scoped credential, a restore from an old dump, a bad migration — could
   mint an approval that verify() would accept.
3. Audit trail. revoke() updated revoked_at in place, so the record of a
   revocation overwrote the thing it revoked, and neither grants nor refusals
   were logged at all.

These tests lock the replacements down. They drive the real approvals module
against a real SQLite store; nothing here re-implements the logic under test.
"""
from __future__ import annotations

import hashlib
import threading
from datetime import UTC, datetime, timedelta

import pytest

from agent.modules import approvals, db

IDENTITY = "operator@example.com"
TOKEN = "test-approval-token-not-a-real-secret"
SIGNING_KEY = "test-signing-key-not-a-real-secret"


@pytest.fixture
def state(tmp_path, monkeypatch):
    """An isolated SQLite store holding one video and its render file."""
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
def operator(monkeypatch):
    """A fully configured approver: identity, token and signing key."""
    monkeypatch.setenv("SHORTS_APPROVER_ALLOWLIST", IDENTITY)
    monkeypatch.setenv("SHORTS_APPROVAL_TOKEN", TOKEN)
    monkeypatch.setenv("SHORTS_APPROVAL_SIGNING_KEY", SIGNING_KEY)


@pytest.fixture
def approved(state, operator):
    """A granted, signed approval bound to the render file. Returns its hash."""
    digest = approvals.file_hash(str(state))
    ok, reason = approvals.grant(1, IDENTITY, TOKEN, digest)
    assert ok is True, reason
    return digest


def _raw(conn_sql: str, params: tuple = ()) -> None:
    conn = db.get_conn()
    conn.execute(conn_sql, params)
    conn.commit()
    conn.close()


# ─── 1. single-use ───────────────────────────────────────────────────────────


def test_an_approval_authorises_exactly_one_publication(approved):
    """The hole this exists to close: the same grant used to serve forever."""
    assert approvals.consume(1, approved, "upload_short")[0] is True
    ok, reason = approvals.consume(1, approved, "upload_short")
    assert ok is False
    assert "already been used" in reason


def test_a_spent_approval_stops_being_publishable_via_verify_too(approved):
    """consume() is the spend; verify() must then agree it is gone."""
    assert approvals.verify(1, approved)[0] is True
    assert approvals.consume(1, approved, "upload_short")[0] is True
    ok, reason = approvals.verify(1, approved)
    assert ok is False
    assert "already been used" in reason


def test_concurrent_publishers_cannot_share_one_approval(approved):
    """
    Exactly one of N racers may spend the record.

    The spend is a single UPDATE guarded by `consumed_at IS NULL`, so the
    database serialises it. Which refusal branch the losers hit does not
    matter; that only one succeeds does.
    """
    results: list[bool] = []
    lock = threading.Lock()

    def race() -> None:
        ok, _ = approvals.consume(1, approved, "upload_short")
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=race) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 6
    assert sum(1 for r in results if r) == 1, f"expected exactly one winner: {results}"


def test_verify_is_read_only_and_does_not_spend(approved):
    """
    A status check must not destroy the thing it reports on.

    An approval interface will poll this; if it consumed, the first poll would
    publish-or-ruin the video it was merely asked about.
    """
    for _ in range(20):
        assert approvals.verify(1, approved)[0] is True
    assert approvals.consume(1, approved, "upload_short")[0] is True


def test_verify_leaves_no_audit_rows(approved):
    """Read-only means no writes at all, including no audit noise."""
    before = len(approvals.audit_trail(1))
    for _ in range(20):
        approvals.verify(1, approved)
    assert len(approvals.audit_trail(1)) == before


def test_a_fresh_approval_is_needed_after_a_publication(approved, operator):
    """Spending is not a bug to work around: re-granting is the intended path."""
    assert approvals.consume(1, approved, "upload_short")[0] is True
    assert approvals.consume(1, approved, "upload_short")[0] is False

    ok, _ = approvals.grant(1, IDENTITY, TOKEN, approved)
    assert ok is True
    assert approvals.consume(1, approved, "upload_short")[0] is True


# ─── 2. record authentication ────────────────────────────────────────────────


def test_a_row_inserted_straight_into_the_table_is_refused(state, operator):
    """
    Database write access alone must not be enough to approve a video.

    This is the attack the signature exists to stop: every field is plausible,
    the decision is 'approved', the hash matches — but nobody signed it.
    """
    digest = approvals.file_hash(str(state))
    now = datetime.now(UTC)
    _raw(
        "INSERT INTO approvals "
        "(video_id, decision, approved_by, channel, content_hash, granted_at, expires_at) "
        "VALUES (?, 'approved', ?, 'forged', ?, ?, ?)",
        (1, IDENTITY, digest, now.isoformat(), (now + timedelta(hours=1)).isoformat()),
    )
    ok, reason = approvals.verify(1, digest)
    assert ok is False
    assert "unsigned" in reason


def test_a_signature_from_the_wrong_key_is_refused(approved, monkeypatch):
    """Key rotation must invalidate outstanding records, not be ignored."""
    assert approvals.verify(1, approved)[0] is True
    monkeypatch.setenv("SHORTS_APPROVAL_SIGNING_KEY", "a-different-key")
    ok, reason = approvals.verify(1, approved)
    assert ok is False
    assert "signature" in reason


def test_tampering_with_a_signed_record_is_detected(approved):
    """
    The signature covers the content hash, so re-pointing an approval at
    different bytes is caught rather than trusted.
    """
    assert approvals.verify(1, approved)[0] is True
    _raw("UPDATE approvals SET content_hash=? WHERE video_id=1", ("a" * 64,))
    ok, reason = approvals.verify(1, "a" * 64)
    assert ok is False
    assert "signature" in reason


def test_extending_an_expiry_after_the_fact_is_detected(approved):
    """The signature covers the expiry epoch, so it cannot simply be edited."""
    assert approvals.verify(1, approved)[0] is True
    later = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    _raw("UPDATE approvals SET expires_at=? WHERE video_id=1", (later,))
    ok, reason = approvals.verify(1, approved)
    assert ok is False
    assert "signature" in reason


def test_flipping_a_rejection_into_an_approval_is_detected(state, operator):
    """Decision is signed too: a stored 'rejected' cannot be edited to pass."""
    digest = approvals.file_hash(str(state))
    ok, _ = approvals.grant(1, IDENTITY, TOKEN, digest, decision=approvals.REJECTED)
    assert ok is True
    _raw("UPDATE approvals SET decision='approved' WHERE video_id=1")
    ok, reason = approvals.verify(1, digest)
    assert ok is False
    assert "signature" in reason


def test_nothing_can_be_granted_without_a_signing_key(state, operator, monkeypatch):
    """A missing key refuses to grant rather than writing an unverifiable row."""
    monkeypatch.delenv("SHORTS_APPROVAL_SIGNING_KEY", raising=False)
    ok, reason = approvals.grant(1, IDENTITY, TOKEN, "deadbeef")
    assert ok is False
    assert "signing key" in reason


def test_a_signed_record_cannot_be_verified_without_the_key(approved, monkeypatch):
    """Losing the key fails closed: it cannot silently become permissive."""
    assert approvals.verify(1, approved)[0] is True
    monkeypatch.delenv("SHORTS_APPROVAL_SIGNING_KEY", raising=False)
    ok, reason = approvals.verify(1, approved)
    assert ok is False
    assert "signing key" in reason


def test_signing_key_defaults_to_unset(monkeypatch):
    """The fail-closed default: with nothing configured, nothing verifies."""
    monkeypatch.delenv("SHORTS_APPROVAL_SIGNING_KEY", raising=False)
    assert approvals.signing_key() == ""


def test_every_grant_carries_a_distinct_nonce(state, operator):
    """
    A nonce stops two identical approvals being interchangeable.

    Without one, a signature captured for video 1 could be replayed onto any
    row with the same field values.
    """
    rows = []
    for _ in range(3):
        assert approvals.grant(1, IDENTITY, TOKEN, "deadbeef")[0] is True
    conn = db.get_conn()
    rows = conn.execute("SELECT nonce, signature FROM approvals").fetchall()
    conn.close()
    assert len({r["nonce"] for r in rows}) == 3
    assert len({r["signature"] for r in rows}) == 3
    assert all(r["nonce"] for r in rows), "a nonce must never be blank"


def test_a_signature_binds_to_the_video_id(state, operator):
    """
    Copying a valid row onto another video must not work.

    The video id is signed, so the copy carries a signature that no longer
    matches the row it now sits in.
    """
    digest = approvals.file_hash(str(state))
    assert approvals.grant(1, IDENTITY, TOKEN, digest)[0] is True

    conn = db.get_conn()
    src = conn.execute("SELECT * FROM approvals WHERE video_id=1").fetchone()
    conn.execute(
        "INSERT INTO approvals "
        "(video_id, decision, approved_by, channel, content_hash, granted_at, "
        "expires_at, signature, nonce) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (2, src["decision"], src["approved_by"], src["channel"], src["content_hash"],
         src["granted_at"], src["expires_at"], src["signature"], src["nonce"]),
    )
    conn.commit()
    conn.close()

    ok, reason = approvals.verify(2, digest)
    assert ok is False
    assert "signature" in reason


def test_a_tampered_row_cannot_be_consumed_either(approved):
    """consume() applies the same policy — the spend must not be a back door."""
    _raw("UPDATE approvals SET content_hash=? WHERE video_id=1", ("b" * 64,))
    ok, reason = approvals.consume(1, "b" * 64, "upload_short")
    assert ok is False
    assert "signature" in reason


# ─── 3. audit trail ──────────────────────────────────────────────────────────


def test_granting_is_audited(approved):
    trail = approvals.audit_trail(1)
    assert [r["action"] for r in trail] == ["granted"]
    assert trail[0]["actor"] == IDENTITY


def test_a_refused_grant_is_audited(state, operator, monkeypatch):
    """
    A failed authorisation attempt leaves evidence.

    Someone probing for a valid approver identity should be visible afterwards,
    not indistinguishable from nothing having happened.
    """
    ok, _ = approvals.grant(1, "intruder@example.com", TOKEN, "deadbeef")
    assert ok is False
    trail = approvals.audit_trail(1)
    assert [r["action"] for r in trail] == ["refused"]
    assert "not permitted to approve" in trail[0]["reason"]


def test_consuming_and_a_replay_attempt_are_both_audited(approved):
    approvals.consume(1, approved, "upload_short")
    approvals.consume(1, approved, "upload_short")
    actions = [r["action"] for r in approvals.audit_trail(1)]
    assert actions == ["granted", "consumed", "refused"]


def test_revoking_is_audited_without_erasing_the_grant(approved):
    """
    The trail is append-only.

    revoke() still updates revoked_at in place, because that is what verify()
    must observe, but the fact that an approval was granted and then withdrawn
    has to survive that update.
    """
    assert approvals.revoke(1, by=IDENTITY) == 1
    actions = [r["action"] for r in approvals.audit_trail(1)]
    assert actions == ["granted", "revoked"]
    assert approvals.audit_trail(1)[1]["actor"] == IDENTITY


def test_audit_rows_are_never_rewritten(approved):
    """Append-only means the granted row keeps its original timestamp."""
    first = approvals.audit_trail(1)[0]
    approvals.revoke(1, by=IDENTITY)
    approvals.consume(1, approved, "upload_short")
    assert approvals.audit_trail(1)[0] == first


def test_a_missing_audit_table_is_an_empty_trail_not_a_crash(state):
    """The trail must not become a new way to take the approval path down."""
    _raw("DROP TABLE approval_audit")
    assert approvals.audit_trail(1) == []


def test_an_audit_failure_cannot_change_the_decision(approved, monkeypatch):
    """
    A broken audit write must neither crash the pipeline nor approve anything.

    The decision stands on its own; only the record is lost, and that is logged.
    """
    _raw("DROP TABLE approval_audit")
    assert approvals.consume(1, approved, "upload_short")[0] is True


# ─── 4. schema and dialect portability ───────────────────────────────────────


def test_existing_rows_survive_the_migration(tmp_path, monkeypatch):
    """
    The upgrade is additive: a pre-P4.1 database keeps its rows.

    Nothing is dropped or re-typed. Those legacy rows are unsigned and so are
    correctly refused, but they must still be present to be examined.
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state" / "db.sqlite")
    conn = db.get_conn()
    conn.execute("INSERT INTO videos (id, title) VALUES (1, 'legacy')")
    conn.execute(
        "INSERT INTO approvals (video_id, decision, approved_by, channel, "
        "content_hash, granted_at, expires_at) VALUES (1, 'approved', 'old@x.com', "
        "'cli', 'deadbeef', ?, ?)",
        (datetime.now(UTC).isoformat(),
         (datetime.now(UTC) + timedelta(hours=1)).isoformat()),
    )
    conn.commit()
    conn.close()

    # Re-opening migrates in place.
    conn = db.get_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(approvals)").fetchall()}
    row = conn.execute("SELECT approved_by FROM approvals").fetchone()
    conn.close()

    assert {"signature", "nonce", "consumed_at", "consumed_by"} <= cols
    assert row["approved_by"] == "old@x.com"


def test_postgres_ddl_is_additive_only():
    """The Postgres dialect must not rewrite or drop the approvals table."""
    from agent.modules import db_pg

    ddl = db_pg.SCHEMA_DDL
    assert "DROP TABLE" not in ddl
    assert ddl.count("ALTER TABLE shorts.approvals ADD COLUMN IF NOT EXISTS") == 4
    assert "shorts.approval_audit" in ddl
    assert "approval_audit" in db_pg.EXPECTED_TABLES


def test_signed_epochs_round_trip_through_stored_timestamps(approved):
    """
    The signature is over whole-second epochs, not the stored text.

    Postgres returns a datetime rendered in the session timezone while SQLite
    returns the exact string that was written, so signing the text would not
    verify across backends. This guards the choice by checking that a record
    granted and verified through the SQLite path still agrees with a signature
    recomputed from the stored timestamps.
    """
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM approvals WHERE video_id=1").fetchone()
    conn.close()

    expected = approvals._sign(
        row["video_id"], row["decision"], row["approved_by"], row["channel"],
        row["content_hash"],
        int(approvals._parse_iso(row["granted_at"])),
        int(approvals._parse_iso(row["expires_at"])),
        row["nonce"],
    )
    assert expected == row["signature"]


def test_file_hash_is_the_real_digest(state):
    """Sanity: the binding value is the sha256 of the actual bytes."""
    assert approvals.file_hash(str(state)) == hashlib.sha256(
        state.read_bytes()
    ).hexdigest()


@pytest.mark.parametrize(
    "offset",
    ["+00:00", "+05:30", "-08:00", "+13:00"],
)
def test_signature_verifies_regardless_of_stored_timezone(offset):
    """
    The signature must survive a backend that renders timestamps elsewhere.

    SQLite hands back the exact string that was written, but Postgres returns a
    TIMESTAMPTZ-aware datetime rendered in the session timezone — so on a
    Postgres deployment the stored text differs from what SQLite stored. That
    is why the signature covers whole-second epochs rather than the text. This
    is the guard on that choice, and it is the one assertion here that SQLite
    alone could not have caught: it exercises the offset handling directly.
    """
    from datetime import timezone

    utc_instant = datetime(2026, 11, 21, 10, 13, 39, tzinfo=UTC)
    same_instant_local = utc_instant.astimezone(
        timezone(timedelta(hours=int(offset[:3]), minutes=int(offset[0] + offset[4:])))
    )
    assert same_instant_local.isoformat().endswith(offset)

    assert approvals._parse_iso(same_instant_local.isoformat()) == (
        approvals._parse_iso(utc_instant.isoformat())
    )
    assert int(approvals._parse_iso(same_instant_local.isoformat())) == int(
        utc_instant.timestamp()
    )


def test_signature_survives_a_datetime_object_not_just_a_string():
    """
    psycopg returns datetime objects, not the strings SQLite returns.

    _parse_iso must accept both, or verification would fail on Postgres while
    passing on SQLite.
    """
    as_dt = datetime(2026, 11, 21, 10, 13, 39, tzinfo=UTC)
    as_str = as_dt.isoformat()
    assert approvals._parse_iso(as_dt) == approvals._parse_iso(as_str)

    # The property that makes int() safe in _sign: a whole-second timestamp
    # truncates without loss, so the signed value is identical whichever
    # backend produced it. A fractional part here would mean the two backends
    # could sign different numbers for the same instant.
    parsed = approvals._parse_iso(as_dt)
    assert float(parsed).is_integer(), parsed
    # Anchored against an independently computed epoch, so a change to
    # _parse_iso that shifted the value would fail rather than silently sign
    # something else.
    assert int(parsed) == int(as_dt.timestamp()) == 1795256019
