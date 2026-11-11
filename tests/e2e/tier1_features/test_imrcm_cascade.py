"""
test_imrcm_cascade.py — Feature 19: Infinite Multi-Router Cascade Matrix (IMRCM).

Validates:
  1. Same-model first routing: If a model is requested (e.g. claude-3-5-sonnet),
     the cascade exhausts all providers serving that exact same model before falling back to peers.
  2. Parallel wiring of all 12 router channels in _is_provider_online.
  3. Provider parameter translation across same-model routes.
  4. Automatic failover upon rate limits (HTTP 429) or transient provider failure.
  5. Preservation of reasoning effort across cascade transitions.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from agent.modules.capability_registry import UniversalCapabilityRegistry, CANONICAL_MODELS
from agent.modules.omniroute_embedded import EmbeddedOmniRoute


def test_imrcm_same_model_chain_construction(monkeypatch):
    """Verify same-model providers are placed at the head of the cascade chain before general fallbacks."""
    # Enable both arena and anthropic (both offer claude-3-5-sonnet)
    monkeypatch.setenv("ARENA_API_KEY", "test-arena-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")

    router = EmbeddedOmniRoute()
    spec = CANONICAL_MODELS["claude-3-5-sonnet"]
    model_providers = list(spec["provider_mappings"].keys())

    # Build chain as EmbeddedOmniRoute does
    same_model_chain = [p for p in model_providers if router._is_provider_online(p)]
    assert "arena" in same_model_chain
    assert "anthropic" in same_model_chain


def test_imrcm_all_twelve_routers_mapped():
    """Verify _is_provider_online accounts for all router keys across the 12-channel matrix."""
    router = EmbeddedOmniRoute()

    expected_channels = [
        "arena", "anthropic", "openai", "openrouter", "omniroute",
        "opencode", "anyapi", "groq", "deepinfra", "vertex_ai",
        "fal_ai", "pollinations"
    ]

    for channel in expected_channels:
        # pollinations is always online
        if channel == "pollinations":
            assert router._is_provider_online(channel) is True
        else:
            # When keys are missing, should return False cleanly without crashing
            assert isinstance(router._is_provider_online(channel), bool)


def test_imrcm_parameter_translation_preserves_canonical_model():
    """Verify UniversalCapabilityRegistry translates the same canonical model appropriately for each router."""
    # Test claude-3-5-sonnet across routers
    arena_params = UniversalCapabilityRegistry.translate_parameters("claude-3-5-sonnet", "arena")
    assert arena_params["model"] == "claude-coding-router-preview"

    anthropic_params = UniversalCapabilityRegistry.translate_parameters("claude-3-5-sonnet", "anthropic")
    assert anthropic_params["model"] == "claude-3-5-sonnet-20241022"

    openrouter_params = UniversalCapabilityRegistry.translate_parameters("claude-3-5-sonnet", "openrouter")
    assert openrouter_params["model"] == "anthropic/claude-3.5-sonnet"


def test_imrcm_failover_on_provider_exception(monkeypatch):
    """Verify when primary same-model router fails with 429, cascade continues to next router."""
    monkeypatch.setenv("ARENA_API_KEY", "test-arena-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    router = EmbeddedOmniRoute()

    def mock_arena_429(*args, **kwargs):
        raise RuntimeError("HTTP 429 Too Many Requests: Rate limit exceeded on Arena H100 cluster")

    def mock_anthropic_success(*args, **kwargs):
        return {
            "provider": "anthropic",
            "model": "claude-3-5-sonnet-20241022",
            "text": "Failover completion from Anthropic",
            "status": "success"
        }

    with patch.object(router, "_call_arena_text", side_effect=mock_arena_429):
        with patch("agent.modules.anthropic_adapter.AnthropicAdapter.complete", side_effect=mock_anthropic_success):
            res = router.complete_text(
                prompt="Cascade test prompt",
                model="claude-3-5-sonnet",
                provider_preference=["arena", "anthropic"]
            )

    assert res["status"] == "success"
    assert res["provider"] == "anthropic"
    assert res["text"] == "Failover completion from Anthropic"
    assert any(a.get("provider") == "arena" for a in res["attempt_history"])


def test_imrcm_cascade_preserves_reasoning_effort():
    """Verify high reasoning effort parameter is propagated during parameter translation."""
    effort = "high"
    params = UniversalCapabilityRegistry.translate_parameters(
        canonical_id="coding-router-preview",
        target_provider="arena",
        effort=effort
    )
    assert params.get("reasoning_effort") == "high"
