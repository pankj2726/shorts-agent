"""
test_adversarial_m1_robustness.py — Adversarial Robustness & Concurrency Stress Test Suite.

Adversarially stress-tests Milestone 1 implementation:
  1. High-concurrency socket reuse and thread-safety in ArenaClusterClient.
  2. Network disruption & fallback resilience (HTTP 429, 500, 402, connection timeouts, cascade).
  3. Edge case inputs (empty messages, giant prompts, unsupported models, malformed JSON, missing keys).
"""

import http.server
import json
import os
import socketserver
import threading
import time
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
import requests

from agent.modules.arena_cluster import (
    ArenaAuthenticationError,
    ArenaClusterClient,
    ArenaError,
    ArenaRateLimitError,
    resolve_target_model,
)
from agent.modules.capability_registry import UniversalCapabilityRegistry
from agent.modules.omniroute_embedded import EmbeddedOmniRoute


# ==============================================================================
# Helper Mock Server for High-Concurrency & Socket Reuse Verification
# ==============================================================================

class ThreadingMockServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class MockArenaHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Local HTTP mock server for testing real socket reuse and concurrent throughput."""
    request_count = 0
    lock = threading.Lock()

    def log_message(self, format, *args):
        # Suppress noisy HTTP server logs during test runs
        pass

    def do_GET(self):
        with self.lock:
            MockArenaHTTPHandler.request_count += 1

        if self.path == "/v1/models":
            body = json.dumps({
                "object": "list",
                "data": [
                    {"id": "claude-coding-router-preview", "object": "model"},
                    {"id": "coding-router-preview", "object": "model"},
                ]
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        with self.lock:
            MockArenaHTTPHandler.request_count += 1

        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)

        if self.path == "/v1/chat/completions":
            try:
                req_json = json.loads(post_data.decode("utf-8"))
            except Exception:
                req_json = {}

            body = json.dumps({
                "id": "chatcmpl-test-concurrency",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": req_json.get("model", "coding-router-preview"),
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Concurrent response confirmed"
                    },
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.0}
            }).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def local_mock_arena_server():
    """Spins up a local HTTP server on an ephemeral port for concurrency testing."""
    server = ThreadingMockServer(("127.0.0.1", 0), MockArenaHTTPHandler)
    port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    yield f"http://127.0.0.1:{port}/v1"
    server.shutdown()
    server.server_close()


# ==============================================================================
# SECTION 1: High-Concurrency Socket Reuse & Thread-Safety
# ==============================================================================

class TestConcurrencyAndThreadSafety:
    """Stress tests high concurrency, thread safety, and socket pooling."""

    def test_singleton_thread_safety(self):
        """50 concurrent threads requesting get_instance() must receive the exact same singleton."""
        instances = []
        threads = []

        def worker():
            inst = ArenaClusterClient.get_instance()
            instances.append(inst)

        for _ in range(50):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=3.0)

        assert len(instances) == 50
        first_inst = instances[0]
        for inst in instances:
            assert inst is first_inst, "Race condition: get_instance() returned distinct instances"

    def test_high_concurrency_socket_reuse_no_deadlock(self, local_mock_arena_server, monkeypatch):
        """
        Executes 100 concurrent requests across 25 parallel threads against a local HTTP server.
        Verifies:
          - No deadlocks.
          - 100% success rate.
          - Sockets are pooled and reused via requests.Session HTTPAdapter.
        """
        monkeypatch.setenv("ARENA_API_KEY", "test-adversarial-concurrency-key")

        client = ArenaClusterClient(
            base_url=local_mock_arena_server,
            pool_connections=10,
            pool_maxsize=25,
            timeout=10,
        )

        results = []
        errors = []
        threads = []
        total_requests = 100
        num_threads = 25

        def worker(requests_per_thread):
            for _ in range(requests_per_thread):
                try:
                    resp = client.send_request(
                        model="coding-router-preview",
                        messages=[{"role": "user", "content": "Concurrency stress probe"}],
                    )
                    assert resp.get("choices")[0]["message"]["content"] == "Concurrent response confirmed"
                    results.append(resp)
                except Exception as ex:
                    errors.append(ex)

        reqs_per_thread = total_requests // num_threads
        t_start = time.time()

        for _ in range(num_threads):
            t = threading.Thread(target=worker, args=(reqs_per_thread,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10.0)
            assert not t.is_alive(), "Deadlock detected: thread did not finish within timeout"

        t_elapsed = time.time() - t_start

        # Assert zero exceptions and full success
        assert len(errors) == 0, f"Encountered {len(errors)} errors during concurrency: {errors[:3]}"
        assert len(results) == total_requests, f"Expected {total_requests} completions, got {len(results)}"

        # Verify session adapter connection pooling exists and pool is healthy
        session = client._get_session()
        adapter = session.adapters["http://"]
        assert adapter._pool_connections == 10
        assert adapter._pool_maxsize == 25

        # Cleanup
        client.close()
        assert client._session is None

    def test_concurrent_stream_generators_isolation(self, monkeypatch):
        """
        Ensures multiple concurrent streaming requests yield isolated SSE streams
        without cross-thread buffer corruption.
        """
        monkeypatch.setenv("ARENA_API_KEY", "test-stream-key")
        client = ArenaClusterClient()

        def fake_iter_lines(thread_id):
            for i in range(5):
                yield f"data: {{\"thread\": {thread_id}, \"chunk\": {i}}}"

        streams_data = {t_id: [] for t_id in range(10)}

        def stream_worker(t_id):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.iter_lines.return_value = fake_iter_lines(t_id)

            with patch.object(client._get_session(), "post", return_value=mock_resp):
                gen = client.send_request(
                    model="coding-router-preview",
                    messages=[{"role": "user", "content": f"Stream probe {t_id}"}],
                    stream=True,
                )
                for item in gen:
                    streams_data[t_id].append(item)

        threads = [threading.Thread(target=stream_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        for t_id, chunks in streams_data.items():
            assert len(chunks) == 5
            for i, chunk in enumerate(chunks):
                data = json.loads(chunk.replace("data: ", ""))
                assert data["thread"] == t_id
                assert data["chunk"] == i

        client.close()


# ==============================================================================
# SECTION 2: Network Disruption & Fallback Resilience
# ==============================================================================

class TestNetworkDisruptionAndFallback:
    """Tests system resilience against 429, 500, 402, timeouts, and multi-router cascades."""

    def test_arena_http_429_rate_limit_exception(self, monkeypatch):
        """ArenaClusterClient must raise ArenaRateLimitError on HTTP 429."""
        monkeypatch.setenv("ARENA_API_KEY", "test-key")
        client = ArenaClusterClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "Rate limit exceeded. Tier quota reached."

        with patch.object(client._get_session(), "post", return_value=mock_resp):
            with pytest.raises(ArenaRateLimitError) as exc_info:
                client.send_request("claude-3-5-sonnet", [{"role": "user", "content": "hi"}])
            assert "HTTP 429" in str(exc_info.value)
        client.close()

    def test_arena_http_500_server_error_exception(self, monkeypatch):
        """ArenaClusterClient must raise ArenaError on HTTP 500."""
        monkeypatch.setenv("ARENA_API_KEY", "test-key")
        client = ArenaClusterClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error in H100 cluster."

        with patch.object(client._get_session(), "post", return_value=mock_resp):
            with pytest.raises(ArenaError) as exc_info:
                client.send_request("claude-3-5-sonnet", [{"role": "user", "content": "hi"}])
            assert "HTTP 500" in str(exc_info.value)
        client.close()

    def test_same_model_cascade_on_arena_429_transitions_to_openrouter(self, monkeypatch):
        """
        When Arena returns HTTP 429 on claude-3-5-sonnet:
        Same-Model Multi-Router Cascade must instantly failover to OpenRouter
        with the exact same model, recording the failure in attempt_history.
        """
        monkeypatch.setenv("ARENA_API_KEY", "arena-key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CONSOLE_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)

        router = EmbeddedOmniRoute()

        # Mock Arena failing with 429
        def mock_arena_429(*args, **kwargs):
            raise ArenaRateLimitError("Arena HTTP 429: Rate limit exhausted")

        # Mock OpenRouter succeeding with the exact same model
        def mock_openrouter_success(prompt, system, model, timeout):
            return {
                "provider": "openrouter",
                "model": model,
                "text": "OpenRouter successfully took over Claude 3.5 Sonnet request",
                "cost": 0.003,
                "status": "success"
            }

        with patch.object(router, "_call_arena_text", side_effect=mock_arena_429):
            with patch.object(router, "_call_openrouter_text", side_effect=mock_openrouter_success):
                res = router.complete_text("Solve quantum mechanics", model="claude-3-5-sonnet")

        assert res["status"] == "success"
        assert res["provider"] == "openrouter"
        assert res["model"] == "anthropic/claude-3.5-sonnet"
        assert "OpenRouter successfully took over" in res["text"]

        # Verify attempt_history recorded the Arena 429 failure
        assert len(res["attempt_history"]) == 1
        assert res["attempt_history"][0]["provider"] == "arena"
        assert "429" in res["attempt_history"][0]["error"]

    def test_same_model_cascade_on_http_500_transitions_to_next_router(self, monkeypatch):
        """
        When primary router throws HTTP 500:
        Cascade moves to next router without unhandled crash.
        """
        monkeypatch.setenv("ARENA_API_KEY", "arena-key")
        monkeypatch.setenv("GROQ_API_KEY", "groq-key")

        router = EmbeddedOmniRoute()

        def mock_arena_500(*args, **kwargs):
            raise ArenaError("Arena API Error (HTTP 500): Internal H100 GPU cluster fault")

        def mock_groq_success(prompt, system, model, timeout):
            return {
                "provider": "groq",
                "model": model,
                "text": "Groq LPU successfully took over",
                "cost": 0.0,
                "status": "success"
            }

        with patch.object(router, "_call_arena_text", side_effect=mock_arena_500):
            with patch.object(router, "_call_groq_text", side_effect=mock_groq_success):
                res = router.complete_text("Quick coding task", model="coding-router-preview")

        assert res["status"] == "success"
        assert res["provider"] == "groq"
        assert len(res["attempt_history"]) >= 1
        assert res["attempt_history"][0]["provider"] == "arena"
        assert "500" in res["attempt_history"][0]["error"]

    def test_http_402_transparent_retry_claude_family(self, monkeypatch):
        """
        ArenaClusterClient transparently retries claude-fable-5-1 with claude-coding-router-preview
        when HTTP 402 paid_credits_required is returned.
        """
        monkeypatch.setenv("ARENA_API_KEY", "arena-key")
        client = ArenaClusterClient()

        resp_402 = MagicMock()
        resp_402.status_code = 402
        resp_402.json.return_value = {
            "error": {"message": "Credit balance insufficient for dedicated H100", "code": "paid_credits_required"}
        }
        resp_402.text = json.dumps(resp_402.json.return_value)

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {
            "id": "chatcmpl-fallback-success",
            "model": "claude-coding-router-preview",
            "choices": [{"message": {"role": "assistant", "content": "Fallback Claude router response"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "cost": 0.0}
        }

        with patch.object(client._get_session(), "post", side_effect=[resp_402, resp_200]) as mock_post:
            res = client.complete("Write code", model="claude-fable-5-1")

        assert res["status"] == "success"
        assert res["fallback_used"] is True
        assert res["requested_model"] == "claude-fable-5-1"
        assert res["model"] == "claude-coding-router-preview"
        assert res["text"] == "Fallback Claude router response"
        assert mock_post.call_count == 2
        client.close()

    def test_http_402_transparent_retry_general_coding_family(self, monkeypatch):
        """
        ArenaClusterClient retries non-Claude models (e.g. gpt-6-astra) with coding-router-preview on 402.
        """
        monkeypatch.setenv("ARENA_API_KEY", "arena-key")
        client = ArenaClusterClient()

        resp_402 = MagicMock()
        resp_402.status_code = 402
        resp_402.json.return_value = {"error": {"code": "paid_credits_required"}}
        resp_402.text = json.dumps(resp_402.json.return_value)

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {
            "id": "chatcmpl-fallback-coding",
            "model": "coding-router-preview",
            "choices": [{"message": {"role": "assistant", "content": "Fallback general coding response"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "cost": 0.0}
        }

        with patch.object(client._get_session(), "post", side_effect=[resp_402, resp_200]) as mock_post:
            res = client.complete("Architect system", model="gpt-6")

        assert res["status"] == "success"
        assert res["fallback_used"] is True
        assert res["requested_model"] == "gpt-6"
        assert res["model"] == "coding-router-preview"
        assert mock_post.call_count == 2
        client.close()

    def test_connection_timeout_resilience_and_cascade(self, monkeypatch):
        """
        When primary provider times out (ConnectTimeout / ReadTimeout),
        EmbeddedOmniRoute catches timeout and cascades smoothly.
        """
        monkeypatch.setenv("ARENA_API_KEY", "arena-key")
        monkeypatch.setenv("GROQ_API_KEY", "groq-key")

        router = EmbeddedOmniRoute()

        def mock_arena_timeout(*args, **kwargs):
            raise requests.exceptions.ConnectTimeout("Arena connection timed out after 30s")

        def mock_groq_success(prompt, system, model, timeout):
            return {
                "provider": "groq",
                "model": model,
                "text": "Groq responded quickly after Arena timeout",
                "status": "success"
            }

        with patch.object(router, "_call_arena_text", side_effect=mock_arena_timeout):
            with patch.object(router, "_call_groq_text", side_effect=mock_groq_success):
                res = router.complete_text("Fast query", model="coding-router-preview")

        assert res["status"] == "success"
        assert res["provider"] == "groq"
        assert "Arena connection timed out" in res["attempt_history"][0]["error"]

    def test_all_providers_failure_raises_informative_runtime_error(self, monkeypatch):
        """
        When all providers in chain fail, complete_text must raise RuntimeError
        containing full attempt history.
        """
        monkeypatch.setenv("ARENA_API_KEY", "arena-key")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)

        router = EmbeddedOmniRoute()

        def mock_arena_fail(*args, **kwargs):
            raise ArenaError("Arena cluster down")

        def mock_pollinations_fail(*args, **kwargs):
            raise urllib.error.URLError("Pollinations network unreachable")

        with patch.object(router, "_call_arena_text", side_effect=mock_arena_fail):
            with patch.object(router, "_call_pollinations_text", side_effect=mock_pollinations_fail):
                with pytest.raises(RuntimeError) as exc_info:
                    router.complete_text("Hello world", model="coding-router-preview")

                err_str = str(exc_info.value)
                assert "All embedded gateway routes failed" in err_str
                assert "arena" in err_str
                assert "pollinations" in err_str


# ==============================================================================
# SECTION 3: Edge Case Inputs & Data Boundary Handling
# ==============================================================================

class TestEdgeCaseInputs:
    """Stress tests edge case inputs: empty strings, giant prompts, unsupported models, malformed JSON."""

    def test_empty_prompt_and_empty_messages(self, monkeypatch):
        """Empty prompt string or empty messages should produce valid payload structure without crashing."""
        monkeypatch.setenv("ARENA_API_KEY", "arena-key")
        client = ArenaClusterClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Handled empty prompt"}}]
        }

        with patch.object(client._get_session(), "post", return_value=mock_resp) as mock_post:
            res = client.complete(prompt="", model="coding-router-preview")
            assert res["status"] == "success"
            assert res["text"] == "Handled empty prompt"

            # Verify messages payload contains user message with empty content
            sent_payload = mock_post.call_args[1]["json"]
            assert sent_payload["messages"] == [{"role": "user", "content": ""}]

        client.close()

    def test_giant_prompt_stress(self, monkeypatch):
        """
        Passes a 2MB giant prompt (approx 500k words) through ArenaClusterClient and EmbeddedOmniRoute.
        Verifies JSON serialization and headers handle large payloads without memory faults.
        """
        monkeypatch.setenv("ARENA_API_KEY", "arena-key")
        client = ArenaClusterClient()

        giant_prompt = "Alpha Beta Gamma " * 150000  # ~2.5 MB string

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Processed giant prompt"}}]
        }

        with patch.object(client._get_session(), "post", return_value=mock_resp) as mock_post:
            res = client.complete(prompt=giant_prompt, model="coding-router-preview")
            assert res["status"] == "success"
            sent_payload = mock_post.call_args[1]["json"]
            assert len(sent_payload["messages"][0]["content"]) == len(giant_prompt)

        client.close()

    def test_unsupported_model_graceful_handling(self):
        """
        Passing an unsupported/fictional model name should resolve safely without throwing KeyError.
        """
        unknown_model = "non-existent-quantum-gpt-omega-9000"

        # UniversalCapabilityRegistry.resolve_canonical_id should return None safely
        resolved = UniversalCapabilityRegistry.resolve_canonical_id(unknown_model)
        assert resolved is None

        # resolve_target_model in ArenaCluster should preserve or return default
        target = resolve_target_model(unknown_model)
        assert target == unknown_model

        # None or empty model should resolve to coding-router-preview
        assert resolve_target_model("") == "coding-router-preview"
        assert resolve_target_model(None) == "coding-router-preview"

    def test_malformed_json_response_resilience(self, monkeypatch):
        """
        When provider endpoint returns 200 but body is malformed JSON or HTML,
        ArenaClusterClient must raise an exception caught by router cascade.
        """
        monkeypatch.setenv("ARENA_API_KEY", "arena-key")
        client = ArenaClusterClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = json.JSONDecodeError("Expecting value", "<html>502 Bad Gateway</html>", 0)

        with patch.object(client._get_session(), "post", return_value=mock_resp):
            with pytest.raises(json.JSONDecodeError):
                client.send_request("coding-router-preview", [{"role": "user", "content": "hi"}])

        client.close()

    def test_missing_api_keys_failsafe(self, monkeypatch):
        """
        When all router API keys are stripped from environment:
        ArenaClusterClient raises ArenaAuthenticationError.
        EmbeddedOmniRoute safely falls back to public_free pollinations route.
        """
        monkeypatch.delenv("ARENA_API_KEY", raising=False)
        monkeypatch.delenv("ARENA_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CONSOLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
        monkeypatch.delenv("VERTEX_API_KEY", raising=False)

        client = ArenaClusterClient()
        with pytest.raises(ArenaAuthenticationError) as exc_info:
            client.get_api_key()
        assert "ARENA_API_KEY environment variable is not configured" in str(exc_info.value)
        client.close()

        # OmniRoute should still have pollinations online as free zero-key fallback
        router = EmbeddedOmniRoute()
        routes = router.get_available_routes()
        pollinations_route = next((r for r in routes if r["provider"] == "pollinations"), None)
        assert pollinations_route is not None
        assert pollinations_route["online"] is True
        assert pollinations_route["requires_key"] is False


# ==============================================================================
# SECTION 4: Empirical Finding Probes (Adversarial Edge Cases)
# ==============================================================================

class TestAdversarialEdgeCaseFindings:
    """Probes edge failure modes identified in static code review."""

    def test_arena_402_fallback_itself_fails(self, monkeypatch):
        """
        CRITICAL ADVERSARIAL CHECK:
        If a model call returns 402, and the fallback model call returns HTTP 500 or 402:
        What does ArenaClusterClient.send_request return?
        Verify whether status code is checked on the fallback request.
        """
        monkeypatch.setenv("ARENA_API_KEY", "arena-key")
        client = ArenaClusterClient()

        resp_402_first = MagicMock()
        resp_402_first.status_code = 402
        resp_402_first.json.return_value = {"error": {"code": "paid_credits_required"}}
        resp_402_first.text = '{"error": {"code": "paid_credits_required"}}'

        # Second request (to fallback model) also fails with 500
        resp_500_second = MagicMock()
        resp_500_second.status_code = 500
        resp_500_second.json.return_value = {"error": {"message": "Cluster overload on router model"}}
        resp_500_second.text = '{"error": {"message": "Cluster overload on router model"}}'

        with patch.object(client._get_session(), "post", side_effect=[resp_402_first, resp_500_second]):
            # If send_request blindly returns resp.json() without checking status_code != 200 on retry,
            # complete() will return status="success" with empty text, masking the error!
            res = client.complete("Test code", model="claude-fable-5-1")
            
            # Record empirical behavior:
            # Does complete() return status="success" even though the server returned 500?
            print(f"\n[EMPIRICAL OBSERVATION] Result when 402 fallback returns 500: {res}")
            
        client.close()
