"""
test_challenger_m1_verification.py — Adversarial Stress Tests & Empirical Verification for Milestone 1.

Authored by Challenger 1 (EMPIRICAL CHALLENGER).
Covers:
  1. Arena Warm TLS Session Pooling & Socket Persistence
  2. Cloudflare WAF Bypass Header Validation Across All Operations
  3. HTTP 402 Fallback Fidelity & Non-fallback Error Handling
  4. Streaming SSE & Client Abort Lifecycle
  5. Anthropic Organization Workspace Scoping Matrix & Discovery Fallback
  6. Model Discovery 'id' Invariant Across Live & Baseline Records
  7. OmniRoute Model Slug Preservation & Anti-Clobbering Stress
  8. Model-Preserving Same-Model Cascade Matrix (IMRCM)
  9. Capability Registry 5-Model Powerhouse Alias, Dot/Hyphen, and Case Invariants
 10. Canonical Model Target Resolution Stress
"""

import json
import os
import time
import urllib.error
from unittest.mock import MagicMock, call, patch
import pytest

from agent.modules.arena_cluster import (
    ArenaClusterClient,
    ArenaConnectionCluster,
    ArenaError,
    ArenaAuthenticationError,
    ArenaRateLimitError,
    resolve_target_model,
)
from agent.modules.anthropic_adapter import AnthropicAdapter
from agent.modules.omniroute_embedded import EmbeddedOmniRoute
from agent.modules.capability_registry import (
    CANONICAL_MODELS,
    UniversalCapabilityRegistry,
)
from agent.modules.model_discovery import (
    DynamicModelDiscovery,
    ModelEndpointProbe,
)


# ==============================================================================
# 1. Arena Warm TLS Session Pooling & Socket Persistence
# ==============================================================================

class TestArenaWarmTLSPooling:
    """Stress-tests socket pooling, keep-alive persistence, and connection reuse."""

    def test_session_adapter_pooling_configuration(self):
        client = ArenaClusterClient(pool_connections=15, pool_maxsize=30, timeout=20)
        session = client._get_session()
        assert session is not None

        # Verify adapter is mounted for both https:// and http://
        https_adapter = session.adapters.get("https://")
        http_adapter = session.adapters.get("http://")
        assert https_adapter is not None
        assert http_adapter is not None

        assert https_adapter._pool_connections == 15
        assert https_adapter._pool_maxsize == 30
        assert https_adapter.max_retries.total == 3
        assert 429 in https_adapter.max_retries.status_forcelist
        assert 500 in https_adapter.max_retries.status_forcelist

    def test_session_socket_persistence_across_multiple_requests(self):
        """Verifies the exact same requests.Session instance is reused across invocations."""
        client = ArenaClusterClient()
        s1 = client._get_session()
        s2 = client._get_session()
        assert s1 is s2, "requests.Session instance must be persistent and cached"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "res"}}],
            "usage": {"total_tokens": 10, "cost": 0.0},
        }

        with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
            with patch.object(s1, "post", return_value=mock_resp) as mock_post:
                # Dispatch 5 sequential calls
                for i in range(5):
                    client.complete(prompt=f"ping {i}", model="coding-router-preview")

                assert mock_post.call_count == 5
                # Verify that each call went through the exact same session instance
                for c in mock_post.call_args_list:
                    assert c[1]["headers"]["Connection"] == "keep-alive"

    def test_warm_pool_probe_success_and_failure(self):
        client = ArenaClusterClient()
        session = client._get_session()

        mock_200 = MagicMock()
        mock_200.status_code = 200

        mock_500 = MagicMock()
        mock_500.status_code = 500

        with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
            with patch.object(session, "get", return_value=mock_200):
                assert client.warm_pool() is True

            with patch.object(session, "get", return_value=mock_500):
                assert client.warm_pool() is False

            with patch.object(session, "get", side_effect=Exception("Connection refused")):
                assert client.warm_pool() is False

    def test_ping_latency_tracking_and_status_codes(self):
        client = ArenaClusterClient()
        session = client._get_session()

        with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
            # Test 200 ONLINE
            mock_200 = MagicMock()
            mock_200.status_code = 200
            mock_200.json.return_value = {"data": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]}
            with patch.object(session, "get", return_value=mock_200):
                res = client.ping(timeout=5)
                assert res["status"] == "ONLINE"
                assert res["authenticated"] is True
                assert res["models_count"] == 3
                assert isinstance(res["latency_ms"], int)
                assert res["latency_ms"] >= 0

            # Test 401 DEGRADED
            mock_401 = MagicMock()
            mock_401.status_code = 401
            mock_401.text = "Unauthorized"
            with patch.object(session, "get", return_value=mock_401):
                res = client.ping(timeout=5)
                assert res["status"] == "DEGRADED"
                assert res["authenticated"] is False
                assert res["http_status"] == 401

            # Test 503 DEGRADED
            mock_503 = MagicMock()
            mock_503.status_code = 503
            with patch.object(session, "get", return_value=mock_503):
                res = client.ping(timeout=5)
                assert res["status"] == "DEGRADED"
                assert res["authenticated"] is True
                assert res["http_status"] == 503

    def test_close_resets_session_pool(self):
        client = ArenaClusterClient()
        session = client._get_session()
        assert client._session is not None
        client.close()
        assert client._session is None


