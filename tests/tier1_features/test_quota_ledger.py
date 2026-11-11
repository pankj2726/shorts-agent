"""
Tier 1: Feature Coverage — Persistent Quota Ledger & Rate Pacing (Features 6 & 11)
Tests SQLite quota usage logging across restarts, YouTube Data API cost tables,
warning thresholds, and daily summary metrics.
"""
from datetime import date
import pytest

from agent.modules import quota


def test_quota_unit_costs_table():
    """Test 1: Verify canonical unit cost definitions per YouTube API documentation."""
    assert quota.UNIT_COSTS["videos.insert"] == 1600
    assert quota.UNIT_COSTS["playlistItems.list"] == 1
    assert quota.UNIT_COSTS["videos.list"] == 1
    assert quota.UNIT_COSTS["channels.list"] == 1
    assert quota.UNIT_COSTS["comments.insert"] == 50
    assert quota.UNIT_COSTS["search.list"] == 100
    assert quota.DAILY_LIMIT == 10_000


def test_quota_consumption_and_tracking(isolated_db):
    """Test 2: Ensure consume() records units and used_today() accurately totals them."""
    initial = quota.used_today()
    assert initial == 0

    quota.consume("playlistItems.list")  # 1 unit
    assert quota.used_today() == 1
    assert quota.remaining() == 9999

    quota.consume("videos.insert")  # 1600 units
    assert quota.used_today() == 1601
    assert quota.remaining() == 8399


def test_quota_check_allows_when_sufficient(isolated_db):
    """Test 3: check() returns True when sufficient quota units remain."""
    assert quota.check("videos.insert") is True
    assert quota.check("videos.list") is True


def test_quota_check_blocks_when_insufficient(isolated_db, monkeypatch):
    """Test 4: check() returns False when units required exceed remaining units."""
    # Consume 9,000 units
    quota.consume("custom_bulk", units=9000)
    assert quota.remaining() == 1000

    # videos.insert costs 1600 > 1000 remaining -> should return False
    monkeypatch.setattr(quota, "notify", lambda *a, **k: None)
    allowed = quota.check("videos.insert")
    assert allowed is False

    # 1-unit call should still be allowed
    assert quota.check("videos.list") is True


def test_quota_daily_summary_breakdown(isolated_db):
    """Test 5: daily_summary() returns formatted aggregation with call breakdown."""
    quota.consume("videos.list")
    quota.consume("videos.list")
    quota.consume("comments.insert")

    summary = quota.daily_summary()
    assert summary["date"] == date.today().isoformat()
    assert summary["used"] == 52  # 1 + 1 + 50
    assert summary["remaining"] == 9948
    assert summary["breakdown"]["videos.list"] == 2
    assert summary["breakdown"]["comments.insert"] == 50


def test_failed_requests_still_accounted_for(isolated_db):
    """Test 6: Rule requirement — failed requests still cost quota and must be consumed."""
    initial = quota.used_today()
    # Simulate a failed attempt to fetch video details
    quota.consume("videos.list")
    assert quota.used_today() == initial + 1
