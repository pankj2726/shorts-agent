"""
approvals.py — the only authority on whether a video may be published.

This exists because of a real hole in the pipeline. Before it, approval was a
branch in run_pipeline():

    if env_bool("APPROVAL_REQUIRED", True):
        _wait_for_approval(...)
    upload_short(...)

Set APPROVAL_REQUIRED=false and control fell straight through to the YouTube
upload. An environment variable — settable by anyone with repo variable write
access, visible to every workflow — was the entire approval mechanism. The only
thing behind it was PAUSE_PUBLISHING, which defaults to false.

The fix is structural rather than another flag: publishing consults this table
and nothing else. APPROVAL_REQUIRED and PAUSE_PUBLISHING can both be set to
whatever anyone likes and publication still requires an approval record. That
is the property the tests lock down.

Fails closed on every axis:

    no record                  -> refuse
    expired                    -> refuse
    revoked                    -> refuse
    recorded for another video -> refuse
    decision is not 'approved' -> refuse
    content hash mismatch      -> refuse
    no approvers configured    -> refuse to even grant one
    clock nonsense             -> refuse

Until P4 ships a real approval interface there is deliberately no way for the
pipeline to approve its own output. Granting an approval requires an identity
on SHORTS_APPROVER_ALLOWLIST plus the matching SHORTS_APPROVAL_TOKEN, from a
process outside the pipeline (scripts/approve_video.py). With neither
configured — the default — nothing can be approved and nothing can publish.
"""
import contextlib
import hashlib
import hmac
import logging
import os
import secrets
import time
from datetime import UTC, datetime

from agent.modules import db
from agent.modules.config import env_int, env_list

logger = logging.getLogger(__name__)

# An approval is a short-lived authorisation for one specific render, not a
# standing licence. Long enough to review, short enough that a stale grant
# cannot be reused weeks later.
DEFAULT_TTL_S = 24 * 60 * 60
MAX_TTL_S = 7 * 24 * 60 * 60

APPROVED = "approved"
REJECTED = "rejected"


# --------------------------------------------------------------------------
# Granting
# --------------------------------------------------------------------------
def approvers() -> list[str]:
    """Identities allowed to approve. Empty by default: nobody may approve."""
    return [a.strip() for a in env_list("SHORTS_APPROVER_ALLOWLIST", []) if a.strip()]


def delivery_channels() -> list[str]:
    """
    Channels that can both deliver an approval request and receive a decision.

    This is the provider-neutral source of truth for whether approval is
    possible at all. It is deliberately empty: no such channel exists yet.

    Telegram is not in this list and must not be added. Its delivery is
    permanently disabled, and it was the implicit approval mechanism whose
    removal this whole design exists to enforce. Deriving availability from
    Telegram — even as a negative check — means anyone who re-enables delivery
    silently reopens the pipeline gate.

    P4 should append real channels here (web, Android) rather than teaching
    main.py about any particular provider.
    """
    return []


def ttl_seconds() -> int:
    """Approval lifetime, clamped so a huge value cannot make one permanent."""
    return max(60, min(env_int("SHORTS_APPROVAL_TTL_S", DEFAULT_TTL_S), MAX_TTL_S))


def file_hash(path: str) -> str:
    """
    SHA-256 of the rendered file, used to bind an approval to exact bytes.

    Returns an empty string when the file cannot be read. Callers must treat
    that as "not verifiable" rather than "no constraint" — see verify_approval.
    """
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Record authentication
# --------------------------------------------------------------------------
SIGNING_KEY_ENV = "SHORTS_APPROVAL_SIGNING_KEY"


def signing_key() -> str:
    """
    Key used to sign approval records. Unset by default, which means nothing
    can be granted — the same fail-closed posture as the allowlist and token.
    """
    return (os.environ.get(SIGNING_KEY_ENV) or "").strip()