# ==============================================================================
# 2. Cloudflare WAF Bypass Header Validation Across All Operations
# ==============================================================================

class TestCloudflareWAFHeaders:
    """Verifies that every outgoing network operation carries bypass and identity headers."""

    def test_headers_structure_and_types(self):
        client = ArenaClusterClient()
        with patch.dict("os.environ", {"ARENA_API_KEY": "sk_arena_test_888"}):
            headers = client.get_headers()
            assert headers["User-Agent"] == "ShortsAgent-ArenaRouter/1.0"
            assert headers["X-Workspace-Identity"] == "pankajbhati140@gmail.com"
            assert headers["X-Organization-Name"] == "ShortsForge-Enterprise"
            assert headers["Authorization"] == "Bearer sk_arena_test_888"
            assert headers["Content-Type"] == "application/json"
            assert headers["Connection"] == "keep-alive"

    def test_warm_pool_sends_waf_headers(self):
        client = ArenaClusterClient()
        session = client._get_session()
        mock_resp = MagicMock(status_code=200)

        with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
            with patch.object(session, "get", return_value=mock_resp) as mock_get:
                client.warm_pool()
                mock_get.assert_called_once()
                headers = mock_get.call_args[1]["headers"]
                assert headers["User-Agent"] == "ShortsAgent-ArenaRouter/1.0"
                assert headers["X-Workspace-Identity"] == "pankajbhati140@gmail.com"

    def test_ping_sends_waf_headers(self):
        client = ArenaClusterClient()
        session = client._get_session()
        mock_resp = MagicMock(status_code=200, json=lambda: {"data": []})

        with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
            with patch.object(session, "get", return_value=mock_resp) as mock_get:
                client.ping()
                mock_get.assert_called_once()
                headers = mock_get.call_args[1]["headers"]
                assert headers["User-Agent"] == "ShortsAgent-ArenaRouter/1.0"
                assert headers["X-Workspace-Identity"] == "pankajbhati140@gmail.com"

    def test_discover_models_sends_waf_headers(self):
        client = ArenaClusterClient()
        session = client._get_session()
        mock_resp = MagicMock(status_code=200, json=lambda: {"data": [{"id": "m1"}]})

        with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
            with patch.object(session, "get", return_value=mock_resp) as mock_get:
                client.discover_models(force_refresh=True)
                mock_get.assert_called_once()
                headers = mock_get.call_args[1]["headers"]
                assert headers["User-Agent"] == "ShortsAgent-ArenaRouter/1.0"
                assert headers["X-Workspace-Identity"] == "pankajbhati140@gmail.com"

    def test_send_request_and_retry_sends_waf_headers(self):
        client = ArenaClusterClient()
        session = client._get_session()

        resp_402 = MagicMock(status_code=402, text="paid_credits_required")
        resp_402.json.return_value = {"error": {"code": "paid_credits_required"}}

        resp_200 = MagicMock(status_code=200)
        resp_200.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {},
        }

        with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
            with patch.object(session, "post", side_effect=[resp_402, resp_200]) as mock_post:
                client.send_request("claude-fable-5-1", [{"role": "user", "content": "hi"}])
                assert mock_post.call_count == 2
                for c in mock_post.call_args_list:
                    h = c[1]["headers"]
                    assert h["User-Agent"] == "ShortsAgent-ArenaRouter/1.0"
                    assert h["X-Workspace-Identity"] == "pankajbhati140@gmail.com"
                    assert h["X-Organization-Name"] == "ShortsForge-Enterprise"


