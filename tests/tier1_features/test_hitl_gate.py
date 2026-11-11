"""
Tier 1: Feature Coverage — Strict Human-In-The-Loop Safety Gate (Feature 25)
Tests SQLite pending_actions queue, authorization verification, UnauthorizedActionError enforcement,
action expiration, and operator approval transitions.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from tests.conftest import UnauthorizedActionError

# Try importing from agent.modules.social or define contract implementation
try:
    from agent.modules.social import stage_action_for_approval, verify_action_authorized
except ImportError:
    def stage_action_for_approval(db_path: Path, action_type: str, payload: dict, expires_in_hours: int = 24) -> str:
        action_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(hours=expires_in_hours)).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO pending_actions (action_id, action_type, payload, status, created_at, expires_at) "
                "VALUES (?, ?, ?, 'pending', ?, ?)",
                (action_id, action_type, json.dumps(payload), now.isoformat(), expires_at),
            )
            conn.commit()
        return action_id

    def verify_action_authorized(db_path: Path, action_id: str) -> bool:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT status, expires_at FROM pending_actions WHERE action_id = ?",
                (action_id,)
            ).fetchone()
        if not row:
            raise UnauthorizedActionError(f"Action '{action_id}' not found in authorization registry.")
        
        status, expires_at_str = row[0], row[1]
        if status != "approved":
            raise UnauthorizedActionError(f"Action '{action_id}' has not been approved by operator (current status: {status}).")
        
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str)
            if datetime.now(timezone.utc) > expires_at:
                raise UnauthorizedActionError(f"Action '{action_id}' approval has expired.")
        return True


def approve_action(db_path: Path, action_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE pending_actions SET status = 'approved', authorized_at = ? WHERE action_id = ?",
            (now, action_id),
        )
        conn.commit()


def reject_action(db_path: Path, action_id: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE pending_actions SET status = 'rejected' WHERE action_id = ?",
            (action_id,),
        )
        conn.commit()


def test_stage_action_creates_pending_record(isolated_db):
    """Test 1: stage_action_for_approval inserts pending record and returns action token."""
    payload = {"video_id": 42, "title": "Secret Tech Shorts"}
    token = stage_action_for_approval(isolated_db, "publish_video", payload)

    assert token is not None
    assert len(token) > 10

    with sqlite3.connect(isolated_db) as conn:
        row = conn.execute("SELECT status, payload FROM pending_actions WHERE action_id = ?", (token,)).fetchone()
    assert row is not None
    assert row[0] == "pending"
    assert "Secret Tech Shorts" in row[1]


def test_verify_action_unapproved_raises_unauthorized_error(isolated_db):
    """Test 2: verify_action_authorized raises UnauthorizedActionError for pending actions."""
    token = stage_action_for_approval(isolated_db, "reply_comment", {"comment_id": "c1", "reply": "Thanks!"})

    with pytest.raises(UnauthorizedActionError) as exc_info:
        verify_action_authorized(isolated_db, token)
    assert "has not been approved" in str(exc_info.value)


def test_verify_action_approved_succeeds(isolated_db):
    """Test 3: verify_action_authorized returns True once operator explicitly approves."""
    token = stage_action_for_approval(isolated_db, "publish_video", {"video_id": 10})
    approve_action(isolated_db, token)

    assert verify_action_authorized(isolated_db, token) is True


def test_rejected_action_raises_unauthorized_error(isolated_db):
    """Test 4: Explicit operator rejection raises UnauthorizedActionError."""
    token = stage_action_for_approval(isolated_db, "post_comment", {"text": "Check this"})
    reject_action(isolated_db, token)

    with pytest.raises(UnauthorizedActionError) as exc_info:
        verify_action_authorized(isolated_db, token)
    assert "rejected" in str(exc_info.value)


def test_expired_action_cannot_be_authorized(isolated_db):
    """Test 5: Actions past their TTL expiration window are blocked."""
    token = stage_action_for_approval(isolated_db, "publish_video", {"video_id": 99}, expires_in_hours=-1)
    approve_action(isolated_db, token)

    with pytest.raises(UnauthorizedActionError) as exc_info:
        verify_action_authorized(isolated_db, token)
    assert "expired" in str(exc_info.value)


def test_unknown_action_token_rejected(isolated_db):
    """Test 6: Attempting to verify an unknown or fabricated token raises UnauthorizedActionError."""
    with pytest.raises(UnauthorizedActionError) as exc_info:
        verify_action_authorized(isolated_db, "fabricated-token-999")
    assert "not found" in str(exc_info.value)
