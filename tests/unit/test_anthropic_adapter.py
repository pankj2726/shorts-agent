"""
test_anthropic_adapter.py — Unit Tests for Anthropic Adapter Workspace Scoping & Telemetry.
"""

import json
from unittest.mock import MagicMock, patch
import pytest

from agent.modules.anthropic_adapter import AnthropicAdapter


@pytest.fixture(autouse=True)
def reset_adapter_cache():
    # Reset cached workspace ID before and after each test
    AnthropicAdapter._cached_workspace_id = None
    yield
    AnthropicAdapter._cached_workspace_id = None


def test_workspace_id_from_environment():
    with patch.dict("os.environ", {
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "ANTHROPIC_WORKSPACE_ID": "wrkspc_custom_env_123"
    }):
        headers = AnthropicAdapter.get_headers()
        assert headers["anthropic-workspace-id"] == "wrkspc_custom_env_123"
        assert AnthropicAdapter.get_workspace_id() == "wrkspc_custom_env_123"


def test_workspace_id_auto_discovery_and_caching():
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "data": [
            {"id": "wrkspc_discovered_456", "name": "Discovered Workspace"}
        ]
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True):
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            ws_id = AnthropicAdapter.get_workspace_id()
            assert ws_id == "wrkspc_discovered_456"
            assert AnthropicAdapter._cached_workspace_id == "wrkspc_discovered_456"
            headers = AnthropicAdapter.get_headers()
            assert headers["anthropic-workspace-id"] == "wrkspc_discovered_456"


def test_workspace_id_fallback_to_default():
    # Simulate discovery endpoint failure (e.g. 404 or network error)
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True):
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            ws_id = AnthropicAdapter.get_workspace_id()
            assert ws_id == AnthropicAdapter.DEFAULT_WORKSPACE_ID
            assert ws_id == "wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm"
            headers = AnthropicAdapter.get_headers()
            assert headers["anthropic-workspace-id"] == AnthropicAdapter.DEFAULT_WORKSPACE_ID


def test_discover_models_returns_id_key_live():
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "data": [
            {"id": "claude-3-7-sonnet-20250219", "display_name": "Claude 3.7 Sonnet"},
            {"id": "claude-3-5-haiku-20241022", "display_name": "Claude 3.5 Haiku"}
        ]
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test", "ANTHROPIC_WORKSPACE_ID": "wrkspc_test"}):
        with patch("urllib.request.urlopen", return_value=mock_resp):
            models = AnthropicAdapter.discover_models()
            assert len(models) == 2
            for m in models:
                assert "id" in m
                assert "canonical_id" in m
                assert m["id"] == m["canonical_id"]


def test_discover_models_returns_id_key_fallback():
    # Probe fails -> fallback baseline
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test", "ANTHROPIC_WORKSPACE_ID": "wrkspc_test"}):
        with patch("urllib.request.urlopen", side_effect=Exception("Failed")):
            models = AnthropicAdapter.discover_models()
            assert len(models) >= 2
            for m in models:
                assert "id" in m
                assert "canonical_id" in m
                assert len(m["id"]) > 0


def test_complete_rate_limit_extraction():
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Anthropic response"}],
        "usage": {"input_tokens": 10, "output_tokens": 5}
    }).encode("utf-8")
    mock_resp.headers = {
        "anthropic-ratelimit-requests-limit": "1000",
        "anthropic-ratelimit-requests-remaining": "999",
        "anthropic-ratelimit-tokens-limit": "100000",
        "content-type": "application/json"
    }
    mock_resp.__enter__.return_value = mock_resp

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test", "ANTHROPIC_WORKSPACE_ID": "wrkspc_test"}):
        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = AnthropicAdapter.complete(prompt="Ping", model="claude-3-5-haiku-20241022")
            assert res["status"] == "success"
            assert res["text"] == "Anthropic response"
            assert "rate_limits" in res
            assert res["rate_limits"]["anthropic-ratelimit-requests-limit"] == "1000"
            assert res["rate_limits"]["anthropic-ratelimit-requests-remaining"] == "999"


def test_extended_thinking_payload_construction():
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "content": [
            {"type": "thinking", "thinking": "Internal reasoning..."},
            {"type": "text", "text": "Final answer"}
        ],
        "usage": {"input_tokens": 20, "output_tokens": 50}
    }).encode("utf-8")
    mock_resp.headers = {}
    mock_resp.__enter__.return_value = mock_resp

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test", "ANTHROPIC_WORKSPACE_ID": "wrkspc_test"}):
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            res = AnthropicAdapter.complete(
                prompt="Think carefully",
                model="claude-3-5-sonnet-20241022",
                effort="high",
                max_tokens=2048,
            )
            assert res["thinking"] == "Internal reasoning..."
            assert res["text"] == "Final answer"
            # Verify payload sent to urlopen
            sent_req = mock_urlopen.call_args[0][0]
            sent_payload = json.loads(sent_req.data.decode("utf-8"))
            assert "thinking" in sent_payload
            assert sent_payload["thinking"]["type"] == "enabled"
            assert sent_payload["thinking"]["budget_tokens"] == 4096
            # max_tokens adjusted because max_tokens <= budget_tokens
            assert sent_payload["max_tokens"] == 4096 + 2048