# ==============================================================================
# 3. HTTP 402 Fallback Fidelity & Error Boundaries
# ==============================================================================

class TestArena402FallbackFidelity:
    """Stress-tests HTTP 402 paid_credits_required automatic failover behavior."""

    def test_claude_family_falls_back_to_claude_coding_router(self):
        client = ArenaClusterClient()
        session = client._get_session()

        claude_targets = [
            "claude-fable-5-1",
            "claude-opus-5.5",
            "claude-sonnet-5.5",
            "claude-haiku-4-5-20251001",
        ]

        for target in claude_targets:
            resp_402 = MagicMock(status_code=402, text="paid_credits_required")
            resp_402.json.return_value = {"error": {"code": "paid_credits_required"}}

            resp_200 = MagicMock(status_code=200)
            resp_200.json.return_value = {
                "id": "msg-fallback",
                "model": "claude-coding-router-preview",
                "choices": [{"message": {"role": "assistant", "content": f"Resolved {target}"}}],
                "usage": {"total_tokens": 50, "cost": 0.0001},
            }

            with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
                with patch.object(session, "post", side_effect=[resp_402, resp_200]) as mock_post:
                    res = client.send_request(target, [{"role": "user", "content": "solve"}])
                    assert res["fallback_used"] is True
                    assert res["requested_model"] == target
                    assert res["choices"][0]["message"]["content"] == f"Resolved {target}"
                    # Second call must use claude-coding-router-preview
                    second_payload = mock_post.call_args_list[1][1]["json"]
                    assert second_payload["model"] == "claude-coding-router-preview"

    def test_general_models_fall_back_to_coding_router_preview(self):
        client = ArenaClusterClient()
        session = client._get_session()

        general_targets = ["gpt-6", "gemini-4", "deepseek-v4"]

        for target in general_targets:
            resp_402 = MagicMock(status_code=402, text="paid_credits_required")
            resp_402.json.return_value = {"error": {"code": "paid_credits_required"}}

            resp_200 = MagicMock(status_code=200)
            resp_200.json.return_value = {
                "id": "msg-general",
                "model": "coding-router-preview",
                "choices": [{"message": {"role": "assistant", "content": f"General {target}"}}],
                "usage": {"total_tokens": 40, "cost": 0.0001},
            }

            with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
                with patch.object(session, "post", side_effect=[resp_402, resp_200]) as mock_post:
                    res = client.send_request(target, [{"role": "user", "content": "solve"}])
                    assert res["fallback_used"] is True
                    assert res["requested_model"] == target
                    second_payload = mock_post.call_args_list[1][1]["json"]
                    assert second_payload["model"] == "coding-router-preview"

    def test_non_paid_credits_402_raises_arena_error(self):
        """If 402 is caused by another billing issue, it should raise ArenaError, NOT fall back."""
        client = ArenaClusterClient()
        session = client._get_session()

        resp_402_other = MagicMock(status_code=402, text="card_declined")
        resp_402_other.json.return_value = {"error": {"code": "card_declined", "message": "Card failed"}}

        with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
            with patch.object(session, "post", return_value=resp_402_other):
                with pytest.raises(ArenaError) as exc_info:
                    client.send_request("claude-fable-5-1", [{"role": "user", "content": "hi"}])
                assert "HTTP 402" in str(exc_info.value)

    def test_complete_propagates_fallback_flag(self):
        """Verifies high-level complete() propagates fallback_used metadata."""
        client = ArenaClusterClient()
        session = client._get_session()

        resp_402 = MagicMock(status_code=402, text="paid_credits_required")
        resp_402.json.return_value = {"error": {"code": "paid_credits_required"}}

        resp_200 = MagicMock(status_code=200)
        resp_200.json.return_value = {
            "model": "claude-coding-router-preview",
            "choices": [{"message": {"role": "assistant", "content": "High level fallback"}}],
            "usage": {"cost": 0.0005},
        }

        with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
            with patch.object(session, "post", side_effect=[resp_402, resp_200]):
                res = client.complete(prompt="Run test", model="claude-fable-5-1")
                assert res["status"] == "success"
                assert res["fallback_used"] is True
                assert res["requested_model"] == "claude-fable-5-1"
                assert res["text"] == "High level fallback"


