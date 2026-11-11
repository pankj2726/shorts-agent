"""
test_trend_radar.py — Unit tests for the Trend Radar and Chaos Detector.
"""

from agent.modules.trend_radar import TrendRadar, ChaosDetector


def test_opportunity_score_calculation():
    radar = TrendRadar()
    score = radar.calculate_opportunity_score(
        growth_rate=90.0,
        search_demand=85.0,
        competition_count=10,
        production_feasibility=95.0,
    )
    # Expected: (90*0.3) + (85*0.25) + (80*0.25) + (95*0.2) = 27 + 21.25 + 20 + 19 = 87.25 -> 87.2 or 87.3
    assert 85.0 <= score <= 90.0


def test_chaos_detector_signal():
    detector = ChaosDetector()
    breakout = detector.evaluate_chaos_signal(
        topic="Rare snow leopard footage",
        growth_rate=88.0,
        competing_videos_count=15,
        novelty_score=90.0,
    )
    assert breakout["is_chaos_breakout"] is True
    assert breakout["saturation_risk"] == "LOW"
    assert "24–72 hours" in breakout["estimated_opportunity_window"]


def test_scan_and_format_digest():
    radar = TrendRadar()
    trends = radar.scan_trends()
    assert len(trends) >= 3
    assert trends[0]["opportunity_score"] >= trends[1]["opportunity_score"]

    digest = radar.format_telegram_digest(trends)
    assert "SHORTS FORGE — Trend Radar Digest" in digest
    assert "/create" in digest
