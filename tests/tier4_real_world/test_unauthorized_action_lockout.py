"""
Tier 4: Real-World Application Scenarios — Unauthorized Action Lockout & Safety Gate
Tests hostile external interaction defenses: prompt injection attempts, direct unapproved publishing,
commenting on external channels, expired action tokens, and paid endpoint lockdown.
"""
import pytest

from agent.modules import comments
from tests.conftest import UnauthorizedActionError, ZeroCostViolationError
from tests.tier1_features.test_hitl_gate import (
    approve_action,
    stage_action_for_approval,
    verify_action_authorized,
)
from tests.tier2_boundaries.test_zero_cost_violations import validate_model_cost


def test_lockout_direct_unapproved_publishing(isolated_db):
    """Lockout 1: Attempt to publish video without valid approved token is strictly blocked."""
    action_token = stage_action_for_approval(
        isolated_db,
        action_type="publish_video",
        payload={"video_id": 505, "title": "Unapproved Auto Publish"},
    )

    with pytest.raises(UnauthorizedActionError) as exc_info:
        verify_action_authorized(isolated_db, action_token)
    assert "has not been approved" in str(exc_info.value)


def test_lockout_commenting_on_external_channels():
    """Lockout 2: Shorts Agent policy strictly forbids automated commenting on competitor/external channels."""
    OWN_CHANNEL_ID = "UC_OWN_CHANNEL_123"

    def validate_target_channel(target_channel_id: str) -> None:
        if target_channel_id != OWN_CHANNEL_ID:
            raise UnauthorizedActionError(
                f"Policy Violation: Automated commenting on external channel '{target_channel_id}' is strictly forbidden."
            )

    # Own channel is permitted
    validate_target_channel(OWN_CHANNEL_ID)

    # Competitor channel raises UnauthorizedActionError
    with pytest.raises(UnauthorizedActionError) as exc_info:
        validate_target_channel("UC_COMPETITOR_999")
    assert "strictly forbidden" in str(exc_info.value)


def test_lockout_expired_approval_tokens(isolated_db):
    """Lockout 3: Approved action token that has expired cannot be executed."""
    # Staged with negative TTL (already expired)
    expired_token = stage_action_for_approval(
        isolated_db,
        action_type="reply_comment",
        payload={"comment_id": "cmt_stale", "reply": "Late reply"},
        expires_in_hours=-2,
    )
    approve_action(isolated_db, expired_token)

    with pytest.raises(UnauthorizedActionError) as exc_info:
        verify_action_authorized(isolated_db, expired_token)
    assert "expired" in str(exc_info.value)


def test_lockout_forged_or_tampered_action_token(isolated_db):
    """Lockout 4: Attempting to bypass gate with arbitrary or modified token raises error."""
    with pytest.raises(UnauthorizedActionError) as exc_info:
        verify_action_authorized(isolated_db, "admin-bypass-super-token")
    assert "not found" in str(exc_info.value)


def test_lockout_commercial_paid_model_injection():
    """Lockout 5: Injected request attempting to invoke paid commercial API (e.g., GPT-4) is halted."""
    with pytest.raises(ZeroCostViolationError):
        validate_model_cost("gpt-4o")
