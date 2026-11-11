"""
test_scenario_mobile_client.py — Tier 4 Real-World Application Scenarios.

Scenario 2: Realistic Android mobile client workload simulating app launch,
model catalog fetch, streaming dialogue, app backgrounding, and reconnection.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List
import pytest
import requests

from tests.e2e.conftest import (
    MockGatewayServer,
    E2ETestDB,
    parse_anthropic_sse,
    extract_sse_text,
)
import tests.e2e.conftest as conftest_module


@pytest.fixture(autouse=True)
def force_ephemeral_port(monkeypatch: pytest.MonkeyPatch):
    """Ensure each mock gateway server binds to a unique ephemeral port."""
    monkeypatch.setattr(conftest_module, "_find_available_port", lambda preferred=0: 0)


class TestScenarioMobileClient:
    """Real-World Production Workflow: Mobile Android Client Lifecycle & UX Flow."""

    def test_android_app_complete_lifecycle_session(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Simulate the complete Android app lifecycle:
          1. App Launch: Ping health endpoint with CORS preflight.
          2. Catalog Discovery: Fetch available models for dropdown picker.
          3. Active Streaming Session: Stream response on LAN IP.
          4. Background & Wakeup: App backgrounded, then wakes up and recovers transcript.
          5. Multi-turn continuation: Send follow-up turn.
        """
        lan_origin = "http://10.92.57.208:8080"
        conv_id = "scenario_android_lifecycle_001"

        # ── Step 1: App Launch & Health Check ──
        preflight_health = e2e_client.options(
            f"{mock_gateway_server.url}/health",
            headers={"Origin": lan_origin, "Connection": "close"}
        )
        assert preflight_health.status_code == 200
        assert preflight_health.headers.get("Access-Control-Allow-Origin") == "*"

        health_resp = e2e_client.get(
            f"{mock_gateway_server.url}/health",
            headers={"Origin": lan_origin, "Connection": "close"}
        )
        assert health_resp.status_code == 200
        assert health_resp.json()["status"] == "ok"

        # ── Step 2: Model Catalog Discovery ──
        models_resp = e2e_client.get(
            f"{mock_gateway_server.url}/v1/models",
            headers={"Origin": lan_origin, "Connection": "close"}
        )
        assert models_resp.status_code == 200
        models_list = models_resp.json()["data"]
        model_ids = [m["id"] for m in models_list]
        assert "claude-3-5-sonnet" in model_ids
        assert "claude-coding-router-preview" in model_ids
        assert "deepseek-v3" in model_ids

        # ── Step 3: Active Streaming Chat ──
        stream_payload = {
            "model": "claude-3-5-sonnet",
            "conversation_id": conv_id,
            "stream": True,
            "messages": [{"role": "user", "content": "Mobile Turn 1: Brainstorm 3 script hooks."}],
            "max_tokens": 512
        }
        stream_resp = e2e_client.post(
            f"{mock_gateway_server.url}/v1/messages",
            json=stream_payload,
            headers={
                "Origin": lan_origin,
                "x-api-key": "sk-ant-android-key",
                "anthropic-version": "2023-06-01",
                "Connection": "close"
            },
            stream=True
        )
        assert stream_resp.status_code == 200
        text1 = extract_sse_text(stream_resp)
        assert len(text1) > 0

        time.sleep(0.05)

        # ── Step 4: Backgrounding & Reconnection ──
        # App is placed in background, connection closes, app reopens after 5 minutes
        reconnect_session = requests.Session()
        transcript_resp = reconnect_session.get(
            f"{mock_gateway_server.url}/v1/conversations/{conv_id}",
            headers={"Origin": lan_origin, "Connection": "close"}
        )
        assert transcript_resp.status_code == 200
        saved_messages = transcript_resp.json()["messages"]
        assert len(saved_messages) == 2, f"Expected 2 messages rehydrated, got {len(saved_messages)}"

        # ── Step 5: Multi-turn Continuation ──
        cont_payload = {
            "model": "claude-3-5-sonnet",
            "conversation_id": conv_id,
            "messages": [{"role": "user", "content": "Mobile Turn 2: Pick the best hook."}],
            "max_tokens": 256
        }
        cont_resp = reconnect_session.post(
            f"{mock_gateway_server.url}/v1/messages",
            json=cont_payload,
            headers={"Origin": lan_origin, "Connection": "close"}
        )
        assert cont_resp.status_code == 200
        reconnect_session.close()

        time.sleep(0.05)

        # Verify final sequence in DB
        db_msgs = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(db_msgs) == 4
        assert db_msgs[0]["content"] == "Mobile Turn 1: Brainstorm 3 script hooks."
        assert db_msgs[2]["content"] == "Mobile Turn 2: Pick the best hook."

    def test_android_model_picker_dynamic_switch_in_same_chat(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify that an Android user can dynamically switch models in the dropdown
        selector midway through a conversation thread:
          Turn 1: claude-coding-router-preview (Arena)
          Turn 2: mistral-large-2411 (Mistral via OpenAI format)
          Turn 3: deepseek-v3 (DeepSeek via OpenAI format)
        All models co-exist within the same conversation thread without context loss.
        """
        conv_id = "scenario_model_picker_switch_002"

        steps = [
            ("/v1/messages", "claude-coding-router-preview", "Turn 1: Ideate concept."),
            ("/v1/chat/completions", "mistral-large-2411", "Turn 2: Deep reasoning on outline."),
            ("/v1/chat/completions", "deepseek-v3", "Turn 3: Anti-slop and final polish."),
        ]

        for endpoint, model, prompt in steps:
            if "chat/completions" in endpoint:
                payload = {
                    "model": model,
                    "conversation_id": conv_id,
                    "messages": [{"role": "user", "content": prompt}]
                }
            else:
                payload = {
                    "model": model,
                    "conversation_id": conv_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 512
                }

            resp = e2e_client.post(
                f"{mock_gateway_server.url}{endpoint}",
                json=payload,
                headers={"Origin": "http://localhost:8080", "Connection": "close"}
            )
            assert resp.status_code == 200, f"Failed for model {model}: {resp.text}"

        time.sleep(0.05)

        # Inspect SQLite rows
        msgs = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(msgs) == 6
        assistant_models = [m["model"] for m in msgs if m["role"] == "assistant"]
        assert assistant_models == [
            "claude-coding-router-preview",
            "mistral-large-2411",
            "deepseek-v3"
        ]

    def test_android_offline_retry_and_sync_after_dropout(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify that if a mobile client experiences network failure, it can re-poll
        the central database via GET /v1/conversations/{id} to recover the exact last state
        and cleanly resend without creating duplicate assistant turns.
        """
        conv_id = "scenario_dropout_recovery_003"

        # Turn 1: Successfully committed
        e2e_client.post(
            f"{mock_gateway_server.url}/v1/messages",
            json={
                "model": "claude-3-5-sonnet",
                "conversation_id": conv_id,
                "messages": [{"role": "user", "content": "Stable message 1."}],
                "max_tokens": 128
            },
            headers={"Connection": "close"}
        )
        time.sleep(0.05)

        # Simulate mobile client polling state after brief network drop
        resp = e2e_client.get(
            f"{mock_gateway_server.url}/v1/conversations/{conv_id}",
            headers={"Connection": "close"}
        )
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        assert len(msgs) == 2

        # Client safely appends Turn 2
        resp2 = e2e_client.post(
            f"{mock_gateway_server.url}/v1/messages",
            json={
                "model": "claude-3-5-sonnet",
                "conversation_id": conv_id,
                "messages": [{"role": "user", "content": "Stable message 2."}],
                "max_tokens": 128
            },
            headers={"Connection": "close"}
        )
        assert resp2.status_code == 200
        time.sleep(0.05)

        final_msgs = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(final_msgs) == 4

    def test_android_synthetic_rate_limits_unlock_client_ui(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session
    ) -> None:
        """
        Verify that synthetic unbottlenecked rate-limit headers are returned
        with high values (10,000 requests, 2,000,000 tokens) so mobile UI apps
        never display 'Rate limit reached' or disable the input textarea.
        """
        resp = e2e_client.post(
            f"{mock_gateway_server.url}/v1/messages",
            json={
                "model": "claude-3-5-sonnet",
                "messages": [{"role": "user", "content": "Check unbottlenecked limits."}],
                "max_tokens": 64
            },
            headers={"Origin": "http://10.92.57.208:8080", "Connection": "close"}
        )
        assert resp.status_code == 200

        # Inspect headers
        req_limit = int(resp.headers.get("anthropic-ratelimit-requests-limit", 0))
        tok_limit = int(resp.headers.get("anthropic-ratelimit-tokens-limit", 0))
        assert req_limit >= 10000, f"Expected >=10000 requests limit, got {req_limit}"
        assert tok_limit >= 2000000, f"Expected >=2000000 tokens limit, got {tok_limit}"