def _sign(
    video_id: int,
    decision: str,
    approved_by: str,
    channel: str,
    content_hash: str,
    granted_epoch: int,
    expires_epoch: int,
    nonce: str,
) -> str:
    """
    HMAC over the security-relevant fields of a record.

    Authenticating the *grant call* (allowlist plus token) is not enough: that
    protects the front door but not the table. Anyone able to write a row into
    `approvals` — a compromised migration, a mis-scoped credential, a restore
    from an old dump — could otherwise mint an approval that verify() would
    accept. Signing the stored row closes that, because forging one needs the
    signing key and not merely database write access.

    Timestamps are signed as whole-second epochs rather than as the stored ISO
    text. The SQLite path stores the exact string that was written, but the
    Postgres path returns a datetime rendered in the session timezone, so the
    text form is not stable across backends while the epoch is.
    """
    canonical = "|".join((
        str(video_id), decision, approved_by, channel, content_hash,
        str(granted_epoch), str(expires_epoch), nonce,
    ))
    return hmac.new(
        signing_key().encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _audit(
    approval_id: int | None,
    video_id: int,
    action: str,
    actor: str = "",
    reason: str = "",
) -> None:
    """
    Append to the audit trail. Best effort and never raises.

    An audit write that failed must not turn a refusal into a crash, and must
    certainly not turn it into an approval. A lost audit row is logged so the
    gap is at least visible.
    """
    try:
        conn = db.get_conn()
        conn.execute(
            "INSERT INTO approval_audit (approval_id, video_id, action, actor, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (approval_id, video_id, action, actor, (reason or "")[:500]),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001 - audit must never break the decision
        logger.warning(
            "approvals: audit write failed (%s) for %s video %s",
            exc.__class__.__name__, action, video_id,
        )


def audit_trail(video_id: int, limit: int = 50) -> list[dict]:
    """The recorded history for a video, oldest first. Read-only."""
    try:
        conn = db.get_conn()
        rows = conn.execute(
            "SELECT approval_id, video_id, action, actor, reason, created_at "
            "FROM approval_audit WHERE video_id=? ORDER BY id ASC LIMIT ?",
            (video_id, limit),
        ).fetchall()
        conn.close()
    except Exception:  # noqa: BLE001 - a missing table is an empty trail
        return []
    return [
        {
            "approval_id": r["approval_id"],
            "action": r["action"],
            "actor": r["actor"],
            "reason": r["reason"],
            "created_at": str(r["created_at"] or ""),
        }
        for r in rows
    ]


def grant(
    video_id: int,
    approved_by: str,
    token: str,
    content_hash: str,
    channel: str = "cli",
    decision: str = APPROVED,
) -> tuple[bool, str]:
    """
    Record an approval decision for one video, auditing every outcome.

    A thin wrapper so that a refusal cannot silently skip the audit trail:
    _grant_impl has eight distinct refusal paths and each one would otherwise
    need its own logging call that a future edit could forget.

    Returns (granted, reason).
    """
    ok, reason = _grant_impl(
        video_id, approved_by, token, content_hash, channel=channel, decision=decision
    )
    if not ok:
        _audit_refusal(video_id, reason)
    return ok, reason


def _grant_impl(
    video_id: int,
    approved_by: str,
    token: str,
    content_hash: str,
    channel: str = "cli",
    decision: str = APPROVED,
) -> tuple[bool, str]:
    """
    The actual grant policy.

    Requires an allowlisted identity, the matching token, a content hash and a
    signing key. None is sufficient alone, and every one must be non-empty: an
    unset allowlist, token or signing key means granting is impossible rather
    than unauthenticated.

    Returns (granted, reason). Never raises on a policy refusal, so the caller
    can report why without a traceback.
    """
    identity = (approved_by or "").strip()
    allowlist = approvers()

    if not allowlist:
        return False, (
            "no approvers are configured; set SHORTS_APPROVER_ALLOWLIST. "
            "Until an approval mechanism exists, nothing may be approved."
        )
    if not identity:
        return False, "no approver identity supplied"
    if identity not in allowlist:
        # Do not echo the allowlist back — it is a list of who may act.
        return False, "identity is not permitted to approve"

    expected = (os.environ.get("SHORTS_APPROVAL_TOKEN") or "").strip()
    if not expected:
        return False, "no approval token is configured; set SHORTS_APPROVAL_TOKEN"
    if not token:
        return False, "no approval token supplied"
    if not hmac.compare_digest(token, expected):
        return False, "approval token rejected"

    if not content_hash:
        return False, "no content hash supplied; approval must bind to exact bytes"

    if not signing_key():
        return False, (
            f"no record signing key is configured; set {SIGNING_KEY_ENV}. "
            "Approvals are refused unsigned rather than accepted unverified."
        )

    ttl = ttl_seconds()
    now = time.time()
    granted_epoch = int(now)
    expires_epoch = granted_epoch + ttl
    expires = datetime.fromtimestamp(expires_epoch, tz=UTC).isoformat()
    nonce = secrets.token_hex(16)
    signature = _sign(
        video_id, decision, identity, channel, content_hash,
        granted_epoch, expires_epoch, nonce,
    )

    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO approvals "
        "(video_id, decision, approved_by, channel, content_hash, granted_at, "
        "expires_at, signature, nonce) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            video_id,
            decision,
            identity,
            channel,
            content_hash,
            datetime.fromtimestamp(granted_epoch, tz=UTC).isoformat(),
            expires,
            signature,
            nonce,
        ),
    )
    conn.commit()
    approval_id = cur.lastrowid
    conn.close()

    # Refusals are audited too, so a repeated failed attempt leaves a trail
    # instead of being invisible. Those are logged by the callers of grant().
    _audit(approval_id, video_id, "granted", identity,
           f"{decision} via {channel}, expires in {ttl}s")

    logger.info("approvals: %s by %s via %s, expires in %ss", decision, identity, channel, ttl)
    return True, f"recorded; expires in {ttl}s"


