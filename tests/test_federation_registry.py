"""
test_federation_registry.py — Unit Tests for Universal Model Federation Registry & Circuit Breaker.
"""

import time
import pytest
from unittest.mock import patch

from agent.modules import federation_registry
from agent.modules.federation_registry import FederationRegistry, CanonicalModel, ModelRoute


@pytest.fixture(autouse=True)
def isolated_federation_state(tmp_path, monkeypatch):
    """Isolate FederationRegistry state from on-disk cooldowns during tests."""
    monkeypatch.setattr(federation_registry, "STATE_PATH", tmp_path / "federation_state.json")
    FederationRegistry._initialized = False
    FederationRegistry._models = {}
    yield
    FederationRegistry._initialized = False
    FederationRegistry._models = {}


def test_federation_initialization():
    FederationRegistry.initialize()
    models = FederationRegistry.get_all_models()
    assert len(models) >= 13

    claude = FederationRegistry.get_canonical_model("claude-3-5-sonnet")
    assert claude is not None
    assert claude.developer == "Anthropic"
    assert len(claude.routes) >= 4  # Arena, OpenRouter, Anthropic Direct, OmniRoute


def test_same_model_cascade_resolution():
    FederationRegistry.initialize()
    with patch.dict("os.environ", {
        "ARENA_API_KEY": "test_arena",
        "OPENROUTER_API_KEY": "test_openrouter",
        "CLAUDE_CONSOLE_API_KEY": "test_anthropic"
    }):
        routes = FederationRegistry.resolve_route_cascade("claude-3-5-sonnet")
        assert len(routes) >= 3
        # First priority route is Arena H100
        assert routes[0].provider == "arena"
        assert routes[0].upstream_model == "claude-coding-router-preview"
        # Second priority route is OpenRouter Claude 3.5 Sonnet
        assert routes[1].provider == "openrouter"
        assert routes[1].upstream_model == "anthropic/claude-3.5-sonnet"


def test_circuit_breaker_and_cooldown_for_429():
    FederationRegistry.initialize()
    with patch.dict("os.environ", {
        "ARENA_API_KEY": "test_arena",
        "OPENROUTER_API_KEY": "test_openrouter",
    }):
        routes_before = FederationRegistry.resolve_route_cascade("claude-3-5-sonnet")
        assert routes_before[0].provider == "arena"

        # Simulate 429 Rate Limit on Arena
        FederationRegistry.mark_route_failure(
            canonical_id="claude-3-5-sonnet",
            provider="arena",
            error_msg="Rate limit exceeded",
            status_code=429
        )

        # Re-resolve: Arena is in cooldown, so next route (OpenRouter) immediately takes over!
        routes_after = FederationRegistry.resolve_route_cascade("claude-3-5-sonnet")
        assert routes_after[0].provider == "openrouter"

        # Verify circuit recovery
        FederationRegistry.mark_route_success("claude-3-5-sonnet", "arena", latency_ms=640)
        routes_restored = FederationRegistry.resolve_route_cascade("claude-3-5-sonnet")
        assert routes_restored[0].provider == "arena"
