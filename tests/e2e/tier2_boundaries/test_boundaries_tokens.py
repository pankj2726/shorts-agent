"""
test_boundaries_tokens.py — Tier 2 Boundary Tests for Token Parameters & Limits.

Covers:
  - 0 tokens, negative tokens, boundary tokens (128k, 200k, extreme limits)
  - max_completion_tokens vs max_tokens compatibility (OpenAI reasoning models)
  - Float / string / null token parameter resilience
  - Token usage accounting in non-streaming and SSE streaming
  - SQLite token storage boundaries
  - UniversalCapabilityRegistry parameter translation boundaries
"""

from __future__ import annotations

import json
from typing import Any, Dict
import pytest
import requests

from agent.modules.capability_registry import UniversalCapabilityRegistry


# ─── Anthropic Token Boundary Tests ──────────────────────────────────────────

def test_zero_max_tokens_anthropic(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test sending max_tokens=0 to /v1/messages endpoint."""
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 0
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    # Gateway accepts and processes or handles safely without unhandled exception
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        data = resp.json()
        assert "content" in data
        assert "usage" in data


def test_negative_max_tokens_anthropic(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test sending negative max_tokens (-1) to /v1/messages."""
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Test negative tokens"}],
        "max_tokens": -1
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code in (200, 400)
    data = resp.json()
    assert isinstance(data, dict)


def test_upper_boundary_max_tokens_128k_anthropic(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test sending upper boundary max_tokens=131072 (128k) to /v1/messages."""
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "High token limit request"}],
        "max_tokens": 131072
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "message"
    assert data["model"] == "claude-3-5-sonnet"
    assert "usage" in data


def test_upper_boundary_max_tokens_200k_anthropic(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test sending upper boundary max_tokens=200000 (200k) to /v1/messages."""
    payload = {
        "model": "claude-3-5-haiku",
        "messages": [{"role": "user", "content": "200k token boundary request"}],
        "max_tokens": 200000
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "claude-3-5-haiku"


def test_null_max_tokens_anthropic(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test sending max_tokens=None (JSON null) to /v1/messages."""
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Null max tokens"}],
        "max_tokens": None
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data


# ─── OpenAI Token Boundary Tests ─────────────────────────────────────────────

def test_zero_max_tokens_openai(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test sending max_tokens=0 to /v1/chat/completions."""
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Zero tokens completion"}],
        "max_tokens": 0
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code in (200, 400)
    data = resp.json()
    assert "choices" in data or "error" in data


def test_negative_max_tokens_openai(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test sending negative max_tokens (-100) to /v1/chat/completions."""
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Negative tokens"}],
        "max_tokens": -100
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code in (200, 400)


def test_upper_boundary_max_tokens_128k_openai(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test sending upper boundary max_tokens=131072 to /v1/chat/completions."""
    payload = {
        "model": "deepseek-v3",
        "messages": [{"role": "user", "content": "DeepSeek 128k token context test"}],
        "max_tokens": 131072
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "deepseek-v3"
    assert len(data["choices"]) > 0


def test_max_completion_tokens_parameter_openai(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test modern max_completion_tokens parameter (o1/o3/reasoning models) in /v1/chat/completions."""
    payload = {
        "model": "claude-coding-router-preview",
        "messages": [{"role": "user", "content": "Reasoning model test with max_completion_tokens"}],
        "max_completion_tokens": 16384
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["finish_reason"] == "stop"


def test_zero_max_completion_tokens(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test sending max_completion_tokens=0 to /v1/chat/completions."""
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Zero max_completion_tokens"}],
        "max_completion_tokens": 0
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code in (200, 400)


def test_max_tokens_and_max_completion_tokens_coexistence(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test both max_tokens and max_completion_tokens present in the same request payload."""
    payload = {
        "model": "mistral-large-2411",
        "messages": [{"role": "user", "content": "Coexisting token bounds"}],
        "max_tokens": 4096,
        "max_completion_tokens": 8192
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "mistral-large-2411"


def test_extreme_large_token_value(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test extreme token boundary (10,000,000 tokens) without overflow."""
    payload = {
        "model": "llama-3.1-405b",
        "messages": [{"role": "user", "content": "Extreme token boundary"}],
        "max_tokens": 10000000
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "llama-3.1-405b"


def test_floating_point_max_tokens(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test floating point max_tokens=256.7 in request JSON."""
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Float max tokens"}],
        "max_tokens": 256.7
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code in (200, 400)


def test_null_max_tokens_openai(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test sending max_tokens=None to /v1/chat/completions."""
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Null max tokens"}],
        "max_tokens": None
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "choices" in data


# ─── Token Usage Accounting Boundaries ───────────────────────────────────────

def test_token_usage_accounting_anthropic_non_streaming(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Verify non-streaming Anthropic usage block conforms to non-negative integer schema."""
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Accounting test"}], "max_tokens": 100},
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    usage = data.get("usage", {})
    assert "input_tokens" in usage
    assert "output_tokens" in usage
    assert isinstance(usage["input_tokens"], int) and usage["input_tokens"] >= 0
    assert isinstance(usage["output_tokens"], int) and usage["output_tokens"] >= 0


def test_token_usage_accounting_anthropic_streaming(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Verify Anthropic SSE stream message_start and message_delta emit valid token usage."""
    from tests.e2e.conftest import parse_anthropic_sse

    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Stream accounting"}], "stream": True},
        stream=True,
        timeout=5.0
    )
    assert resp.status_code == 200
    events = list(parse_anthropic_sse(resp))
    start_events = [e for e in events if e.get("type") == "message_start"]
    delta_events = [e for e in events if e.get("type") == "message_delta"]

    assert len(start_events) > 0
    assert "usage" in start_events[0]["message"]
    assert start_events[0]["message"]["usage"]["input_tokens"] >= 0

    assert len(delta_events) > 0
    assert "usage" in delta_events[0]
    assert delta_events[0]["usage"]["output_tokens"] >= 0


def test_token_usage_accounting_openai_non_streaming(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Verify OpenAI usage block satisfies total_tokens == prompt_tokens + completion_tokens."""
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Usage math test"}], "max_tokens": 50},
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    usage = data.get("usage", {})
    assert "prompt_tokens" in usage
    assert "completion_tokens" in usage
    assert "total_tokens" in usage
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_token_usage_accounting_openai_streaming(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Verify OpenAI streaming SSE chunks deliver content deltas without token calculation crash."""
    from tests.e2e.conftest import parse_openai_sse

    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Stream chunks"}], "stream": True},
        stream=True,
        timeout=5.0
    )
    assert resp.status_code == 200
    chunks = list(parse_openai_sse(resp))
    assert len(chunks) >= 2
    # Verify finish_reason stop chunk is present
    stop_chunks = [c for c in chunks if c.get("choices") and c["choices"][0].get("finish_reason") == "stop"]
    assert len(stop_chunks) > 0


# ─── SQLite Storage & Registry Translation Token Boundaries ──────────────────

def test_db_token_storage_zero_and_large_boundaries(e2e_db):
    """Test saving messages with zero tokens and extreme 64-bit integer tokens in SQLite."""
    cid = e2e_db.create_conversation(title="Token Boundary DB Test")

    # Zero tokens
    mid_zero = e2e_db.save_message(
        conversation_id=cid,
        role="assistant",
        content="Zero token answer",
        model="claude-3-5-sonnet",
        provider="anthropic",
        tokens_in=0,
        tokens_out=0
    )
    msgs = e2e_db.get_conversation_messages(cid, format="raw")
    m_zero = next(m for m in msgs if m["id"] == mid_zero)
    assert m_zero["tokens_in"] == 0
    assert m_zero["tokens_out"] == 0

    # Extreme boundary (2^31 - 1)
    large_val = 2147483647
    mid_large = e2e_db.save_message(
        conversation_id=cid,
        role="assistant",
        content="Large token answer",
        model="llama-3.1-405b",
        provider="deepinfra",
        tokens_in=large_val,
        tokens_out=large_val
    )
    msgs = e2e_db.get_conversation_messages(cid, format="raw")
    m_large = next(m for m in msgs if m["id"] == mid_large)
    assert m_large["tokens_in"] == large_val
    assert m_large["tokens_out"] == large_val


def test_capability_registry_token_parameter_translation():
    """Verify UniversalCapabilityRegistry maps token parameters correctly across providers."""
    # Test translation of reasoning effort and canonical models
    params_arena = UniversalCapabilityRegistry.translate_parameters(
        canonical_id="claude-3-5-sonnet",
        target_provider="arena",
        effort="high"
    )
    assert "model" in params_arena

    params_anthropic = UniversalCapabilityRegistry.translate_parameters(
        canonical_id="claude-3-5-haiku",
        target_provider="anthropic"
    )
    assert params_anthropic.get("model") == "claude-3-5-haiku-20241022"

    params_groq = UniversalCapabilityRegistry.translate_parameters(
        canonical_id="llama-3.1-405b",
        target_provider="groq"
    )
    assert "model" in params_groq