def _audit_refusal(video_id: int, reason: str) -> None:
    """
    Record why a grant was refused.

    The refusal reasons below deliberately do not echo the allowlist or token,
    so they are safe to store. An attacker probing for a valid identity leaves
    an evidence trail rather than a silent failure.
    """
    _audit(None, video_id, "refused", "", reason)


def revoke(video_id: int, by: str = "") -> int:
    """
    Withdraw every outstanding approval for a video. Returns rows affected.

    Revocation is not gated on the approver allowlist: an operator who can
    reach the database should always be able to stop a publish, even if the
    token has rotated out from under them.
    """
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id FROM approvals WHERE video_id=? AND revoked_at IS NULL", (video_id,)
    ).fetchall()
    cur = conn.execute(
        "UPDATE approvals SET revoked_at=? WHERE video_id=? AND revoked_at IS NULL",
        (datetime.now(UTC).isoformat(), video_id),
    )
    conn.commit()
    n = cur.rowcount if cur.rowcount is not None else 0
    conn.close()

    # The UPDATE mutates the live record in place, which is what verify() must
    # see. The trail is append-only, so the fact of the revocation survives
    # long after the row it revoked has itself been consumed or overwritten.
    for row in rows:
        _audit(row["id"], video_id, "revoked", by or "", "withdrawn by operator")

    if n:
        logger.info("approvals: %s approval(s) for video %s revoked%s", n, video_id,
                    f" by {by}" if by else "")
    return n


# --------------------------------------------------------------------------
# Verifying
# --------------------------------------------------------------------------
def _parse_iso(value: str | None) -> float | None:
    """Parse a stored timestamp. Unparseable means unverifiable, so None."""
    if not value:
        return None
    try:
        # Python 3.11+ parses a trailing Z directly; CI and the supported
        # runtime are both 3.11.
        return datetime.fromisoformat(str(value)).timestamp()
    except (ValueError, TypeError):
        return None


def _check_row(row, content_hash: str) -> tuple[bool, str]:
    """
    The approval policy applied to one stored row.

    Shared by verify() and consume() so that the predicate and the spend can
    never drift apart — a divergence there would mean the UI shows an approval
    as usable that the publisher then refuses, or worse, the reverse.

    Every path returns False unless the row is signed, current, unrevoked,
    unconsumed and bound to these exact bytes.
    """
    get = row.__getitem__

    if (get("revoked_at") or "") != "":
        return False, "approval was revoked"
    if (get("consumed_at") or "") != "":
        # Single-use. A grant authorises one publication of one render, not a
        # standing licence, so a spent approval authorises nothing further.
        return False, "approval has already been used for a publication"
    if (get("decision") or "") != APPROVED:
        return False, f"latest decision for this video is {get('decision')!r}, not approved"

    expires = _parse_iso(get("expires_at"))
    if expires is None:
        return False, "approval has no parseable expiry; refusing"

    granted = _parse_iso(get("granted_at"))
    if granted is None:
        return False, "approval has no parseable grant time; refusing"
    if granted > expires:
        # Checked before expiry so a corrupt row reports as corrupt. Reporting
        # it as merely expired would point an operator at the clock when the
        # record itself is untrustworthy.
        return False, "approval record is internally inconsistent (granted after expiry); refusing"

    if time.time() > expires:
        return False, "approval has expired"

    stored_hash = get("content_hash") or ""
    if not stored_hash:
        # An approval that binds to nothing binds to nothing in particular.
        return False, "approval is not bound to any content; refusing"
    if not content_hash:
        return False, "no content hash supplied for the file to be published; refusing"
    if not hmac.compare_digest(stored_hash, content_hash):
        return False, (
            "approval does not match the file to be published — the render has "
            "changed since it was approved"
        )

    stored_sig = get("signature") or ""
    if not stored_sig:
        # A row written before signing existed, or written directly into the
        # table by something that never held the signing key. Both are refused
        # rather than trusted: signature presence is what distinguishes a
        # decision recorded by an authenticated operator from a row.
        return False, "approval record is unsigned; refusing"
    if not signing_key():
        return False, (
            f"no record signing key is configured ({SIGNING_KEY_ENV}); "
            "cannot authenticate the approval; refusing"
        )
    expected = _sign(
        row["video_id"], get("decision"), get("approved_by"), get("channel"),
        stored_hash, int(granted), int(expires), get("nonce") or "",
    )
    if not hmac.compare_digest(stored_sig, expected):
        return False, (
            "approval record failed signature verification; it was not written by "
            "an authenticated operator, or was altered after the fact"
        )

    return True, f"approved by {get('approved_by')} via {get('channel')}"


