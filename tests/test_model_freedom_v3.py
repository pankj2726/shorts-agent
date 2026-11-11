"""
test_model_freedom_v3.py — End-to-End Unit & Integration Tests for Model Freedom Initiative V3.
"""

import pytest
from unittest.mock import patch, MagicMock

from agent.modules.capability_registry import UniversalCapabilityRegistry
from agent.modules.access_policy import ModelAccessPolicy
from agent.modules.model_discovery import DynamicModelDiscovery
from agent.modules.anthropic_adapter import AnthropicAdapter


def test_universal_capability_registry():
    models = UniversalCapabilityRegistry.get_all_models()
    assert len(models) >= 7

    # Test setting active model
    assert UniversalCapabilityRegistry.set_active_model("deepseek-r1") is True
    assert UniversalCapabilityRegistry.get_active_model() == "deepseek-r1"

    # Test setting effort
    assert UniversalCapabilityRegistry.set_active_effort("max") is True
    assert UniversalCapabilityRegistry.get_active_effort() == "max"

    # Reset
    UniversalCapabilityRegistry.set_active_model("claude-3-5-sonnet")
    UniversalCapabilityRegistry.set_active_effort("high")


def test_parameter_translator():
    # Test Arena translation with high effort
    arena_params = UniversalCapabilityRegistry.translate_parameters(
        canonical_id="claude-3-5-sonnet",
        target_provider="arena",
        effort="high"
    )
    assert arena_params["model"] == "claude-coding-router-preview"
    assert arena_params["reasoning_effort"] == "high"

    # Test Anthropic translation with thinking budget
    ant_params = UniversalCapabilityRegistry.translate_parameters(
        canonical_id="claude-3-5-sonnet",
        target_provider="anthropic",
        effort="max"
    )
    assert ant_params["thinking"]["type"] == "enabled"
    assert ant_params["thinking"]["budget_tokens"] == 4096

    # Test non-reasoning model omits reasoning parameters
    llama_params = UniversalCapabilityRegistry.translate_parameters(
        canonical_id="llama-3-3-70b",
        target_provider="groq",
        effort="high"
    )
    assert "reasoning_effort" not in llama_params
    assert "thinking" not in llama_params


def test_model_access_policy():
    # 1. Unauthenticated user
    res1 = ModelAccessPolicy.evaluate(user_authenticated=False, provider_id="arena", model_id="claude-3-5-sonnet")
    assert res1.allowed is False
    assert res1.status_code == "USER_UNAUTHENTICATED"
    assert "Pro" not in res1.message  # No subscription upselling

    # 2. Authorized execution
    with patch.dict("os.environ", {"ARENA_API_KEY": "test_arena_key"}):
        res2 = ModelAccessPolicy.evaluate(user_authenticated=True, provider_id="arena", model_id="claude-3-5-sonnet")
        assert res2.allowed is True
        assert res2.status_code == "AUTHORIZED"

    # 3. Provider Auth Required
    with patch.dict("os.environ", {"ARENA_API_KEY": ""}, clear=True):
        res3 = ModelAccessPolicy.evaluate(user_authenticated=True, provider_id="arena", model_id="claude-3-5-sonnet")
        assert res3.allowed is False
        assert res3.status_code == "PROVIDER_AUTH_REQUIRED"
        assert "subscription" not in res3.message.lower()


def test_anthropic_adapter_structure():
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test-key", "ANTHROPIC_ORG_ID": "org-test-123"}):
        headers = AnthropicAdapter.get_headers()
        assert headers["x-api-key"] == "sk-ant-test-key"
        assert headers["anthropic-organization-id"] == "org-test-123"
        assert headers["anthropic-version"] == "2023-06-01"


def test_dynamic_discovery_caching():
    models = DynamicModelDiscovery.discover_all(force_refresh=False)
    assert isinstance(models, list)
    assert len(models) >= 3