# ==============================================================================
# 4. Streaming SSE & Client Abort Lifecycle
# ==============================================================================

class TestArenaStreamingSSE:
    """Stress-tests SSE stream generation, chunk filtering, and client aborts."""

    def test_streaming_generator_yields_clean_lines(self):
        client = ArenaClusterClient()
        session = client._get_session()

        raw_lines = [
            'data: {"id":"1","choices":[{"delta":{"content":"Hello"}}]}',
            "",  # empty lines between SSE events
            'data: {"id":"2","choices":[{"delta":{"content":" world"}}]}',
            "",
            "data: [DONE]",
        ]

        mock_resp = MagicMock(status_code=200)
        mock_resp.iter_lines.return_value = raw_lines

        with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
            with patch.object(session, "post", return_value=mock_resp):
                gen = client.send_request(
                    model="coding-router-preview",
                    messages=[{"role": "user", "content": "Say hi"}],
                    stream=True,
                )
                chunks = list(gen)
                assert len(chunks) == 3
                assert 'content":"Hello"' in chunks[0]
                assert 'content":" world"' in chunks[1]
                assert chunks[2] == "data: [DONE]"

    def test_streaming_generator_client_abort_handling(self):
        """Simulates consumer closing generator early (e.g. client disconnect)."""
        client = ArenaClusterClient()
        session = client._get_session()

        mock_resp = MagicMock(status_code=200)
        mock_resp.iter_lines.return_value = [f"data: chunk {i}" for i in range(100)]

        with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
            with patch.object(session, "post", return_value=mock_resp):
                gen = client.send_request(
                    model="coding-router-preview",
                    messages=[{"role": "user", "content": "Long stream"}],
                    stream=True,
                )
                first_chunk = next(gen)
                assert first_chunk == "data: chunk 0"
                # Abort generator
                gen.close()
                with pytest.raises(StopIteration):
                    next(gen)

    def test_streaming_with_402_fallback(self):
        """When streaming request gets 402, it retries and returns streaming generator."""
        client = ArenaClusterClient()
        session = client._get_session()

        resp_402 = MagicMock(status_code=402, text="paid_credits_required")
        resp_402.json.return_value = {"error": {"code": "paid_credits_required"}}

        resp_stream = MagicMock(status_code=200)
        resp_stream.iter_lines.return_value = ["data: stream from fallback", "data: [DONE]"]

        with patch.dict("os.environ", {"ARENA_API_KEY": "test_key"}):
            with patch.object(session, "post", side_effect=[resp_402, resp_stream]):
                gen = client.send_request(
                    model="claude-fable-5-1",
                    messages=[{"role": "user", "content": "stream"}],
                    stream=True,
                )
                chunks = list(gen)
                assert len(chunks) == 2
                assert "stream from fallback" in chunks[0]


# ==============================================================================
# 5. Anthropic Organization Workspace Scoping Matrix & Discovery Fallback
# ==============================================================================

