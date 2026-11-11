"""
test_audit.py — Unit tests for the enterprise audit logging engine.
"""

import json
import pytest
from agent.modules.audit import log_event, get_recent_audit_trail, detect_anomalies, _sanitize_details


def test_sanitize_details_obfuscates_secrets():
    raw_details = {
        "model": "deepseek-v3",
        "api_key": "sk-or-v1-abcdef1234567890",
        "auth_token": "secret123456",
        "tokens_used": 150
    }
    sanitized = json.loads(_sanitize_details(raw_details))
    assert sanitized["model"] == "deepseek-v3"
    assert sanitized["tokens_used"] == 150
    assert sanitized["api_key"] != "sk-or-v1-abcdef1234567890"
    assert "sk-o...7890" in sanitized["api_key"] or "[REDACTED]" in sanitized["api_key"]


def test_log_and_retrieve_event():
    event_id = log_event(
        event_type="AUTH_HANDSHAKE",
        actor_email="pankajbhati140@gmail.com",
        provider="omniroute",
        model="gpt-5.4-nano",
        status="SUCCESS",
        details={"status": "verified"}
    )
    assert event_id > 0

    trail = get_recent_audit_trail(limit=10, actor_email="pankajbhati140@gmail.com")
    assert len(trail) > 0
    latest = trail[0]
    assert latest["event_type"] == "AUTH_HANDSHAKE"
    assert latest["actor_email"] == "pankajbhati140@gmail.com"
    assert latest["provider"] == "omniroute"


def test_anomaly_detection():
    # Log 5 consecutive failures
    for _ in range(5):
        log_event(
            event_type="AUTH_HANDSHAKE",
            actor_email="suspicious_tester@example.com",
            provider="test_provider",
            status="FAILED"
        )

    anomalies = detect_anomalies(threshold_failed_attempts=5)
    assert any(a["actor_email"] == "suspicious_tester@example.com" for a in anomalies)
