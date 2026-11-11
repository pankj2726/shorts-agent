"""
test_messages_sse.py — Feature 13: Anthropic /v1/messages SSE Streaming & JSON Mode.

Validates:
  1. Synchronous JSON request/response mode matching Anthropic Messages specification.
  2. Server-Sent Events (SSE) streaming mode producing compliant Anthropic events.
  3. Text delta concatenation via extract_sse_text helper.
  4. Automatic persistence of streamed conversation turns into SQLite database.
  5. Custom programmatic mock response overriding.
"""

from __future__ import annotations

import json
import requests
import pytest

from tests.e2e.conftest import parse_anthropic_sse, extract_sse_text


def test_anthropic_messages_json_mode(e2e_client, anthropic_headers):
    """Verify POST /v1/messages returns valid Anthropic JSON response when stream is false."""
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Hello Claude"}],
        "max_tokens": 1024,
        "stream": False
    }

    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload
    )

    assert resp.status_code == 200
    assert "application/json" in resp.headers.get("Content-Type", "")
    data = resp.json()
    assert data["type"] == "message"
    assert data["role"] == "assistant"
    assert data["model"] == "claude-3-5-sonnet"
    assert len(data["content"]) > 0
    assert data["content"][0]["type"] == "text"


def test_anthropic_messages_sse_streaming(e2e_client, anthropic_headers):
    """Verify POST /v1/messages with stream=True emits Anthropic SSE event sequence."""
    payload = {
        "model": "claude-3-5-haiku",
        "messages": [{"role": "user", "content": "Stream response"}],
        "stream": True
    }

    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        stream=True
    )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("Content-Type", "")

    events = list(parse_anthropic_sse(resp))
    event_types = [e.get("type") for e in events]

    assert "message_start" in event_types
    assert "content_block_delta" in event_types
    assert "message_stop" in event_types


def test_anthropic_messages_extract_text(e2e_client, anthropic_headers):
    """Verify extract_sse_text properly reconstructs the full message from text deltas."""
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Reconstruct test"}],
        "stream": True
    }

    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        stream=True
    )

    accumulated = extract_sse_text(resp)
    assert len(accumulated) > 0
    assert "claude-3-5-sonnet" in accumulated


def test_anthropic_messages_stream_persists_to_db(e2e_client, anthropic_headers, e2e_db):
    """Verify SSE streaming request persists user message and assistant reply to DB."""
    conv_id = "conv_sse_persistence_test"
    payload = {
        "model": "claude-3-5-sonnet",
        "conversation_id": conv_id,
        "messages": [{"role": "user", "content": "Persistent stream turn"}],
        "stream": True
    }

    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers={**anthropic_headers, "x-conversation-id": conv_id},
        json=payload,
        stream=True
    )
    _ = extract_sse_text(resp)

    # Check database messages
    db_msgs = e2e_db.get_conversation_messages(conv_id, format="raw")
    assert len(db_msgs) >= 2
    roles = [m["role"] for m in db_msgs]
    assert "user" in roles
    assert "assistant" in roles


def test_anthropic_messages_custom_mock_override(e2e_client, anthropic_headers, mock_gateway_server):
    """Verify set_mock_response allows injecting deterministic test fixtures."""
    mock_body = {
        "id": "msg_custom_override_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Deterministic mock text"}],
        "model": "custom-mock-model",
        "usage": {"input_tokens": 1, "output_tokens": 1}
    }
    mock_gateway_server.set_mock_response("/v1/messages", status_code=200, body=mock_body)

    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json={"model": "claude", "messages": []}
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "msg_custom_override_test"
    assert data["content"][0]["text"] == "Deterministic mock text"
    mock_gateway_server.reset()