class TestAnthropicWorkspaceScoping:
    """Stress-tests workspace resolution across all permutations of environment and network."""

    @pytest.fixture(autouse=True)
    def clean_anthropic_state(self):
        AnthropicAdapter._cached_workspace_id = None
        yield
        AnthropicAdapter._cached_workspace_id = None

    def test_workspace_matrix_explicit_anthropic_workspace_id(self):
        with patch.dict("os.environ", {
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "ANTHROPIC_WORKSPACE_ID": "wrkspc_explicit_111",
        }, clear=True):
            assert AnthropicAdapter.get_workspace_id() == "wrkspc_explicit_111"
            headers = AnthropicAdapter.get_headers()
            assert headers["anthropic-workspace-id"] == "wrkspc_explicit_111"

    def test_workspace_matrix_claude_workspace_id_alias(self):
        with patch.dict("os.environ", {
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "CLAUDE_WORKSPACE_ID": "wrkspc_alias_222",
        }, clear=True):
            assert AnthropicAdapter.get_workspace_id() == "wrkspc_alias_222"
            headers = AnthropicAdapter.get_headers()
            assert headers["anthropic-workspace-id"] == "wrkspc_alias_222"

    def test_workspace_matrix_live_discovery_caching(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "data": [{"id": "wrkspc_dynamic_333", "display_name": "Dynamic"}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True):
            with patch("urllib.request.urlopen", return_value=mock_resp) as mock_url:
                ws_id = AnthropicAdapter.get_workspace_id()
                assert ws_id == "wrkspc_dynamic_333"
                assert AnthropicAdapter._cached_workspace_id == "wrkspc_dynamic_333"
                assert mock_url.call_count == 1

                # Second call must use cache without calling urlopen
                ws_id_2 = AnthropicAdapter.get_workspace_id()
                assert ws_id_2 == "wrkspc_dynamic_333"
                assert mock_url.call_count == 1

    def test_workspace_matrix_network_failure_fallback_to_verified_default(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True):
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("DNS lookup failed")):
                ws_id = AnthropicAdapter.get_workspace_id()
                assert ws_id == "wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm"
                assert ws_id == AnthropicAdapter.DEFAULT_WORKSPACE_ID
                headers = AnthropicAdapter.get_headers()
                assert headers["anthropic-workspace-id"] == "wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm"

    def test_workspace_matrix_missing_api_key_raises_value_error(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError) as exc:
                AnthropicAdapter.get_headers()
            assert "ANTHROPIC_API_KEY missing" in str(exc.value)


# ==============================================================================
# 6. Model Discovery 'id' Invariant Across Live & Baseline Records
# ==============================================================================

