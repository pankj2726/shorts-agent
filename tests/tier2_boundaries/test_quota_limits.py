"""
Tier 2: Boundary & Corner Cases — Quota Limits & Thresholds
Tests boundary transitions around the 10,000 daily limit, 20% warning threshold (2,000 units),
exact cost matches (1600 units for videos.insert), and 0-remaining conditions.
"""
from unittest.mock import MagicMock
import pytest

from agent.modules import quota


def test_quota_boundary_fresh_day_full_limit(isolated_db):
    """Boundary Case 1: Fresh day starts with exactly 10,000 units remaining (1.0 pct)."""
    assert quota.used_today() == 0
    assert quota.remaining() == 10_000
    assert quota.pct_remaining() == 1.0


def test_quota_boundary_exactly_20_percent_threshold(isolated_db, monkeypatch):
    """Boundary Case 2: At exactly 2,000 units remaining (20.0%), warning behavior check."""
    # Consume 8,000 units
    quota.consume("bulk_ops", units=8000)
    assert quota.remaining() == 2000
    assert quota.pct_remaining() == 0.20

    warn_called = False
    def mock_notify(event, text, level="info"):
        nonlocal warn_called
        if "QUOTA_LOW" in event:
            warn_called = True

    monkeypatch.setattr(quota, "notify", mock_notify)
    # Check 1-unit call: remaining is 2000, 2000/10000 = 0.20 which is not strictly < 0.20
    allowed = quota.check("videos.list")
    assert allowed is True
    assert warn_called is False


def test_quota_boundary_below_20_percent_triggers_warning(isolated_db, monkeypatch):
    """Boundary Case 3: At 1,999 units remaining (19.99%), warning notification is triggered."""
    # Consume 8,001 units
    quota.consume("bulk_ops", units=8001)
    assert quota.remaining() == 1999
    assert quota.pct_remaining() < 0.20

    notifications = []
    monkeypatch.setattr(quota, "notify", lambda event, text, level="info": notifications.append((event, level)))

    allowed = quota.check("videos.list")
    assert allowed is True
    assert any(n[0] == "QUOTA_LOW" for n in notifications)


def test_quota_boundary_exact_cost_match_videos_insert(isolated_db, monkeypatch):
    """Boundary Case 4: Exactly 1,600 units remaining — videos.insert (costs 1600) is allowed."""
    quota.consume("bulk_ops", units=8400)
    assert quota.remaining() == 1600

    monkeypatch.setattr(quota, "notify", lambda *a, **k: None)
    assert quota.check("videos.insert") is True


def test_quota_boundary_one_unit_below_cost_blocks(isolated_db, monkeypatch):
    """Boundary Case 5: 1,599 units remaining (1 unit below 1600) — videos.insert is blocked."""
    quota.consume("bulk_ops", units=8401)
    assert quota.remaining() == 1599

    notifications = []
    monkeypatch.setattr(quota, "notify", lambda event, text, level="info": notifications.append((event, text)))

    allowed = quota.check("videos.insert")
    assert allowed is False
    assert any("Cannot make videos.insert" in n[1] for n in notifications)


def test_quota_boundary_zero_remaining_exhaustion(isolated_db, monkeypatch):
    """Boundary Case 6: 0 units remaining — all paid or unit-cost calls are blocked."""
    quota.consume("bulk_ops", units=10_000)
    assert quota.remaining() == 0
    assert quota.pct_remaining() == 0.0

    monkeypatch.setattr(quota, "notify", lambda *a, **k: None)
    assert quota.check("videos.list") is False
    assert quota.check("comments.insert") is False
    assert quota.check("videos.insert") is False
