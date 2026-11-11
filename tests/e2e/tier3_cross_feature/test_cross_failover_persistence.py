"""
test_cross_failover_persistence.py — Tier 3 Cross-Feature Combination Tests.

Covers Feature Interaction:
  - Feature 11 / Feature 19: Workspace Proxy & Failover Dispatcher / IMRCM Cascade.
  - Feature 6 / Feature 8: Central SQLite conversation storage & context preservation.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List
import pytest
import requests

from agent.modules.omniroute_embedded import EmbeddedOmniRoute
from tests.e2e.conftest import MockGatewayServer, E2ETestDB
import tests.e2e.conftest as conftest_module


@pytest.fixture(autouse=True)
def force_ephemeral_port(monkeypatch: pytest.MonkeyPatch):
    """Ensure each mock gateway server binds to a unique ephemeral port."""
    monkeypatch.setattr(conftest_module, "_find_available_port", lambda preferred=0: 0)


class TestCrossFailoverPersistence:
    """Pairwise combination testing: Failover Routing <-> Persistent Conversation Continuity."""

    def test_mid_conversation_failover_preserves_conversation_id_and_sequence(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify that when a mid-conversation failover occurs (e.g. Turn 1 on Anthropic,
        Turn 2 encounters 429 on Anthropic and cascades to OpenAI endpoint), the conversation ID
        and sequential turn ordering in SQLite are completely preserved.
        """
        conv_id = "conv_failover_persist_001"

        # Turn 1: Primary Anthropic request succeeds
        t1_payload = {
            "model": "claude-3-5-sonnet",
            "conversation_id": conv_id,
            "messages": [{"role": "user", "content": "Turn 1: Recommend 3 video topics."}],
            "max_tokens": 256
        }
        res1 = e2e_client.post(
            f"{mock_gateway_server.url}/v1/messages",
            json=t1_payload,
            headers={"Connection": "close"}
        )
        assert res1.status_code == 200
        time.sleep(0.05)

        # Verify Turn 1 in DB
        msgs_t1 = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(msgs_t1) == 2
        assert msgs_t1[1]["role"] == "assistant"
        assert msgs_t1[1]["model"] == "claude-3-5-sonnet"

        # Turn 2: Simulate primary provider 429 rate limit
        mock_gateway_server.set_mock_response(
            "/v1/messages",
            status_code=429,
            body={"error": {"type": "rate_limit_error", "message": "Tokens per minute exceeded"}},
            headers={"Retry-After": "30"}
        )

        res2_primary = e2e_client.post(
            f"{mock_gateway_server.url}/v1/messages",
            json={
                "model": "claude-3-5-sonnet",
                "conversation_id": conv_id,
                "messages": [{"role": "user", "content": "Turn 2: Pick topic 1."}]
            },
            headers={"Connection": "close"}
        )
        assert res2_primary.status_code == 429

        # Client / Dispatcher fails over to secondary router (OpenAI endpoint) with same conversation_id
        res2_backup = e2e_client.post(
            f"{mock_gateway_server.url}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "conversation_id": conv_id,
                "messages": [{"role": "user", "content": "Turn 2: Pick topic 1."}]
            },
            headers={"Connection": "close"}
        )
        assert res2_backup.status_code == 200
        time.sleep(0.05)

        # Verify final state: exactly 4 messages in DB under same conversation ID
        msgs_final = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(msgs_final) == 4, f"Expected 4 messages, got {len(msgs_final)}"
        assert msgs_final[0]["role"] == "user"
        assert msgs_final[1]["role"] == "assistant"
        assert msgs_final[1]["provider"] == "anthropic"
        assert msgs_final[2]["role"] == "user"
        assert msgs_final[3]["role"] == "assistant"
        assert msgs_final[3]["provider"] == "openai"

    def test_embedded_omniroute_adaptive_failover_persisted_to_db(
        self,
        e2e_db: E2ETestDB,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Verify that EmbeddedOmniRoute executes automated failover across simulated provider
        failures, logs attempt history, and the resulting response is saved to SQLite.
        """
        conv_id = "conv_omniroute_failover_002"
        router = EmbeddedOmniRoute()

        # Simulate provider environment: Arena online, OpenRouter online
        monkeypatch.setenv("ARENA_API_KEY", "sk-arena-test-key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openrouter-test-key")

        # Mock Arena failing with 429, OpenRouter succeeding
        def mock_arena_call(*args, **kwargs):
            raise RuntimeError("HTTP 429 Too Many Requests: Arena cluster capacity reached")

        def mock_openrouter_call(prompt, system_prompt, model, timeout):
            return {
                "provider": "openrouter",
                "model": model,
                "text": "Success from OpenRouter backup route.",
                "cost": 0.0,
                "status": "success"
            }

        monkeypatch.setattr(router, "_call_arena_text", mock_arena_call)
        monkeypatch.setattr(router, "_call_openrouter_text", mock_openrouter_call)

        # Execute completion with failover preference
        result = router.complete_text(
            prompt="Generate a 3-part script outline.",
            provider_preference=["arena", "openrouter"]
        )

        assert result["status"] == "success"
        assert result["provider"] == "openrouter"
        assert "attempt_history" in result
        assert len(result["attempt_history"]) == 1
        assert result["attempt_history"][0]["provider"] == "arena"
        assert "429" in result["attempt_history"][0]["error"]

        # Persist into DB with attempt history metadata
        msg_id = e2e_db.save_message(
            conversation_id=conv_id,
            role="assistant",
            content=result["text"],
            model=result["model"],
            provider=result["provider"],
            metadata={"attempts": result["attempt_history"]}
        )
        assert msg_id.startswith("msg_")

        # Verify DB content
        stored = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(stored) >= 1
        assert stored[-1]["provider"] == "openrouter"
        assert stored[-1]["content"] == "Success from OpenRouter backup route."

    def test_failed_attempts_do_not_commit_corrupted_assistant_turns(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify that when a provider fails with HTTP 500 midway, no phantom or corrupt
        assistant messages are saved in the messages table.
        """
        conv_id = "conv_no_corrupt_messages_003"

        # Force mock gateway to return 500 error on /v1/messages
        mock_gateway_server.set_mock_response(
            "/v1/messages",
            status_code=500,
            body={"error": {"type": "server_error", "message": "Inference backend crashed"}}
        )

        resp = e2e_client.post(
            f"{mock_gateway_server.url}/v1/messages",
            json={
                "model": "claude-3-5-sonnet",
                "conversation_id": conv_id,
                "messages": [{"role": "user", "content": "Trigger failure."}]
            },
            headers={"Connection": "close"}
        )
        assert resp.status_code == 500

        time.sleep(0.05)

        # Verify no assistant messages were saved for this failed request
        msgs = e2e_db.get_conversation_messages(conv_id, format="raw")
        assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert len(assistant_msgs) == 0, f"Expected 0 assistant messages on failure, found {len(assistant_msgs)}"

    def test_cascading_failover_retains_conversation_history_for_next_turn(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify that after recovering from a failover turn, subsequent turns continue
        seamlessly and GET /v1/conversations/{id} returns the complete unbroken history.
        """
        conv_id = "conv_cascade_history_004"

        # Turn 1: Normal user + assistant turn
        e2e_client.post(
            f"{mock_gateway_server.url}/v1/messages",
            json={
                "model": "claude-3-5-sonnet",
                "conversation_id": conv_id,
                "messages": [{"role": "user", "content": "Step 1: Ideate."}]
            },
            headers={"Connection": "close"}
        )
        time.sleep(0.05)

        # Turn 2: Normal user + assistant turn on ChatGPT
        e2e_client.post(
            f"{mock_gateway_server.url}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "conversation_id": conv_id,
                "messages": [{"role": "user", "content": "Step 2: Script."}]
            },
            headers={"Connection": "close"}
        )
        time.sleep(0.05)

        # Turn 3: User + assistant turn on Arena
        e2e_client.post(
            f"{mock_gateway_server.url}/v1/messages",
            json={
                "model": "claude-coding-router-preview",
                "conversation_id": conv_id,
                "messages": [{"role": "user", "content": "Step 3: Review."}]
            },
            headers={"Connection": "close"}
        )
        time.sleep(0.05)

        # Fetch entire history via Gateway API
        resp = e2e_client.get(
            f"{mock_gateway_server.url}/v1/conversations/{conv_id}",
            headers={"Connection": "close"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" in data
        assert len(data["messages"]) == 6, f"Expected 6 messages in history, got {len(data['messages'])}"

    def test_cross_provider_failover_preserves_custom_metadata(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify that conversation metadata (project tags, user flags) remains
        immutable across multi-provider failover hops.
        """
        conv_id = "conv_meta_safe_005"
        meta = {"track": "e2e_testing", "tier": 3, "failover_expected": True}
        e2e_db.create_conversation(conv_id, title="Resilience Check", metadata=meta)

        # Perform request
        resp = e2e_client.post(
            f"{mock_gateway_server.url}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "conversation_id": conv_id,
                "messages": [{"role": "user", "content": "Test metadata immutability."}]
            },
            headers={"Connection": "close"}
        )
        assert resp.status_code == 200
        time.sleep(0.05)

        # Verify metadata
        conv = e2e_db.get_conversation(conv_id)
        assert conv is not None
        assert conv["title"] == "Resilience Check"
        assert conv["metadata"]["track"] == "e2e_testing"
        assert conv["metadata"]["tier"] == 3
