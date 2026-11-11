"""
test_model_discovery.py — Feature 17: Continuous Research & Dynamic Model Discovery Engine.

Validates:
  1. Discovery probe against Arena.AI models endpoint.
  2. Discovery probe against OpenRouter models endpoint with free/frontier filtering.
  3. Discovery probe against Groq endpoint.
  4. Aggregation of cross-provider catalogs via discover_all().
  5. Graceful degradation when provider credentials are missing.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from agent.modules.model_discovery import DynamicModelDiscovery


def test_discovery_arena_models_structure(monkeypatch):
    """Verify Arena model discovery maps raw items into standardized canonical metadata."""
    monkeypatch.setenv("ARENA_API_KEY", "test-arena-key")

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = json.dumps({
        "data": [
            {"id": "claude-coding-router-preview"},
            {"id": "claude-fable-5-1"}
        ]
    }).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        models = DynamicModelDiscovery.discover_arena_models()

    assert len(models) == 2
    assert models[0]["canonical_id"] == "arena/claude-coding-router-preview"
    assert models[0]["provider"] == "arena"
    assert models[0]["cost_tier"] == "router_funded"
    assert "capabilities" in models[0]


def test_discovery_openrouter_models_filtering(monkeypatch):
    """Verify OpenRouter discovery selectively keeps top frontier and free models."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = json.dumps({
        "data": [
            {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Llama 3.3 70B", "pricing": {"prompt": "0"}},
            {"id": "anthropic/claude-3-5-sonnet", "name": "Claude 3.5 Sonnet", "pricing": {"prompt": "0.000003"}},
            {"id": "obscure-niche-model-99", "name": "Obscure", "pricing": {"prompt": "0.000010"}}
        ]
    }).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        models = DynamicModelDiscovery.discover_openrouter_models()

    ids = [m["original_id"] for m in models]
    assert "meta-llama/llama-3.3-70b-instruct:free" in ids
    assert "anthropic/claude-3-5-sonnet" in ids
    assert "obscure-niche-model-99" not in ids


def test_discovery_groq_models(monkeypatch):
    """Verify Groq discovery processes fast LPU models."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = json.dumps({
        "data": [
            {"id": "llama-3.3-70b-versatile"},
            {"id": "llama-3.1-8b-instant"}
        ]
    }).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        models = DynamicModelDiscovery.discover_groq_models()

    assert len(models) == 2
    assert models[0]["provider"] == "groq"
    assert models[0]["canonical_id"] == "groq/llama-3.3-70b-versatile"


def test_discovery_all_combines_catalogs():
    """Verify discover_all aggregates discovered models from all online providers."""
    mock_arena = [{"canonical_id": "arena/m1", "provider": "arena"}]
    mock_or = [{"canonical_id": "openrouter/m2", "provider": "openrouter"}]
    mock_groq = [{"canonical_id": "groq/m3", "provider": "groq"}]

    with patch.object(DynamicModelDiscovery, "discover_arena_models", return_value=mock_arena):
        with patch.object(DynamicModelDiscovery, "discover_openrouter_models", return_value=mock_or):
            with patch.object(DynamicModelDiscovery, "discover_groq_models", return_value=mock_groq):
                all_discovered = DynamicModelDiscovery.discover_all(force_refresh=True)

    cids = {m.get("canonical_id") or m.get("id") for m in all_discovered}
    assert "arena/m1" in cids
    assert "openrouter/m2" in cids
    assert "groq/m3" in cids


def test_discovery_graceful_on_missing_keys(monkeypatch):
    """Verify discovery probes return empty lists without exceptions when API keys are absent."""
    monkeypatch.delenv("ARENA_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    assert DynamicModelDiscovery.discover_arena_models() == []
    assert DynamicModelDiscovery.discover_openrouter_models() == []
    assert DynamicModelDiscovery.discover_groq_models() == []
