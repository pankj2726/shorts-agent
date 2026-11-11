"""
test_arena_cluster.py — Feature 1: Arena.AI Handshake & H100 Cluster Warm TLS (Agent A).

Validates:
  1. Singleton pattern and instance lifecycle.
  2. Cloudflare Error 1010 bypass User-Agent and enterprise identity headers.
  3. Model canonical shorthand and dot-syntax resolution.
  4. Persistent warm TLS session pooling configuration.
  5. Authentication error handling on missing credentials.
  6. Zero-cost ping telemetry and metrics reporting.
  7. Dynamic model discovery with TTL caching.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch
import pytest
import requests

from agent.modules.arena_cluster import (
    ArenaClusterClient,
    ArenaAuthenticationError,
    resolve_target_model,
    CLAUDE_FAMILY_MODELS,
    MODEL_CANONICAL_MAPPINGS,
)


def test_arena_singleton_instance():
    """Verify ArenaClusterClient implements a stable singleton via get_instance()."""
    client1 = ArenaClusterClient.get_instance()
    client2 = ArenaClusterClient.get_instance()
    assert client1 is client2
    assert isinstance(client1, ArenaClusterClient)
    assert client1.base_url == "https://api.arena.ai/v1"


def test_arena_headers_and_cloudflare_bypass(monkeypatch):
    """Verify headers include Cloudflare-bypassing User-Agent and enterprise identity."""
    monkeypatch.setenv("ARENA_API_KEY", "test-arena-secret-key-12345")
    client = ArenaClusterClient()
    headers = client.get_headers()

    assert headers["Authorization"] == "Bearer test-arena-secret-key-12345"
    assert headers["User-Agent"] == "ShortsAgent-ArenaRouter/1.0"
    assert headers["X-Workspace-Identity"] == "pankajbhati140@gmail.com"
    assert headers["X-Organization-Name"] == "ShortsForge-Enterprise"
    assert headers["Connection"] == "keep-alive"
    assert headers["Content-Type"] == "application/json"


def test_arena_model_canonical_resolution():
    """Verify resolve_target_model correctly resolves shorthand aliases and fallbacks."""
    assert resolve_target_model("claude-opus-5.5") == "claude-opus-5-5"
    assert resolve_target_model("claude-sonnet-5.5") == "claude-sonnet-5-5"
    assert resolve_target_model("claude-fable-5.1") == "claude-fable-5-1"
    assert resolve_target_model("gpt-6") == "gpt-6-astra"
    assert resolve_target_model("gemini-3.7") == "gemini-3.7-flash"
    assert resolve_target_model("") == "coding-router-preview"
    assert resolve_target_model(None) == "coding-router-preview"
    assert resolve_target_model("custom-frontier-model") == "custom-frontier-model"


def test_arena_session_connection_pooling():
    """Verify persistent keep-alive session uses configured pool size and mounts adapters."""
    client = ArenaClusterClient(pool_connections=15, pool_maxsize=30)
    session = client._get_session()

    assert isinstance(session, requests.Session)
    adapter_https = session.adapters.get("https://")
    assert adapter_https is not None
    assert adapter_https._pool_connections == 15
    assert adapter_https._pool_maxsize == 30


def test_arena_missing_api_key_raises_auth_error(monkeypatch):
    """Verify ArenaAuthenticationError is raised when no Arena credentials are in environment."""
    monkeypatch.delenv("ARENA_API_KEY", raising=False)
    monkeypatch.delenv("ARENA_KEY", raising=False)
    monkeypatch.delenv("ARENA_TOKEN", raising=False)

    client = ArenaClusterClient()
    with pytest.raises(ArenaAuthenticationError) as exc_info:
        client.get_api_key()
    assert "ARENA_API_KEY environment variable is not configured" in str(exc_info.value)


def test_arena_ping_success_and_metrics(monkeypatch):
    """Verify ping() performs zero-cost telemetry check and reports latency and status."""
    monkeypatch.setenv("ARENA_API_KEY", "test-arena-key")
    client = ArenaClusterClient()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [{"id": "coding-router-preview"}, {"id": "claude-fable-5-1"}]
    }

    with patch.object(client._get_session(), "get", return_value=mock_resp):
        ping_result = client.ping(timeout=5)

    assert ping_result["status"] == "ONLINE"
    assert ping_result["authenticated"] is True
    assert ping_result["models_count"] == 2
    assert ping_result["latency_ms"] >= 0


def test_arena_discover_models_caching(monkeypatch):
    """Verify discover_models caches catalog and obeys TTL unless force_refresh=True."""
    monkeypatch.setenv("ARENA_API_KEY", "test-arena-key")
    client = ArenaClusterClient()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"id": "claude-coding-router-preview"},
            {"id": "coding-router-preview"}
        ]
    }

    with patch.object(client._get_session(), "get", return_value=mock_resp) as mock_get:
        models1 = client.discover_models()
        assert len(models1) == 2
        assert models1[0]["canonical_id"] == "arena/claude-coding-router-preview"
        assert mock_get.call_count == 1

        # Second call should use TTL cache without calling get() again
        models2 = client.discover_models()
        assert mock_get.call_count == 1
        assert models1 == models2

        # force_refresh=True should bypass cache and invoke get()
        models3 = client.discover_models(force_refresh=True)
        assert mock_get.call_count == 2