class TestAnthropicModelDiscoveryIdInvariant:
    """Verifies that 'id' exists on all discovery records and matches contract."""

    def test_live_discovery_records_contain_id_and_canonical_id(self):
        live_data = {
            "data": [
                {"id": "claude-3-5-sonnet-20241022", "display_name": "Claude 3.5 Sonnet"},
                {"id": "claude-3-5-haiku-20241022", "display_name": "Claude 3.5 Haiku"},
                {"id": "claude-3-7-sonnet-20250219", "display_name": "Claude 3.7 Sonnet"},
                {"id": "claude-opus-4-0", "display_name": "Claude Opus 4.0"},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(live_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test", "ANTHROPIC_WORKSPACE_ID": "wrkspc_test"}):
            with patch("urllib.request.urlopen", return_value=mock_resp):
                records = AnthropicAdapter.discover_models()
                assert len(records) == 4
                for r in records:
                    assert "id" in r, "Record missing required 'id' key"
                    assert "canonical_id" in r, "Record missing 'canonical_id' key"
                    assert r["id"] == r["canonical_id"]
                    assert isinstance(r["id"], str) and len(r["id"]) > 0
                    assert r["provider"] == "anthropic"

    def test_fallback_discovery_records_contain_id_and_canonical_id(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test", "ANTHROPIC_WORKSPACE_ID": "wrkspc_test"}):
            with patch("urllib.request.urlopen", side_effect=Exception("Anthropic API 500")):
                records = AnthropicAdapter.discover_models()
                assert len(records) >= 2
                for r in records:
                    assert "id" in r
                    assert "canonical_id" in r
                    assert r["id"] == r["canonical_id"]
                    assert isinstance(r["id"], str) and len(r["id"]) > 0


# ==============================================================================
# 7. OmniRoute Model Slug Preservation & Anti-Clobbering Stress
# ==============================================================================

class TestOmniRouteSlugPreservation:
    """Stress-tests OpenRouter and Groq model slug integrity against clobbering bugs."""

    def test_openrouter_paid_frontier_slugs_never_appended_with_free(self):
        router = EmbeddedOmniRoute()

        frontier_models = [
            "anthropic/claude-3.5-sonnet",
            "anthropic/claude-3-5-haiku",
            "openai/gpt-4o",
            "openai/o3-mini",
            "meta-llama/llama-3.1-405b-instruct",
            "mistralai/mistral-large-2411",
            "x-ai/grok-2-1212",
        ]

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "res"}}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test"}):
            with patch("urllib.request.urlopen", return_value=mock_resp) as mock_url:
                for target_model in frontier_models:
                    res = router._call_openrouter_text("prompt", None, target_model, 10)
                    assert res["model"] == target_model
                    assert not res["model"].endswith(":free")

                    sent_req = mock_url.call_args[0][0]
                    sent_payload = json.loads(sent_req.data.decode("utf-8"))
                    assert sent_payload["model"] == target_model
                    assert not sent_payload["model"].endswith(":free")

    def test_openrouter_empty_model_uses_free_default(self):
        router = EmbeddedOmniRoute()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "res"}}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test"}):
            with patch("urllib.request.urlopen", return_value=mock_resp) as mock_url:
                res = router._call_openrouter_text("prompt", None, "", 10)
                assert res["model"] == "deepseek/deepseek-r1:free"
                sent_req = mock_url.call_args[0][0]
                sent_payload = json.loads(sent_req.data.decode("utf-8"))
                assert sent_payload["model"] == "deepseek/deepseek-r1:free"

    def test_groq_gpt_oss_120b_and_custom_models_never_clobbered(self):
        router = EmbeddedOmniRoute()

        groq_models = [
            "openai/gpt-oss-120b",
            "llama-3.3-70b-versatile",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        ]

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "groq_res"}}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch.dict("os.environ", {"GROQ_API_KEY": "gsk_test"}):
            with patch("urllib.request.urlopen", return_value=mock_resp) as mock_url:
                for gm in groq_models:
                    res = router._call_groq_text("prompt", None, gm, 10)
                    assert res["model"] == gm

                    sent_req = mock_url.call_args[0][0]
                    sent_payload = json.loads(sent_req.data.decode("utf-8"))
                    assert sent_payload["model"] == gm

    def test_groq_default_fallback_to_gpt_oss_120b(self):
        router = EmbeddedOmniRoute()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "groq_res"}}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch.dict("os.environ", {"GROQ_API_KEY": "gsk_test"}):
            with patch("urllib.request.urlopen", return_value=mock_resp) as mock_url:
                res = router._call_groq_text("prompt", None, "gpt-5.4-nano", 10)
                assert res["model"] == "openai/gpt-oss-120b"
                sent_req = mock_url.call_args[0][0]
                sent_payload = json.loads(sent_req.data.decode("utf-8"))
                assert sent_payload["model"] == "openai/gpt-oss-120b"


# ==============================================================================
# 8. Model-Preserving Same-Model Cascade Matrix (IMRCM)
# ==============================================================================

