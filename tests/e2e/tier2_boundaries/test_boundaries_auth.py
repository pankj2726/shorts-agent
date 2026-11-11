"""
test_boundaries_auth.py — Tier 2 Boundary Tests for Authentication & Organization Scoping.

Covers:
  - Missing x-api-key and Authorization headers
  - Malformed Bearer tokens (missing Bearer, whitespace-only, corrupt prefixes)
  - Invalid API key formats across Anthropic (sk-ant-) and OpenAI (sk-)
  - Workspace scoping auto-discovery & default fallback (wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm)
  - Malformed workspace IDs (missing wrkspc_ prefix)
  - Key lookup precedence across environment variables
  - Case-insensitive auth headers and conflicting dual auth headers
  - Provider 401 Unauthorized & 403 Forbidden simulations
"""

from __future__ import annotations

import os
from typing import Any, Dict
import pytest
import requests

from agent.modules.anthropic_adapter import AnthropicAdapter
from tests.e2e.conftest import MockGatewayServer


# ─── Missing and Blank Header Boundaries ─────────────────────────────────────

def test_missing_x_api_key_anthropic_endpoint(e2e_client: requests.Session):
    """Test /v1/messages request without x-api-key header."""
    headers = {
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
        "Connection": "close"
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Auth test"}]},
        timeout=5.0
    )
    # Gateway accepts local client requests or enforces auth gracefully
    assert resp.status_code in (200, 401)


def test_missing_authorization_openai_endpoint(e2e_client: requests.Session):
    """Test /v1/chat/completions request without Authorization header."""
    headers = {
        "Content-Type": "application/json",
        "Connection": "close"
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Auth test"}]},
        timeout=5.0
    )
    assert resp.status_code in (200, 401)


def test_empty_x_api_key(e2e_client: requests.Session):
    """Test /v1/messages request with empty string x-api-key: ''."""
    headers = {
        "x-api-key": "",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
        "Connection": "close"
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=headers,
        json={"model": "claude-3-5-haiku", "messages": [{"role": "user", "content": "Empty key"}]},
        timeout=5.0
    )
    assert resp.status_code in (200, 401)


def test_empty_bearer_token(e2e_client: requests.Session):
    """Test /v1/chat/completions with empty Bearer token: 'Bearer '."""
    headers = {
        "Authorization": "Bearer ",
        "Content-Type": "application/json",
        "Connection": "close"
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Empty bearer"}]},
        timeout=5.0
    )
    assert resp.status_code in (200, 401)


def test_malformed_bearer_token_missing_bearer_prefix(e2e_client: requests.Session):
    """Test Authorization header missing the required 'Bearer ' scheme prefix."""
    headers = {
        "Authorization": "sk-openai-token-without-bearer-keyword",
        "Content-Type": "application/json",
        "Connection": "close"
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Bad bearer scheme"}]},
        timeout=5.0
    )
    assert resp.status_code in (200, 400, 401)


# ─── Key Format and Validation Boundaries ────────────────────────────────────

def test_invalid_api_key_format_anthropic(e2e_client: requests.Session):
    """Test x-api-key lacking valid Anthropic sk-ant- key prefix."""
    headers = {
        "x-api-key": "invalid_key_no_sk_ant_prefix_12345",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Prefix check"}]},
        timeout=5.0
    )
    assert resp.status_code in (200, 400, 401)


def test_invalid_api_key_format_openai(e2e_client: requests.Session):
    """Test Authorization Bearer token lacking standard sk- format."""
    headers = {
        "Authorization": "Bearer non_standard_key_xyz",
        "Content-Type": "application/json"
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Key format"}]},
        timeout=5.0
    )
    assert resp.status_code in (200, 400, 401)


# ─── Workspace Scoping & Auto-Discovery Boundaries ────────────────────────────

def test_valid_organization_workspace_id_propagation(
    mock_gateway_server: MockGatewayServer,
    e2e_client: requests.Session,
    workspace_headers: Dict[str, str]
):
    """Verify anthropic-workspace-id header (wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm) is received and recorded."""
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=workspace_headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Org test"}]},
        timeout=5.0
    )
    assert resp.status_code == 200

    # Inspect recorded requests in mock server
    recorded = mock_gateway_server.state.recorded_requests
    matching = [r for r in recorded if r["path"] == "/v1/messages"]
    assert len(matching) > 0
    last_req = matching[-1]
    # Header keys in HTTP request are lowercase or case-preserved
    rec_headers = {k.lower(): v for k, v in last_req["headers"].items()}
    assert rec_headers.get("anthropic-workspace-id") == "wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm"


