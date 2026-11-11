"""
Tier 4: Real-World Application Scenarios — High-Volume Batch Production under Free Quotas
Tests simulating an automated batch run of multiple video productions under YouTube Data API's
10,000 daily free units quota, tracking ledger consumption and graceful pausing when quota exhausts.
"""
import pytest
from agent.modules import quota


def test_batch_production_quota_lifecycle(isolated_db, monkeypatch):
    """
    Scenario 2: High-Volume batch production:
    - 5 videos produced consuming 1600 units each (8,000 units)
    - 6th video consumes 1600 units (9,600 units total, leaving 400)
    - 7th video is safely paused/blocked without API rate-limit errors or paid billing demands.
    """
    notifications = []
    def mock_notify(event, text, level="info"):
        notifications.append({"event": event, "text": text, "level": level})

    monkeypatch.setattr(quota, "notify", mock_notify)

    # 1. Produce 5 videos
    for v_idx in range(1, 6):
        assert quota.check("videos.insert") is True
        quota.consume("videos.insert")  # 1600 units
        quota.consume("playlistItems.list")  # 1 unit
        quota.consume("videos.list")  # 1 unit

    used_after_5 = quota.used_today()
    # 5 * (1600 + 1 + 1) = 8010 units
    assert used_after_5 == 8010
    assert quota.remaining() == 1990  # < 2000 units (< 20%)

    # 2. 6th video check occurs while remaining is 1990 (< 20%), triggering warning
    assert quota.check("videos.insert") is True
    assert any(n["event"] == "QUOTA_LOW" for n in notifications)

    # 3. 6th video consumes 1600 units
    quota.consume("videos.insert")
    assert quota.remaining() == 390

    # 4. 7th video requires 1600 units, but only 390 remain -> must be BLOCKED
    allowed_7th = quota.check("videos.insert")
    assert allowed_7th is False

    # 5. Low-cost monitoring calls (1 unit) should still be allowed
    assert quota.check("videos.list") is True
    quota.consume("videos.list")
    assert quota.remaining() == 389

    # 6. Verify daily summary aggregates entire batch
    summary = quota.daily_summary()
    assert summary["used"] == 9611
    assert summary["remaining"] == 389
    assert summary["breakdown"]["videos.insert"] == 6 * 1600
