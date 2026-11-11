"""
test_arena_cluster.py — Unit Tests for Arena Connection Cluster & Warm TLS Engine.
"""

import json
from unittest.mock import MagicMock, patch
import pytest

from agent.modules.arena_cluster import (
    ArenaClusterClient,
    ArenaConnectionCluster,
    ArenaError,
    ArenaAuthenticationError,
    ArenaRateLimitError,
    resolve_target_model,
)


def test_arena_alias_and_singleton():
    assert ArenaConnectionCluster is ArenaClusterClient
    instance1 = ArenaClusterClient.get_instance()
    instance2 = ArenaClusterClient.get_instance()
    assert instance1 is instance2


def test_resolve_target_model():
    assert resolve_target_model("claude-opus-5.5") == "claude-opus-5-5"
    assert resolve_target_model("claude-sonnet-5.5") == "claude-sonnet-5-5"
    assert resolve_target_model("gpt-6") == "gpt-6-astra"
    assert resolve_target_model("coding-router-preview") == "coding-router-preview"


def test_arena_cluster_headers():
    client = ArenaClusterClient(base_url="https://api.arena.ai/v1")
    with patch.dict("os.environ", {"ARENA_API_KEY": "arena_test_key_12345"}):
        headers = client.get_headers()
        assert headers["Authorization"] == "Bearer arena_test_key_12345"
        assert headers["User-Agent"] == "ShortsAgent-ArenaRouter/1.0"
        assert headers["X-Workspace-Identity"] == "pankajbhati140@gmail.com"
        assert headers["X-Organization-Name"] == "ShortsForge-Enterprise"
        assert headers["Connection"] == "keep-alive"


def test_arena_cluster_auth_missing():
    client = ArenaClusterClient(base_url="https://api.arena.ai/v1")
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ArenaAuthenticationError):
            client.get_headers()


def test_arena_cluster_warm_pool():
    client = ArenaClusterClient(pool_connections=10, pool_maxsize=25)
    session = client._get_session()
    assert session is not None
    adapter = session.adapters["https://"]
    assert adapter._pool_connections == 10
    assert adapter._pool_maxsize == 25


def test_arena_cluster_ping_success():
    client = ArenaClusterClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"id": "model-1"}, {"id": "model-2"}]}

    with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
        with patch.object(client._get_session(), "get", return_value=mock_resp):
            res = client.ping(timeout=5)
            assert res["status"] == "ONLINE"
            assert res["authenticated"] is True
            assert res["models_count"] == 2
            assert res["latency_ms"] >= 0


def test_arena_cluster_ping_degraded():
    client = ArenaClusterClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Forbidden"

    with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
        with patch.object(client._get_session(), "get", return_value=mock_resp):
            res = client.ping(timeout=5)
            assert res["status"] == "DEGRADED"
            assert res["authenticated"] is False
            assert res["http_status"] == 403


def test_arena_cluster_send_request_mocked():
    client = ArenaClusterClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": "chatcmpl-test",
        "model": "gpt-6-astra",
        "choices": [
            {"message": {"role": "assistant", "content": "Hello from Arena H100!"}, "finish_reason": "stop"}
        ],
        "usage": {"total_tokens": 25, "cost": 0.00015},
    }

    with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
        with patch.object(client._get_session(), "post", return_value=mock_resp) as mock_post:
            res = client.send_request(
                model="gpt-6",
                messages=[{"role": "user", "content": "Hello"}],
                system="You are a helpful assistant",
                reasoning_effort="high",
            )
            assert res["choices"][0]["message"]["content"] == "Hello from Arena H100!"
            # Verify payload was mapped to gpt-6-astra
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["model"] == "gpt-6-astra"
            assert call_kwargs["json"]["reasoning_effort"] == "high"


def test_arena_cluster_streaming_mocked():
    client = ArenaClusterClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_lines.return_value = [
        'data: {"choices":[{"delta":{"content":"Hi"}}]}',
        'data: {"choices":[{"delta":{"content":" there!"}}]}',
        "data: [DONE]",
    ]

    with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
        with patch.object(client._get_session(), "post", return_value=mock_resp):
            generator = client.send_request(
                model="coding-router-preview",
                messages=[{"role": "user", "content": "Hi"}],
                stream=True,
            )
            chunks = list(generator)
            assert len(chunks) == 3
            assert 'data: {"choices":[{"delta":{"content":"Hi"}}]}' in chunks[0]


def test_arena_cluster_402_fallback_to_router():
    client = ArenaClusterClient()

    # Call 1: HTTP 402 with paid_credits_required on direct dedicated model claude-fable-5-1
    resp_402 = MagicMock()
    resp_402.status_code = 402
    resp_402.text = "paid_credits_required"
    resp_402.json.return_value = {
        "error": {
            "code": "paid_credits_required",
            "message": "Direct model usage needs a promotional (all models) or purchased USD balance.",
        }
    }

    # Call 2: HTTP 200 on fallback model claude-coding-router-preview
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {
        "id": "msg-router",
        "model": "claude-coding-router-preview",
        "choices": [{"message": {"role": "assistant", "content": "Router fallback response"}}],
        "usage": {"total_tokens": 30, "cost": 0.00012},
    }

    with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
        with patch.object(client._get_session(), "post", side_effect=[resp_402, resp_200]) as mock_post:
            res = client.send_request(
                model="claude-fable-5-1",
                messages=[{"role": "user", "content": "Test"}],
            )
            assert res["choices"][0]["message"]["content"] == "Router fallback response"
            assert res.get("fallback_used") is True
            assert res.get("requested_model") == "claude-fable-5-1"
            # Verify retry was made with claude-coding-router-preview
            assert mock_post.call_count == 2
            second_call_json = mock_post.call_args_list[1][1]["json"]
            assert second_call_json["model"] == "claude-coding-router-preview"


def test_arena_cluster_complete_interface():
    client = ArenaClusterClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": "chatcmpl-test",
        "model": "coding-router-preview",
        "choices": [{"message": {"role": "assistant", "content": "Completed text."}}],
        "usage": {"total_tokens": 15, "cost": 0.00008},
    }

    with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
        with patch.object(client._get_session(), "post", return_value=mock_resp):
            res = client.complete(
                prompt="Write a test",
                system="Be concise",
                model="coding-router-preview",
                effort="high",
            )
            assert res["provider"] == "arena"
            assert res["text"] == "Completed text."
            assert res["status"] == "success"
            assert res["cost"] == 0.00008
            assert res["model"] == "coding-router-preview"
