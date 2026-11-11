"""
test_chat_completions.py — Feature 14: OpenAI /v1/chat/completions SSE Streaming & JSON Mode.

Validates:
  1. Synchronous JSON mode compliant with OpenAI Chat Completions API.
  2. SSE chunk streaming mode emitting delta objects and terminating with [DONE].
  3. Stream accumulation via extract_sse_text helper.
  4. Automatic persistence of chat completion turns into SQLite database.
  5. Multi-model compatibility (gpt-4o, deepseek-v3, llama-3.1-405b).
"""

from __future__ import annotations

import json
import requests
import pytest

from tests.e2e.conftest import parse_openai_sse, extract_sse_text


def test_chat_completions_json_mode(e2e_client, openai_headers):
    """Verify POST /v1/chat/completions returns OpenAI-compliant JSON completion."""
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Explain quantum entanglement in 10 words."}],
        "stream": False
    }

    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers={**openai_headers, "Connection": "close"},
        json=payload
    )

    assert resp.status_code == 200
    assert "application/json" in resp.headers.get("Content-Type", "")
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "gpt-4o"
    assert len(data["choices"]) > 0
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert len(data["choices"][0]["message"]["content"]) > 0
    resp.close()


def test_chat_completions_sse_streaming(e2e_client, openai_headers):
    """Verify POST /v1/chat/completions with stream=True emits OpenAI delta chunks and terminates."""
    payload = {
        "model": "deepseek-v3",
        "messages": [{"role": "user", "content": "Stream completion"}],
        "stream": True
    }

    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers={**openai_headers, "Connection": "close"},
        json=payload,
        stream=True
    )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("Content-Type", "")

    chunks = list(parse_openai_sse(resp))
    resp.close()
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.get("object") == "chat.completion.chunk"
        assert "choices" in chunk


def test_chat_completions_extract_text(e2e_client, openai_headers):
    """Verify extract_sse_text properly reconstructs text from OpenAI delta chunks."""
    payload = {
        "model": "mistral-large-2411",
        "messages": [{"role": "user", "content": "Reconstruct test"}],
        "stream": True
    }

    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers={**openai_headers, "Connection": "close"},
        json=payload,
        stream=True
    )

    text = extract_sse_text(resp)
    resp.close()
    assert len(text) > 0
    assert "mistral-large-2411" in text


def test_chat_completions_persists_to_db(e2e_client, openai_headers, e2e_db):
    """Verify chat completion dialogue is persisted to SQLite store with user and assistant roles."""
    conv_id = "conv_openai_persistence_test"
    payload = {
        "model": "llama-3.1-405b",
        "conversation_id": conv_id,
        "messages": [{"role": "user", "content": "Save this completion turn"}],
        "stream": False
    }

    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers={**openai_headers, "Connection": "close", "x-conversation-id": conv_id},
        json=payload
    )
    assert resp.status_code == 200
    resp.close()

    db_msgs = e2e_db.get_conversation_messages(conv_id, format="raw")
    assert len(db_msgs) >= 2
    roles = [m["role"] for m in db_msgs]
    assert "user" in roles
    assert "assistant" in roles


def test_chat_completions_custom_model_echo(e2e_client, openai_headers):
    """Verify model parameter passed in request is reflected in the completion output."""
    payload = {
        "model": "grok-2",
        "messages": [{"role": "user", "content": "Test model echo"}],
        "stream": False
    }

    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers={**openai_headers, "Connection": "close"},
        json=payload
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "grok-2"
    resp.close()