def test_malformed_workspace_id_prefix(e2e_client: requests.Session):
    """Test sending malformed workspace ID lacking wrkspc_ prefix."""
    headers = {
        "x-api-key": "sk-ant-test-key",
        "anthropic-version": "2023-06-01",
        "anthropic-workspace-id": "badprefix_9999999",
        "Content-Type": "application/json"
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Bad ws"}]},
        timeout=5.0
    )
    assert resp.status_code in (200, 400)


def test_anthropic_adapter_workspace_discovery_env_override(monkeypatch: pytest.MonkeyPatch):
    """Test AnthropicAdapter.get_workspace_id() respects explicit ANTHROPIC_WORKSPACE_ID environment variable."""
    test_ws = "wrkspc_custom_override_12345"
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", test_ws)
    discovered = AnthropicAdapter.get_workspace_id()
    assert discovered == test_ws


def test_anthropic_adapter_workspace_discovery_fallback(monkeypatch: pytest.MonkeyPatch):
    """Test AnthropicAdapter.get_workspace_id() falls back to verified default when env is empty."""
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("CLAUDE_WORKSPACE_ID", raising=False)
    AnthropicAdapter._cached_workspace_id = None
    discovered = AnthropicAdapter.get_workspace_id()
    assert discovered == AnthropicAdapter.DEFAULT_WORKSPACE_ID
    assert discovered == "wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm"


def test_anthropic_adapter_key_lookup_order(monkeypatch: pytest.MonkeyPatch):
    """Test AnthropicAdapter.get_api_key() precedence order across key aliases."""
    # 1. ANTHROPIC_API_KEY takes precedence
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-primary")
    monkeypatch.setenv("CLAUDE_CONSOLE_API_KEY", "sk-ant-secondary")
    assert AnthropicAdapter.get_api_key() == "sk-ant-primary"

    # 2. Falls back to CLAUDE_CONSOLE_API_KEY if primary unset
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert AnthropicAdapter.get_api_key() == "sk-ant-secondary"


# ─── Dual, Whitespace & Case-Insensitive Auth Headers ─────────────────────────

def test_case_insensitive_auth_headers(e2e_client: requests.Session):
    """Test uppercase and mixed case auth headers: X-API-KEY and x-api-key."""
    headers = {
        "X-API-KEY": "sk-ant-case-test",
        "Anthropic-Version": "2023-06-01",
        "Content-Type": "application/json"
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Case header"}]},
        timeout=5.0
    )
    assert resp.status_code == 200


def test_conflicting_dual_auth_headers(e2e_client: requests.Session):
    """Test sending both x-api-key and Authorization headers simultaneously."""
    headers = {
        "x-api-key": "sk-ant-test-anthropic-key",
        "Authorization": "Bearer sk-openai-test-bearer-key",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Dual auth"}]},
        timeout=5.0
    )
    assert resp.status_code == 200


def test_auth_headers_with_whitespace_and_newlines(e2e_client: requests.Session):
    """Test Bearer token containing extra leading and trailing whitespace."""
    headers = {
        "Authorization": "Bearer   sk-openai-padded-key-12345   ",
        "Content-Type": "application/json"
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Padded token"}]},
        timeout=5.0
    )
    assert resp.status_code == 200


# ─── 401 Unauthorized & 403 Forbidden Provider Simulations ───────────────────

def test_mock_server_401_unauthorized_simulation(
    mock_gateway_server: MockGatewayServer,
    e2e_client: requests.Session,
    anthropic_headers: Dict[str, str]
):
    """Simulate 401 Unauthorized from upstream provider."""
    mock_gateway_server.set_mock_response(
        path="/v1/messages",
        status_code=401,
        body={"type": "error", "error": {"type": "authentication_error", "message": "Invalid API key"}},
        headers={"Content-Type": "application/json"}
    )
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "401 test"}]},
        timeout=5.0
    )
    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["type"] == "authentication_error"
    mock_gateway_server.reset()


def test_mock_server_403_forbidden_cloudflare_bypass(
    mock_gateway_server: MockGatewayServer,
    e2e_client: requests.Session,
    openai_headers: Dict[str, str]
):
    """Simulate 403 Forbidden (Cloudflare Error 1010 or organization access denied)."""
    mock_gateway_server.set_mock_response(
        path="/v1/chat/completions",
        status_code=403,
        body={"error": {"type": "permission_denied", "message": "Cloudflare Error 1010: Access Denied"}},
        headers={"Content-Type": "application/json"}
    )
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json={"model": "claude-coding-router-preview", "messages": [{"role": "user", "content": "403 test"}]},
        timeout=5.0
    )
    assert resp.status_code == 403
    data = resp.json()
    assert "Error 1010" in data["error"]["message"]
    mock_gateway_server.reset()