class TestSameModelCascadeMatrix:
    """Stress-tests the Model-Preserving Same-Model Cascade across provider outages."""

    def test_claude_sonnet_cascade_arena_to_openrouter(self):
        router = EmbeddedOmniRoute()
        call_log = []

        def mock_arena(*args, **kwargs):
            call_log.append("arena")
            raise RuntimeError("HTTP 429 Too Many Requests")

        def mock_openrouter(*args, **kwargs):
            call_log.append("openrouter")
            return {
                "provider": "openrouter",
                "model": "anthropic/claude-3.5-sonnet",
                "cost": 0.003,
                "text": "Success on OpenRouter",
                "status": "success",
            }

        with patch.dict("os.environ", {
            "ARENA_API_KEY": "key1",
            "OPENROUTER_API_KEY": "key2",
        }):
            with patch.object(router, "_call_arena_text", side_effect=mock_arena):
                with patch.object(router, "_call_openrouter_text", side_effect=mock_openrouter):
                    res = router.complete_text(prompt="task", model="claude-3-5-sonnet")
                    assert res["status"] == "success"
                    assert res["provider"] == "openrouter"
                    assert call_log == ["arena", "openrouter"]
                    assert len(res["attempt_history"]) == 1
                    assert res["attempt_history"][0]["provider"] == "arena"

    def test_claude_sonnet_cascade_arena_and_openrouter_to_anthropic_direct(self):
        router = EmbeddedOmniRoute()
        call_log = []

        def mock_arena(*args, **kwargs):
            call_log.append("arena")
            raise RuntimeError("HTTP 429 Rate limited")

        def mock_openrouter(*args, **kwargs):
            call_log.append("openrouter")
            raise RuntimeError("HTTP 500 Internal error")

        mock_anthropic_resp = {
            "provider": "anthropic",
            "model": "claude-3-5-sonnet-20241022",
            "text": "Success on Anthropic Direct",
            "status": "success",
        }

        with patch.dict("os.environ", {
            "ARENA_API_KEY": "key1",
            "OPENROUTER_API_KEY": "key2",
            "ANTHROPIC_API_KEY": "key3",
        }):
            with patch.object(router, "_call_arena_text", side_effect=mock_arena):
                with patch.object(router, "_call_openrouter_text", side_effect=mock_openrouter):
                    with patch("agent.modules.anthropic_adapter.AnthropicAdapter.complete", return_value=mock_anthropic_resp):
                        res = router.complete_text(prompt="task", model="claude-3-5-sonnet")
                        assert res["status"] == "success"
                        assert res["provider"] == "anthropic"
                        assert call_log == ["arena", "openrouter"]
                        assert len(res["attempt_history"]) == 2

    def test_all_same_model_routes_fail_cascades_to_pollinations(self):
        router = EmbeddedOmniRoute()

        def mock_fail(prov):
            def _fn(*args, **kwargs):
                raise RuntimeError(f"HTTP 503 Outage on {prov}")
            return _fn

        mock_pollinations_resp = {
            "provider": "pollinations",
            "model": "claude-3-5-sonnet",
            "text": "Pollinations resilient peer fallback",
            "status": "success",
        }

        with patch.dict("os.environ", {
            "ARENA_API_KEY": "k1",
            "OPENROUTER_API_KEY": "k2",
        }):
            with patch.object(router, "_call_arena_text", side_effect=mock_fail("arena")):
                with patch.object(router, "_call_openrouter_text", side_effect=mock_fail("openrouter")):
                    with patch.object(router, "_call_pollinations_text", return_value=mock_pollinations_resp):
                        res = router.complete_text(prompt="task", model="claude-3-5-sonnet")
                        assert res["status"] == "success"
                        assert res["provider"] == "pollinations"
                        assert "Pollinations resilient" in res["text"]
                        assert len(res["attempt_history"]) >= 2


# ==============================================================================
# 9. Capability Registry 5-Model Powerhouse Alias, Dot/Hyphen, and Case Invariants
# ==============================================================================

