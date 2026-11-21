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
import hashlib
import hmac
import logging
import os
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


def grant(
    video_id: int,
    approved_by: str,
    token: str,
    content_hash: str,
    channel: str = "cli",
    decision: str = APPROVED,
) -> tuple[bool, str]:
    """
    Record an approval decision for one video.

    Requires both an allowlisted identity and the matching token. Neither is
    sufficient alone, and both must be non-empty: an unset allowlist or an
    unset token means granting is impossible rather than unauthenticated.

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

    ttl = ttl_seconds()
    now = time.time()
    expires = (
        datetime.fromtimestamp(now + ttl, tz=UTC).isoformat()
    )

    conn = db.get_conn()
    conn.execute(
        "INSERT INTO approvals "
        "(video_id, decision, approved_by, channel, content_hash, granted_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            video_id,
            decision,
            identity,
            channel,
            content_hash,
            datetime.fromtimestamp(now, tz=UTC).isoformat(),
            expires,
        ),
    )
    conn.commit()
    conn.close()

    logger.info("approvals: %s by %s via %s, expires in %ss", decision, identity, channel, ttl)
    return True, f"recorded; expires in {ttl}s"


def revoke(video_id: int, by: str = "") -> int:
    """
    Withdraw every outstanding approval for a video. Returns rows affected.

    Revocation is not gated on the approver allowlist: an operator who can
    reach the database should always be able to stop a publish, even if the
    token has rotated out from under them.
    """
    conn = db.get_conn()
    cur = conn.execute(
        "UPDATE approvals SET revoked_at=? WHERE video_id=? AND revoked_at IS NULL",
        (datetime.now(UTC).isoformat(), video_id),
    )
    conn.commit()
    n = cur.rowcount if cur.rowcount is not None else 0
    conn.close()
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


def verify(video_id: int, content_hash: str = "") -> tuple[bool, str]:
    """
    The single question publishing is allowed to ask.

    Returns (allowed, reason). Deliberately consults no environment variable
    that could switch the requirement off — only the TTL knob on the grant
    side, and that shortens rather than removes the requirement.

    Every refusal path returns False. There is no code path that returns True
    without a matching, unexpired, unrevoked, content-bound 'approved' row.
    """
    try:
        conn = db.get_conn()
        row = conn.execute(
            "SELECT decision, approved_by, channel, content_hash, granted_at, "
            "expires_at, revoked_at FROM approvals "
            "WHERE video_id=? ORDER BY id DESC LIMIT 1",
            (video_id,),
        ).fetchone()
        conn.close()
    except Exception as e:  # noqa: BLE001 - unreadable store must not publish
        return False, f"approval store could not be read ({e.__class__.__name__}); refusing"

    if row is None:
        return False, (
            "no approval record for this video. Publishing requires an approval "
            "granted by an allowlisted operator; see scripts/approve_video.py."
        )

    # Both backends expose row[key]: sqlite3.Row natively, db_pg.Row as a dict
    # subclass. No branch needed.
    get = row.__getitem__

    if (get("revoked_at") or "") != "":
        return False, "approval was revoked"
    if (get("decision") or "") != APPROVED:
        return False, f"latest decision for this video is {get('decision')!r}, not approved"

    expires = _parse_iso(get("expires_at"))
    if expires is None:
        return False, "approval has no parseable expiry; refusing"

    granted = _parse_iso(get("granted_at"))
    if granted is not None and granted > expires:
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

    return True, f"approved by {get('approved_by')} via {get('channel')}"
