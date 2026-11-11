"""
test_cross_sse_to_db.py — Tier 3 Cross-Feature Combination Tests.

Covers Feature Interaction:
  - Feature 13 / Feature 14: Streaming SSE endpoints (/v1/messages, /v1/chat/completions).
  - Feature 6 / Feature 15: Central SQLite conversation storage & synthetic rate limit headers.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, List
import pytest
import requests

from tests.e2e.conftest import (
    MockGatewayServer,
    E2ETestDB,
    parse_anthropic_sse,
    parse_openai_sse,
    extract_sse_text,
)
import tests.e2e.conftest as conftest_module


@pytest.fixture(autouse=True)
def force_ephemeral_port(monkeypatch: pytest.MonkeyPatch):
    """Ensure each mock gateway server binds to a unique ephemeral port to avoid Windows SO_REUSEADDR port collision."""
    monkeypatch.setattr(conftest_module, "_find_available_port", lambda preferred=0: 0)


class TestCrossSseToDb:
    """Pairwise combination testing: SSE Streaming Engine <-> SQLite Persistence."""

    def test_anthropic_sse_stream_persisted_with_token_attribution(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify that an Anthropic SSE stream is consumed cleanly by the client
        while the gateway simultaneously writes the complete assistant message
        to SQLite with input and output token counts.
        """
        conv_id = "conv_sse_anthropic_001"
        payload = {
            "model": "claude-3-5-sonnet",
            "conversation_id": conv_id,
            "stream": True,
            "messages": [
                {"role": "user", "content": "Generate a 15-second opening hook."}
            ],
            "max_tokens": 512
        }

        resp = e2e_client.post(
            f"{mock_gateway_server.url}/v1/messages",
            json=payload,
            stream=True
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("Content-Type", "")

        # Consume SSE stream events and accumulate text
        events: List[Dict[str, Any]] = list(parse_anthropic_sse(resp))
        assert len(events) >= 3, "Expected at least message_start, delta, and message_stop events"
        event_types = [e.get("type") for e in events]
        assert "message_start" in event_types
        assert "content_block_delta" in event_types

        # Verify DB persistence
        stored_msgs = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(stored_msgs) == 2, f"Expected user and assistant in DB, got {len(stored_msgs)}"

        assistant_row = stored_msgs[1]
        assert assistant_row["role"] == "assistant"
        assert assistant_row["model"] == "claude-3-5-sonnet"
        assert assistant_row["provider"] == "anthropic"
        assert len(assistant_row["content"]) > 0
        assert assistant_row["tokens_in"] > 0
        assert assistant_row["tokens_out"] > 0

    def test_openai_sse_stream_persisted_with_token_attribution(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify that an OpenAI SSE stream ending in [DONE] is consumed and
        simultaneously saved to the messages table with correct token metadata.
        """
        conv_id = "conv_sse_openai_002"
        payload = {
            "model": "gpt-4o",
            "conversation_id": conv_id,
            "stream": True,
            "messages": [
                {"role": "user", "content": "List 3 hashtags for AI automation."}
            ]
        }

        resp = e2e_client.post(
            f"{mock_gateway_server.url}/v1/chat/completions",
            json=payload,
            stream=True
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("Content-Type", "")

        # Consume OpenAI chunks
        chunks = list(parse_openai_sse(resp))
        assert len(chunks) >= 2, "Expected multiple OpenAI chunk events"

        # Verify DB persistence
        stored_msgs = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(stored_msgs) == 2

        assistant_row = stored_msgs[1]
        assert assistant_row["role"] == "assistant"
        assert assistant_row["model"] == "gpt-4o"
        assert assistant_row["provider"] == "openai"
        assert len(assistant_row["content"]) > 0
        assert assistant_row["tokens_out"] > 0

    def test_sse_stream_rate_limit_headers_and_db_integrity(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify that streaming SSE requests receive synthetic unbottlenecked rate limit
        headers while successfully recording to SQLite without locking issues.
        """
        conv_id = "conv_sse_headers_003"
        payload = {
            "model": "claude-3-5-haiku",
            "conversation_id": conv_id,
            "stream": True,
            "messages": [{"role": "user", "content": "Test headers."}]
        }

        resp = e2e_client.post(
            f"{mock_gateway_server.url}/v1/messages",
            json=payload,
            stream=True
        )
        assert resp.status_code == 200

        # Assert synthetic rate limit headers are present on SSE stream
        assert "anthropic-ratelimit-requests-limit" in resp.headers
        assert int(resp.headers["anthropic-ratelimit-requests-limit"]) >= 1000
        assert "anthropic-ratelimit-tokens-limit" in resp.headers
        assert int(resp.headers["anthropic-ratelimit-tokens-limit"]) >= 100000

        # Consume stream
        text = extract_sse_text(resp)
        assert len(text) > 0

        # Confirm DB record
        conv = e2e_db.get_conversation(conv_id)
        assert conv is not None

    def test_multiple_distinct_sse_streams_persisted_cleanly(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify multiple distinct SSE streams across different models and conversation IDs
        persist their tokens and dialogue history cleanly and independently in SQLite.
        """
        test_cases = [
            ("conv_stream_multi_1", "claude-3-5-sonnet", "/v1/messages", "Draft hook A"),
            ("conv_stream_multi_2", "gpt-4o", "/v1/chat/completions", "Draft hook B"),
            ("conv_stream_multi_3", "claude-coding-router-preview", "/v1/messages", "Draft hook C"),
        ]

        for cid, model, endpoint, prompt in test_cases:
            if "messages" in endpoint:
                payload = {
                    "model": model,
                    "conversation_id": cid,
                    "stream": True,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 256
                }
            else:
                payload = {
                    "model": model,
                    "conversation_id": cid,
                    "stream": True,
                    "messages": [{"role": "user", "content": prompt}],
                }

            resp = e2e_client.post(f"{mock_gateway_server.url}{endpoint}", json=payload, stream=True)
            assert resp.status_code == 200
            text = extract_sse_text(resp)
            assert len(text) > 0

            # Verify isolated persistence
            msgs = e2e_db.get_conversation_messages(cid, format="raw")
            assert len(msgs) == 2, f"Expected 2 messages for {cid}, got {len(msgs)}"
            assert msgs[0]["content"] == prompt
            assert msgs[1]["role"] == "assistant"
            assert msgs[1]["model"] == model
            assert msgs[1]["tokens_out"] > 0

    def test_sse_stream_accumulated_text_matches_db_content(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify exact parity between the accumulated SSE delta chunks
        received by the client and the content string persisted in SQLite.
        """
        conv_id = "conv_text_parity_005"
        payload = {
            "model": "deepseek-v3",
            "conversation_id": conv_id,
            "stream": True,
            "messages": [{"role": "user", "content": "Summarize key takeaway."}]
        }

        resp = e2e_client.post(
            f"{mock_gateway_server.url}/v1/chat/completions",
            json=payload,
            headers={"Connection": "close"},
            stream=True
        )
        assert resp.status_code == 200
        streamed_text = extract_sse_text(resp)

        import time
        time.sleep(0.05)

        msgs = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(msgs) == 2
        persisted_text = msgs[1]["content"]

        assert streamed_text == persisted_text, (
            f"Streamed text '{streamed_text}' does not match persisted text '{persisted_text}'"
        )