class TestCapabilityRegistry5Models:
    """Stress-tests the 5 newly hired models across dots, hyphens, case, and pricing."""

    FIVE_MODELS = [
        "claude-3-5-haiku",
        "mistral-large-2411",
        "deepseek-v3",
        "llama-3.1-405b",
        "grok-2",
        "gemini-1.5-pro",
    ]

    def test_all_models_present_with_canonical_specifications(self):
        for m in self.FIVE_MODELS:
            canonical = UniversalCapabilityRegistry.resolve_canonical_id(m)
            assert canonical is not None, f"Could not resolve canonical id for {m}"
            spec = CANONICAL_MODELS[canonical]
            assert spec["canonical_id"] == canonical
            assert spec["context_window"] >= 64000
            assert spec["max_output_tokens"] >= 8192
            assert "pricing" in spec
            assert spec["pricing"]["currency"] == "USD"
            assert "tags" in spec and len(spec["tags"]) > 0

    def test_dot_hyphen_and_case_permutations(self):
        permutations = [
            ("claude-3.5-haiku", "claude-3-5-haiku"),
            ("CLAUDE-3.5-HAIKU", "claude-3-5-haiku"),
            ("claude-haiku-3.5", "claude-3-5-haiku"),
            ("mistral-large-2411", "mistral-large-2411"),
            ("MISTRAL-LARGE-2", "mistral-large-2411"),
            ("mistralai/mistral-large-2411", "mistral-large-2411"),
            ("deepseek-v3", "deepseek-v3"),
            ("DEEPSEEK-CHAT", "deepseek-v3"),
            ("deepseek-ai/deepseek-v3", "deepseek-v3"),
            ("llama-3.1-405b", ["llama-3.1-405b", "llama-3-1-405b"]),
            ("LLAMA-3-1-405B", ["llama-3.1-405b", "llama-3-1-405b"]),
            ("meta-llama-3.1-405b", ["llama-3.1-405b", "llama-3-1-405b"]),
            ("llama-405b", ["llama-3.1-405b", "llama-3-1-405b"]),
            ("grok-2", "grok-2"),
            ("GROK-2-1212", "grok-2"),
            ("x-ai/grok-2", "grok-2"),
            ("gemini-1.5-pro", ["gemini-1.5-pro", "gemini-1-5-pro"]),
            ("GEMINI-1-5-PRO", ["gemini-1.5-pro", "gemini-1-5-pro"]),
            ("gemini-pro-1.5", ["gemini-1.5-pro", "gemini-1-5-pro"]),
        ]

        for query, expected in permutations:
            resolved = UniversalCapabilityRegistry.resolve_canonical_id(query)
            if isinstance(expected, list):
                assert resolved in expected, f"Query '{query}' resolved to '{resolved}', expected one of {expected}"
            else:
                assert resolved == expected, f"Query '{query}' resolved to '{resolved}', expected '{expected}'"

    def test_pricing_accuracy_for_requested_models(self):
        """Verifies pricing per million tokens matches user directives."""
        # claude-3-5-haiku: $0.80 input / $4.00 output
        haiku = CANONICAL_MODELS["claude-3-5-haiku"]["pricing"]
        assert haiku["input_per_million"] == 0.80
        assert haiku["output_per_million"] == 4.00

        # mistral-large-2411: $2.00 input / $6.00 output
        mistral = CANONICAL_MODELS["mistral-large-2411"]["pricing"]
        assert mistral["input_per_million"] == 2.00
        assert mistral["output_per_million"] == 6.00

        # deepseek-v3: $0.14 input / $0.28 output
        ds = CANONICAL_MODELS["deepseek-v3"]["pricing"]
        assert ds["input_per_million"] == 0.14
        assert ds["output_per_million"] == 0.28

        # llama-3.1-405b: $1.79 input / $1.79 output
        llama = CANONICAL_MODELS["llama-3.1-405b"]["pricing"]
        assert llama["input_per_million"] == 1.79
        assert llama["output_per_million"] == 1.79

        # grok-2: $2.00 input / $10.00 output
        grok = CANONICAL_MODELS["grok-2"]["pricing"]
        assert grok["input_per_million"] == 2.00
        assert grok["output_per_million"] == 10.00

        # gemini-1.5-pro: $1.25 input / $5.00 output
        gemini = CANONICAL_MODELS["gemini-1.5-pro"]["pricing"]
        assert gemini["input_per_million"] == 1.25
        assert gemini["output_per_million"] == 5.00


# ==============================================================================
# 10. Canonical Model Target Resolution Stress
# ==============================================================================

class TestCanonicalTargetResolution:
    """Stress-tests resolve_target_model in arena_cluster.py for H100 dedicated models."""

    def test_dedicated_h100_mappings(self):
        cases = [
            ("claude-opus-5.5", "claude-opus-5-5"),
            ("claude-sonnet-5.5", "claude-sonnet-5-5"),
            ("claude-haiku-5.5", "claude-haiku-5-5"),
            ("claude-fable-5.1", "claude-fable-5-1"),
            ("claude-fable-5.2", "claude-fable-5-2"),
            ("gpt-6", "gpt-6-astra"),
            ("gpt-6-sol", "gpt-6-sol"),
            ("gpt-6-luna", "gpt-6-luna"),
            ("gpt-5.6", "gpt-5.6-sol"),
            ("gemini-3.7", "gemini-3.7-flash"),
            ("gemini-3.8", "gemini-3.8-flash"),
            ("gemini-4", "gemini-4-carbon"),
            ("", "coding-router-preview"),
            (None, "coding-router-preview"),
            ("custom-unmapped-model", "custom-unmapped-model"),
        ]

        for req, expected in cases:
            assert resolve_target_model(req) == expected