_SELECT = (
    "SELECT id, video_id, decision, approved_by, channel, content_hash, granted_at, "
    "expires_at, revoked_at, signature, nonce, consumed_at, consumed_by "
    "FROM approvals WHERE video_id=? ORDER BY id DESC LIMIT 1"
)


NO_RECORD = (
    "no approval record for this video. Publishing requires an approval "
    "granted by an allowlisted operator; see scripts/approve_video.py."
)


def _latest_row(video_id: int):
    """
    The newest approval row for a video, or None.

    Raises on an unreadable store; both callers turn that into a refusal. A
    connection error must never be mistaken for "no approval".
    """
    with contextlib.closing(db.get_conn()) as conn:
        return conn.execute(_SELECT, (video_id,)).fetchone()


def verify(video_id: int, content_hash: str = "") -> tuple[bool, str]:
    """
    The single question publishing is allowed to ask, read-only.

    Returns (allowed, reason) without spending the approval and with no other
    side effect — not even a log row — so an operator interface can poll
    publishability without destroying the approval it is reporting on or
    drowning the audit trail. Publishing itself must call consume(), which
    enforces the same policy and then marks the record spent.

    Deliberately consults no environment variable that could switch the
    requirement off — only the signing key, and its absence refuses rather than
    permits. Every refusal path returns False. There is no code path that
    returns True without a signed, matching, unexpired, unrevoked, unspent,
    content-bound 'approved' row.
    """
    try:
        row = _latest_row(video_id)
    except Exception as e:  # noqa: BLE001 - unreadable store must not publish
        return False, f"approval store could not be read ({e.__class__.__name__}); refusing"

    if row is None:
        return False, NO_RECORD

    return _check_row(row, content_hash)


def consume(
    video_id: int, content_hash: str = "", consumed_by: str = ""
) -> tuple[bool, str]:
    """
    Verify and atomically spend the approval. This is what publishing calls.

    The spend is a single UPDATE guarded by `consumed_at IS NULL`, so two
    concurrent publishers cannot both take the same approval: whichever commit
    lands first wins and the other sees rowcount 0 and is refused. Checking and
    then updating in separate statements would leave a race window open.

    On success the approval can never authorise another publication.
    """
    spent = 0
    try:
        with contextlib.closing(db.get_conn()) as conn:
            row = conn.execute(_SELECT, (video_id,)).fetchone()
            if row is None:
                return False, NO_RECORD

            ok, reason = _check_row(row, content_hash)
            if ok:
                cur = conn.execute(
                    "UPDATE approvals SET consumed_at=?, consumed_by=? "
                    "WHERE id=? AND consumed_at IS NULL",
                    (datetime.now(UTC).isoformat(), consumed_by or "publish", row["id"]),
                )
                conn.commit()
                spent = cur.rowcount if cur.rowcount is not None else 0
                if spent != 1:
                    # Another publisher won the race, or the row vanished.
                    # Either way this caller does not hold the approval.
                    ok = False
                    reason = "approval was consumed by another publication first"
    except Exception as e:  # noqa: BLE001
        return False, (
            f"approval store could not be read or updated "
            f"({e.__class__.__name__}); refusing"
        )

    if ok:
        _audit(row["id"], video_id, "consumed", consumed_by or "publish",
               f"authorised publication of {(content_hash or '?')[:12]}")
        logger.info(
            "approvals: video %s approval consumed by %s", video_id, consumed_by or "publish"
        )
    else:
        _audit(row["id"], video_id, "refused", consumed_by, reason)
    return ok, reason
