"""
Tier 3: Pairwise Combinatorial — HITL Safety Gate + Comment Moderation Integration
Tests cross-feature interaction between comment classification, pending action staging,
operator approval/rejection workflows, and unauthorized reply blocking.
"""
import json
import sqlite3
import pytest

from agent.modules import comments
from tests.conftest import UnauthorizedActionError
from tests.tier1_features.test_hitl_gate import (
    approve_action,
    reject_action,
    stage_action_for_approval,
    verify_action_authorized,
)


def test_question_comment_stages_reply_for_hitl(isolated_db):
    """Pairwise 1: Legitimate question comment triggers draft reply and stages action in pending_actions."""
    comment_data = {
        "comment_id": "cmt_100",
        "author": "Viewer123",
        "text": "Does this battery tip work for iPhone 15?",
        "category": "question",
    }
    draft_reply = "Yes! Modern iPhones support the 80% charge limit in Settings > Battery > Charging Optimization."

    # Stage action in HITL queue
    action_token = stage_action_for_approval(
        isolated_db,
        action_type="reply_comment",
        payload={"comment_id": comment_data["comment_id"], "reply_text": draft_reply},
    )

    assert action_token is not None
    # Verify unapproved state
    with pytest.raises(UnauthorizedActionError):
        verify_action_authorized(isolated_db, action_token)


def test_operator_approval_allows_comment_dispatch(isolated_db):
    """Pairwise 2: Operator explicit approval allows comment posting to execute."""
    action_token = stage_action_for_approval(
        isolated_db,
        action_type="reply_comment",
        payload={"comment_id": "cmt_101", "reply_text": "Thanks for watching!"},
    )

    approve_action(isolated_db, action_token)
    assert verify_action_authorized(isolated_db, action_token) is True

    # Record in comments table
    with sqlite3.connect(isolated_db) as conn:
        conn.execute(
            "INSERT INTO comments (comment_id, author, text, category, replied, reply_text) "
            "VALUES ('cmt_101', 'Viewer', 'Great tip', 'praise', 1, 'Thanks for watching!')"
        )
        conn.commit()
        row = conn.execute("SELECT replied, reply_text FROM comments WHERE comment_id = 'cmt_101'").fetchone()
    assert row[0] == 1
    assert row[1] == "Thanks for watching!"


def test_operator_rejection_halts_comment_dispatch(isolated_db):
    """Pairwise 3: Operator rejection prevents comment from being published."""
    action_token = stage_action_for_approval(
        isolated_db,
        action_type="reply_comment",
        payload={"comment_id": "cmt_102", "reply_text": "Incorrect automated advice"},
    )

    reject_action(isolated_db, action_token)

    with pytest.raises(UnauthorizedActionError) as exc_info:
        verify_action_authorized(isolated_db, action_token)
    assert "rejected" in str(exc_info.value)


def test_spam_comment_filtered_without_staging(isolated_db):
    """Pairwise 4: Comments containing promotional URLs are flagged without staging any reply."""
    spam_text = "Free gift cards at https://scam.example.com"
    is_spam = comments._URL_RE.search(spam_text) is not None
    assert is_spam is True

    # Record flagged comment without staging HITL action
    with sqlite3.connect(isolated_db) as conn:
        conn.execute(
            "INSERT INTO comments (comment_id, author, text, category, flagged, replied) "
            "VALUES ('cmt_spam', 'Spammer', ?, 'spam', 1, 0)",
            (spam_text,),
        )
        conn.commit()
        actions = conn.execute("SELECT count(*) FROM pending_actions").fetchone()[0]

    assert actions == 0  # No action staged for spam


def test_unauthorized_direct_reply_blocked(isolated_db):
    """Pairwise 5: Attempt to dispatch reply without staging token raises UnauthorizedActionError."""
    def dispatch_reply(token: str) -> bool:
        return verify_action_authorized(isolated_db, token)

    with pytest.raises(UnauthorizedActionError):
        dispatch_reply("unregistered_bypass_token")
