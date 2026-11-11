"""
test_cross_arena_to_chatgpt.py — Tier 3 Cross-Feature Combination Tests.

Covers Feature Interaction:
  - Feature 1 / Feature 8: Conversation initiated on Arena.AI, persisted in SQLite,
    and resumed/continued via ChatGPT `/v1/chat/completions`.
  - Feature 6 / Feature 7: Unified SQLite schema & format translation across models.
"""

from __future__ import annotations

import json
import pytest
import requests
from typing import Any, Dict, List

from tests.e2e.conftest import MockGatewayServer, E2ETestDB
import tests.e2e.conftest as conftest_module


@pytest.fixture(autouse=True)
def force_ephemeral_port(monkeypatch: pytest.MonkeyPatch):
    """Ensure each mock gateway server binds to a unique ephemeral port to avoid Windows SO_REUSEADDR port collision."""
    monkeypatch.setattr(conftest_module, "_find_available_port", lambda preferred=0: 0)


class TestCrossArenaToChatGPT:
    """Pairwise combination testing: Arena.AI <-> SQLite Store <-> ChatGPT Endpoint."""

    def test_arena_session_created_and_resumed_in_chatgpt(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify that a conversation thread initiated via Arena model (/v1/messages)
        is persisted in SQLite and can be seamlessly resumed via ChatGPT (/v1/chat/completions).
        """
        conv_id = "conv_arena_chatgpt_001"

        # 1. Initiate conversation via Arena model on /v1/messages
        arena_payload = {
            "model": "claude-coding-router-preview",
            "conversation_id": conv_id,
            "messages": [
                {"role": "user", "content": "Brainstorm 3 viral YouTube Shorts hooks for quantum computing."}
            ],
            "max_tokens": 1024,
        }
        res1 = e2e_client.post(
            f"{mock_gateway_server.url}/v1/messages",
            json=arena_payload,
            headers={"Content-Type": "application/json", "Connection": "close"}
        )
        assert res1.status_code == 200, f"Arena request failed: {res1.text}"
        res1_data = res1.json()
        assert res1_data["role"] == "assistant"
        assert res1_data["model"] == "claude-coding-router-preview"

        import time
        time.sleep(0.05)

        # Verify DB has stored the initial exchange
        messages_turn1 = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(messages_turn1) == 2, f"Expected 2 messages in DB, got {len(messages_turn1)}"
        assert messages_turn1[0]["role"] == "user"
        assert "quantum computing" in messages_turn1[0]["content"]
        assert messages_turn1[1]["role"] == "assistant"
        assert messages_turn1[1]["model"] == "claude-coding-router-preview"

        # 2. Resume and continue conversation via ChatGPT on /v1/chat/completions
        chatgpt_payload = {
            "model": "gpt-4o",
            "conversation_id": conv_id,
            "messages": [
                {"role": "user", "content": "Now expand Hook #1 into a full 30-second script."}
            ],
            "max_tokens": 1024,
        }
        res2 = e2e_client.post(
            f"{mock_gateway_server.url}/v1/chat/completions",
            json=chatgpt_payload,
            headers={"Content-Type": "application/json", "Connection": "close"}
        )
        assert res2.status_code == 200, f"ChatGPT continuation failed: {res2.text}"
        res2_data = res2.json()
        assert res2_data["choices"][0]["message"]["role"] == "assistant"
        assert res2_data["model"] == "gpt-4o"

        time.sleep(0.05)

        # 3. Verify total conversation state in DB: 4 messages across 2 models
        messages_all = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(messages_all) == 4, f"Expected 4 messages, got {len(messages_all)}"
        models_used = [m["model"] for m in messages_all if m["role"] == "assistant"]
        assert models_used == ["claude-coding-router-preview", "gpt-4o"], (
            f"Expected models ['claude-coding-router-preview', 'gpt-4o'], got {models_used}"
        )

    def test_multi_turn_alternation_arena_chatgpt_claude(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify multi-turn alternation cycling across three models:
        Arena -> ChatGPT -> Claude 3.5 Sonnet, confirming sequence ordering.
        """
        conv_id = "conv_three_way_alt_002"

        turns = [
            ("/v1/messages", "coding-router-preview", "Turn 1: Generate title ideas."),
            ("/v1/chat/completions", "gpt-4o", "Turn 2: Select the best title and give 3 reasons."),
            ("/v1/messages", "claude-3-5-sonnet", "Turn 3: Write script based on selected title."),
        ]

        for endpoint, model, prompt in turns:
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
                headers={"Connection": "close"}
            )
            assert resp.status_code == 200, f"Turn with {model} failed: {resp.text}"

        import time
        time.sleep(0.1)

        # Verify DB records all 6 turns in sequence
        stored = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(stored) == 6, f"Expected 6 messages, got {len(stored)}"

        assistant_turns = [m for m in stored if m["role"] == "assistant"]
        assert len(assistant_turns) == 3
        assert assistant_turns[0]["model"] == "coding-router-preview"
        assert assistant_turns[1]["model"] == "gpt-4o"
        assert assistant_turns[2]["model"] == "claude-3-5-sonnet"

    def test_format_translation_openai_view_of_arena_messages(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify bidirectional format translation: An Arena dialogue with thinking
        can be read in standard OpenAI format via GET /v1/conversations/{id}?format=openai.
        """
        conv_id = "conv_translation_test_003"
        e2e_db.create_conversation(conv_id, title="Quantum Computing Translation Test")

        # Save an Arena exchange with thinking scratchpad
        e2e_db.save_message(
            conversation_id=conv_id,
            role="user",
            content="Explain Shor's algorithm simply.",
            model="claude-coding-router-preview",
            provider="arena"
        )
        e2e_db.save_message(
            conversation_id=conv_id,
            role="assistant",
            content="Shor's algorithm efficiently factors large integers on a quantum computer.",
            model="claude-coding-router-preview",
            provider="arena",
            thinking="Step 1: Quantum Fourier transform. Step 2: Period finding."
        )

        # 1. Fetch via Gateway API in OpenAI format
        resp_openai = e2e_client.get(f"{mock_gateway_server.url}/v1/conversations/{conv_id}?format=openai")
        assert resp_openai.status_code == 200
        data_openai = resp_openai.json()
        assert "messages" in data_openai
        msgs_openai = data_openai["messages"]
        assert len(msgs_openai) == 2

        # Verify OpenAI structure
        assert msgs_openai[0]["role"] == "user"
        assert msgs_openai[0]["content"] == "Explain Shor's algorithm simply."
        assert msgs_openai[1]["role"] == "assistant"
        assert "Shor's algorithm" in msgs_openai[1]["content"]

        # 2. Fetch via Gateway API in Anthropic format
        resp_anthropic = e2e_client.get(f"{mock_gateway_server.url}/v1/conversations/{conv_id}?format=anthropic")
        assert resp_anthropic.status_code == 200
        data_anthropic = resp_anthropic.json()
        msgs_anthropic = data_anthropic["messages"]
        assert len(msgs_anthropic) == 2

        # Verify Anthropic content blocks structure (with thinking block)
        assistant_blocks = msgs_anthropic[1]["content"]
        assert isinstance(assistant_blocks, list)
        block_types = [b.get("type") for b in assistant_blocks]
        assert "thinking" in block_types
        assert "text" in block_types

    def test_conversation_metadata_and_continuation_persistence(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify conversation metadata (title, custom tags) is preserved
        during continuation calls across endpoints.
        """
        conv_id = "conv_meta_persistence_004"
        custom_metadata = {"client": "android_shorts", "target_fps": 60, "priority": "high"}
        e2e_db.create_conversation(
            conversation_id=conv_id,
            title="AI Video Strategy",
            metadata=custom_metadata
        )

        # Query GET /v1/conversations
        list_resp = e2e_client.get(
            f"{mock_gateway_server.url}/v1/conversations",
            headers={"Connection": "close"}
        )
        assert list_resp.status_code == 200
        threads = list_resp.json()["data"]
        found = next((t for t in threads if t["id"] == conv_id), None)
        assert found is not None
        assert found["title"] == "AI Video Strategy"

        # Continue conversation on OpenAI endpoint
        post_resp = e2e_client.post(
            f"{mock_gateway_server.url}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "conversation_id": conv_id,
                "messages": [{"role": "user", "content": "What is the optimal video length?"}]
            },
            headers={"Connection": "close"}
        )
        assert post_resp.status_code == 200

        import time
        time.sleep(0.05)

        # Verify metadata remains unchanged after update
        conv_record = e2e_db.get_conversation(conv_id)
        assert conv_record is not None
        assert conv_record["title"] == "AI Video Strategy"
        assert conv_record["metadata"].get("target_fps") == 60

    def test_conversation_resume_with_nonexistent_id_auto_initializes(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify that attempting to resume a previously unseen conversation ID
        auto-initializes gracefully without returning an HTTP 500 error.
        """
        fresh_cid = "conv_fresh_unseen_005"

        resp = e2e_client.post(
            f"{mock_gateway_server.url}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "conversation_id": fresh_cid,
                "messages": [{"role": "user", "content": "Initial ping for unseen conversation."}]
            },
            headers={"Connection": "close"}
        )
        assert resp.status_code == 200, f"Expected 200 on auto-init, got: {resp.status_code} - {resp.text}"

        import time
        time.sleep(0.05)

        # Verify thread was created in DB
        conv = e2e_db.get_conversation(fresh_cid)
        assert conv is not None
        msgs = e2e_db.get_conversation_messages(fresh_cid, format="raw")
        assert len(msgs) == 2
        assert msgs[0]["content"] == "Initial ping for unseen conversation."
