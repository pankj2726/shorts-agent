"""
test_capability_registry.py — Unit Tests for 5-Model Powerhouse, Aliases & Dynamic Probe.
"""

import json
import urllib.error
from unittest.mock import MagicMock, patch
import pytest

from agent.modules.capability_registry import (
    CANONICAL_MODELS,
    UniversalCapabilityRegistry,
)
from agent.modules.model_discovery import (
    DynamicModelDiscovery,
    ModelEndpointProbe,
)


def test_five_frontier_models_registered():
    """Verifies that all 5 user-requested frontier models are registered with full metadata."""
    required_models = [
        "claude-3-5-haiku",
        "mistral-large-2411",
        "deepseek-v3",
        "llama-3.1-405b",
        "grok-2",
        "gemini-1.5-pro",
    ]

    for m in required_models:
        assert m in CANONICAL_MODELS, f"Model {m} missing from CANONICAL_MODELS"
        spec = CANONICAL_MODELS[m]
        assert "pricing" in spec, f"Model {m} missing pricing metadata"
        assert "input_per_million" in spec["pricing"]
        assert "output_per_million" in spec["pricing"]
        assert spec["pricing"]["currency"] == "USD"
        assert "tags" in spec, f"Model {m} missing tags"
        assert len(spec["tags"]) > 0
        assert spec["context_window"] > 0
        assert spec["max_output_tokens"] > 0
        assert "provider_mappings" in spec
        assert len(spec["provider_mappings"]) > 0


def test_model_alias_and_dot_syntax_resolution():
    """Verifies dot-syntax and hyphen-syntax normalization."""
    # Dot vs Hyphen normalization
    assert UniversalCapabilityRegistry.resolve_canonical_id("llama-3.1-405b") in ("llama-3.1-405b", "llama-3-1-405b")
    assert UniversalCapabilityRegistry.resolve_canonical_id("llama-3-1-405b") in ("llama-3.1-405b", "llama-3-1-405b")

    assert UniversalCapabilityRegistry.resolve_canonical_id("gemini-1.5-pro") in ("gemini-1.5-pro", "gemini-1-5-pro")
    assert UniversalCapabilityRegistry.resolve_canonical_id("gemini-1-5-pro") in ("gemini-1.5-pro", "gemini-1-5-pro")

    assert UniversalCapabilityRegistry.resolve_canonical_id("grok-2") == "grok-2"
    assert UniversalCapabilityRegistry.resolve_canonical_id("claude-3.5-haiku") == "claude-3-5-haiku"

    # Status resolution succeeds for both syntaxes
    status_dot = UniversalCapabilityRegistry.get_model_status("llama-3.1-405b")
    status_hyphen = UniversalCapabilityRegistry.get_model_status("llama-3-1-405b")
    assert status_dot["status"] != "UNSUPPORTED"
    assert status_hyphen["status"] != "UNSUPPORTED"

    status_gemini_dot = UniversalCapabilityRegistry.get_model_status("gemini-1.5-pro")
    status_gemini_hyphen = UniversalCapabilityRegistry.get_model_status("gemini-1-5-pro")
    assert status_gemini_dot["status"] != "UNSUPPORTED"
    assert status_gemini_hyphen["status"] != "UNSUPPORTED"


def test_parameter_translation_across_providers():
    """Verifies parameter translation for the newly added models."""
    # Claude 3.5 Haiku
    haiku_arena = UniversalCapabilityRegistry.translate_parameters("claude-3-5-haiku", "arena")
    assert haiku_arena["model"] == "claude-haiku-4-5-20251001"
    haiku_ant = UniversalCapabilityRegistry.translate_parameters("claude-3-5-haiku", "anthropic")
    assert haiku_ant["model"] == "claude-3-5-haiku-20241022"

    # DeepSeek V3
    ds_arena = UniversalCapabilityRegistry.translate_parameters("deepseek-v3", "arena")
    assert ds_arena["model"] == "deepseek-v4p1-flash"
    ds_or = UniversalCapabilityRegistry.translate_parameters("deepseek-v3", "openrouter")
    assert ds_or["model"] == "deepseek/deepseek-chat"

    # Llama 3.1 405B (works with dot syntax)
    llama_di = UniversalCapabilityRegistry.translate_parameters("llama-3.1-405b", "deepinfra")
    assert llama_di["model"] == "meta-llama/Meta-Llama-3.1-405B-Instruct"

    # Grok 2
    grok_arena = UniversalCapabilityRegistry.translate_parameters("grok-2", "arena")
    assert grok_arena["model"] == "grok-4.7"

    # Gemini 1.5 Pro reasoning effort
    gemini_vertex = UniversalCapabilityRegistry.translate_parameters(
        "gemini-1.5-pro", "vertex_ai", effort="high"
    )
    assert gemini_vertex["model"] == "gemini-1.5-pro"


def test_dynamic_discovery_user_agent():
    """Verifies that model discovery probe sets custom User-Agent headers to prevent Cloudflare 403."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "data": [{"id": "model-arena-1"}]
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch.dict("os.environ", {"ARENA_API_KEY": "test_arena_key"}):
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            models = DynamicModelDiscovery.discover_arena_models()
            assert len(models) == 1
            assert models[0]["id"] == "model-arena-1"
            sent_req = mock_urlopen.call_args[0][0]
            assert sent_req.headers.get("User-agent") == "ShortsAgent-ArenaRouter/1.0"


def test_groq_discovery_user_agent():
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "data": [{"id": "openai/gpt-oss-120b"}]
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch.dict("os.environ", {"GROQ_API_KEY": "test_groq_key"}):
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            models = DynamicModelDiscovery.discover_groq_models()
            assert len(models) == 1
            assert models[0]["id"] == "openai/gpt-oss-120b"
            sent_req = mock_urlopen.call_args[0][0]
            assert sent_req.headers.get("User-agent") == "ShortsAgent-GroqRouter/1.0"


def test_health_probe_latency_and_states():
    """Verifies ModelEndpointProbe health checks under healthy, rate-limited, and failure states."""
    # 1. Healthy (200 OK)
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch.dict("os.environ", {"ARENA_API_KEY": "test_arena_key"}):
        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = ModelEndpointProbe.probe_provider_health("arena")
            assert res["status"] == "HEALTHY"
            assert res["http_code"] == 200
            assert res["latency_ms"] >= 0

    # 2. Rate limited (429)
    err_429 = urllib.error.HTTPError(None, 429, "Too Many Requests", None, None)
    with patch.dict("os.environ", {"ARENA_API_KEY": "test_arena_key"}):
        with patch("urllib.request.urlopen", side_effect=err_429):
            res_429 = ModelEndpointProbe.probe_provider_health("arena")
            assert res_429["status"] == "RATE_LIMITED"
            assert res_429["http_code"] == 429

    # 3. Unavailable / Network Error
    with patch.dict("os.environ", {"ARENA_API_KEY": "test_arena_key"}):
        with patch("urllib.request.urlopen", side_effect=Exception("Connection reset")):
            res_err = ModelEndpointProbe.probe_provider_health("arena")
            assert res_err["status"] == "UNAVAILABLE"

    # 4. Unconfigured
    with patch.dict("os.environ", {}, clear=True):
        res_unconf = ModelEndpointProbe.probe_provider_health("arena")
        assert res_unconf["status"] == "UNCONFIGURED"

    # 5. Unknown provider
    res_unk = ModelEndpointProbe.probe_provider_health("non_existent_provider")
    assert res_unk["status"] == "UNKNOWN_PROVIDER"
