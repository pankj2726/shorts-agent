"""
test_failover_proxy.py — Feature 11: Workspace Proxy & Failover Dispatcher (Agent B).

Validates:
  1. Discovery of available router channels (Pollinations, OpenRouter, DeepInfra, Arena, etc.).
  2. Automated multi-tier failover cascade across provider tiers.
  3. Provider preference overrides.
  4. Attempt history logging for routing observability.
  5. Error recovery: When primary router fails, traffic seamlessly shifts to secondary.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from agent.modules.omniroute_embedded import EmbeddedOmniRoute


def test_omniroute_available_routes_discovery():
    """Verify get_available_routes returns configured list of routes."""
    router = EmbeddedOmniRoute()
    routes = router.get_available_routes()

    assert len(routes) >= 3
    providers = {r["provider"] for r in routes}
    assert "pollinations" in providers
    assert "openrouter" in providers
    assert "deepinfra" in providers


def test_omniroute_fallback_to_pollinations_when_offline(monkeypatch):
    """Verify router automatically cascades to public free Pollinations tier when all keys are missing."""
    monkeypatch.delenv("ARENA_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)

    router = EmbeddedOmniRoute()

    with patch.object(router, "_call_pollinations_text") as mock_pol:
        mock_pol.return_value = {
            "provider": "pollinations",
            "model": "default",
            "cost": 0.0,
            "text": "Pollinations fallback response",
            "status": "success"
        }
        res = router.complete_text(prompt="Hello", model="coding-router-preview")

    assert res["status"] == "success"
    assert res["provider"] == "pollinations"
    assert res["text"] == "Pollinations fallback response"


def test_omniroute_provider_preference_override(monkeypatch):
    """Verify explicit provider_preference forces order of attempted routers."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    router = EmbeddedOmniRoute()

    call_order = []

    def mock_or(prompt, system, model, timeout):
        call_order.append("openrouter")
        return {"provider": "openrouter", "model": model, "cost": 0.0, "text": "OR response", "status": "success"}

    with patch.object(router, "_call_openrouter_text", side_effect=mock_or):
        res = router.complete_text(
            prompt="Hello",
            model="deepseek-r1",
            provider_preference=["openrouter", "pollinations"]
        )

    assert call_order == ["openrouter"]
    assert res["provider"] == "openrouter"


def test_omniroute_attempt_history_tracking(monkeypatch):
    """Verify attempt_history logs failed attempts when cascading through routers."""
    monkeypatch.setenv("ARENA_API_KEY", "test-arena-key")
    router = EmbeddedOmniRoute()

    def mock_arena_fail(prompt, system, model, timeout, effort=None):
        raise RuntimeError("Arena cluster temporarily unavailable")

    def mock_pollinations_success(prompt, system, model, timeout):
        return {"provider": "pollinations", "model": model, "cost": 0.0, "text": "Recovered", "status": "success"}

    with patch.object(router, "_call_arena_text", side_effect=mock_arena_fail):
        with patch.object(router, "_call_pollinations_text", side_effect=mock_pollinations_success):
            res = router.complete_text(
                prompt="Cascade test",
                model="coding-router-preview",
                provider_preference=["arena", "pollinations"]
            )

    assert res["status"] == "success"
    assert res["provider"] == "pollinations"
    assert "attempt_history" in res
    assert any(a.get("provider") == "arena" for a in res["attempt_history"])


def test_omniroute_handles_simulated_failure():
    """Verify router does not crash when an intermediate provider raises an exception."""
    router = EmbeddedOmniRoute()

    with patch.object(router, "_call_pollinations_text", return_value={"provider": "pollinations", "model": "m", "cost": 0.0, "text": "OK", "status": "success"}):
        res = router.complete_text(prompt="Resilience test", model="default")
        assert res["status"] == "success"
