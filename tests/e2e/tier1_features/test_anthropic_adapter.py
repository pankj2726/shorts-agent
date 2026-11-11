"""
test_anthropic_adapter.py — Feature 2: Anthropic Workspace Scoping Auto-Discovery & Direct Adapter.

Validates:
  1. Default workspace auto-discovery fallback (wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm).
  2. Environment variable workspace override behavior.
  3. Header composition (x-api-key, anthropic-version, anthropic-workspace-id, User-Agent).
  4. Organization key scoping (anthropic-organization-id header propagation).
  5. Extended thinking budget configuration and automatic max_tokens adjustment.
  6. Authentication error on missing API credentials.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from agent.modules.anthropic_adapter import AnthropicAdapter


def test_anthropic_workspace_auto_discovery_fallback(monkeypatch):
    """Verify default workspace ID wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm is discovered as fallback."""
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("CLAUDE_WORKSPACE_ID", raising=False)
    AnthropicAdapter._cached_workspace_id = None

    ws_id = AnthropicAdapter.get_workspace_id()
    assert ws_id == "wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm"
    assert AnthropicAdapter.DEFAULT_WORKSPACE_ID == "wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm"


def test_anthropic_workspace_env_override(monkeypatch):
    """Verify ANTHROPIC_WORKSPACE_ID explicitly overrides auto-discovery."""
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_custom_override_999")
    AnthropicAdapter._cached_workspace_id = None

    ws_id = AnthropicAdapter.get_workspace_id()
    assert ws_id == "wrkspc_custom_override_999"


def test_anthropic_headers_assembly(monkeypatch):
    """Verify get_headers attaches required headers including version and workspace scoping."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-test-key-xyz")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm")

    headers = AnthropicAdapter.get_headers()
    assert headers["x-api-key"] == "sk-ant-api-test-key-xyz"
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["anthropic-workspace-id"] == "wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm"
    assert headers["User-Agent"] == "ShortsForge-AnthropicAdapter/1.0"
    assert headers["content-type"] == "application/json"


def test_anthropic_org_id_header(monkeypatch):
    """Verify ANTHROPIC_ORG_ID attaches anthropic-organization-id when present."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-org-key")
    monkeypatch.setenv("ANTHROPIC_ORG_ID", "org_enterprise_123")

    headers = AnthropicAdapter.get_headers()
    assert headers.get("anthropic-organization-id") == "org_enterprise_123"


def test_anthropic_thinking_budget_enforcement(monkeypatch):
    """Verify complete() enforces thinking parameters and expands max_tokens > budget_tokens."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    captured_payload = {}

    def mock_urlopen(req, timeout=45):
        nonlocal captured_payload
        captured_payload = json.loads(req.data.decode("utf-8"))
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.headers = {
            "anthropic-ratelimit-requests-limit": "1000",
            "anthropic-ratelimit-tokens-limit": "100000"
        }
        mock_resp.read.return_value = json.dumps({
            "content": [
                {"type": "thinking", "thinking": "Internal reasoning"},
                {"type": "text", "text": "Synthesized output"}
            ],
            "usage": {"input_tokens": 10, "output_tokens": 20}
        }).encode("utf-8")
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = AnthropicAdapter.complete(
            prompt="Analyze scenario",
            model="claude-3-5-sonnet-20241022",
            thinking_budget=3000,
            max_tokens=2000
        )

    assert res["status"] == "success"
    assert res["text"] == "Synthesized output"
    assert res["thinking"] == "Internal reasoning"
    assert "thinking" in captured_payload
    assert captured_payload["thinking"]["type"] == "enabled"
    assert captured_payload["thinking"]["budget_tokens"] == 3000
    # max_tokens must have been automatically adjusted to be > budget_tokens
    assert captured_payload["max_tokens"] > 3000


def test_anthropic_missing_key_raises_value_error(monkeypatch):
    """Verify get_headers raises ValueError when no Anthropic keys are configured."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CONSOLE_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_OAT_TOKEN", raising=False)

    with pytest.raises(ValueError) as exc_info:
        AnthropicAdapter.get_headers()
    assert "ANTHROPIC_API_KEY missing" in str(exc_info.value)
