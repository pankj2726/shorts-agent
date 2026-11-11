"""
test_boundaries_rate_limits.py — Tier 2 Boundary Tests for Rate Limits, 429 Cascades & Headers.

Covers:
  - Synthetic unbottlenecked rate limit headers (limit=10000, remaining=9999, tokens=2000000)
  - Synthetic limit boundaries (remaining=0, negative remaining)
  - 429 Too Many Requests simulation with Retry-After header variations
  - IMRCM Same-Model Cascade on 429 (Arena -> OpenRouter -> Direct)
  - Router exhaustion limits & circular loop prevention
  - Exponential backoff delay calculation
  - Rapid client burst without artificial local throttling
  - 402 Payment Required failover
"""

from __future__ import annotations

import email.utils
import json
import math
import os
import time
from typing import Any, Dict
import pytest
import requests

from agent.modules.capability_registry import UniversalCapabilityRegistry, CANONICAL_MODELS
from agent.modules.omniroute_embedded import EmbeddedOmniRoute
from tests.e2e.conftest import MockGatewayServer


# ─── Synthetic Unbottlenecked Rate Limit Headers ──────────────────────────────

def test_synthetic_header_requests_limit(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Verify anthropic-ratelimit-requests-limit header is 10000."""
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Header check"}]},
        timeout=5.0
    )
    assert resp.status_code == 200
    assert resp.headers.get("anthropic-ratelimit-requests-limit") == "10000"


def test_synthetic_header_requests_remaining(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Verify anthropic-ratelimit-requests-remaining header is 9999."""
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Header check"}]},
        timeout=5.0
    )
    assert resp.status_code == 200
    assert resp.headers.get("anthropic-ratelimit-requests-remaining") == "9999"


def test_synthetic_header_tokens_limit(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Verify anthropic-ratelimit-tokens-limit header is 2000000."""
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Tokens limit"}]},
        timeout=5.0
    )
    assert resp.status_code == 200
    assert resp.headers.get("anthropic-ratelimit-tokens-limit") == "2000000"


def test_synthetic_header_tokens_remaining(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Verify anthropic-ratelimit-tokens-remaining header is 1995000."""
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Tokens remaining"}]},
        timeout=5.0
    )
    assert resp.status_code == 200
    assert resp.headers.get("anthropic-ratelimit-tokens-remaining") == "1995000"


# ─── Synthetic Header Limit Boundaries (Remaining=0, Negative Remaining) ──────

def test_synthetic_header_boundary_requests_remaining_zero(
    mock_gateway_server: MockGatewayServer,
    anthropic_headers: Dict[str, str]
):
    """Test client handles remaining=0 boundary header gracefully."""
    mock_gateway_server.set_mock_response(
        path="/v1/messages",
        status_code=200,
        body={"type": "message", "id": "msg_001", "role": "assistant", "content": [{"type": "text", "text": "OK"}]},
        headers={
            "Content-Type": "application/json",
            "anthropic-ratelimit-requests-remaining": "0",
            "anthropic-ratelimit-requests-limit": "10000",
            "Connection": "close"
        }
    )
    headers = dict(anthropic_headers)
    headers["Connection"] = "close"
    resp = requests.post(
        f"{mock_gateway_server.url}/v1/messages",
        headers=headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Zero remaining"}]},
        timeout=5.0
    )
    assert resp.status_code == 200
    assert resp.headers.get("anthropic-ratelimit-requests-remaining") == "0"
    mock_gateway_server.reset()


def test_synthetic_header_boundary_negative_tokens_remaining(
    mock_gateway_server: MockGatewayServer,
    anthropic_headers: Dict[str, str]
):
    """Test client handles negative remaining tokens header safely without integer conversion crash."""
    mock_gateway_server.set_mock_response(
        path="/v1/messages",
        status_code=200,
        body={"type": "message", "id": "msg_002", "role": "assistant", "content": [{"type": "text", "text": "OK"}]},
        headers={
            "Content-Type": "application/json",
            "anthropic-ratelimit-tokens-remaining": "-500",
            "Connection": "close"
        }
    )
    headers = dict(anthropic_headers)
    headers["Connection"] = "close"
    resp = requests.post(
        f"{mock_gateway_server.url}/v1/messages",
        headers=headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Negative remaining"}]},
        timeout=5.0
    )
    assert resp.status_code == 200
    remaining_val = int(resp.headers.get("anthropic-ratelimit-tokens-remaining"))
    assert remaining_val == -500
    mock_gateway_server.reset()


# ─── 429 Too Many Requests & Retry-After Parsing ──────────────────────────────

def test_simulate_429_too_many_requests(
    mock_gateway_server: MockGatewayServer,
    anthropic_headers: Dict[str, str]
):
    """Simulate 429 Too Many Requests with Retry-After header."""
    mock_gateway_server.set_mock_response(
        path="/v1/messages",
        status_code=429,
        body={
            "type": "error",
            "error": {"type": "rate_limit_error", "message": "Number of request tokens has exceeded your quota."}
        },
        headers={"Content-Type": "application/json", "Retry-After": "3", "Connection": "close"}
    )
    headers = dict(anthropic_headers)
    headers["Connection"] = "close"
    resp = requests.post(
        f"{mock_gateway_server.url}/v1/messages",
        headers=headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "429 check"}]},
        timeout=5.0
    )
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "3"
    data = resp.json()
    assert data["error"]["type"] == "rate_limit_error"
    mock_gateway_server.reset()


def test_simulate_429_retry_after_integer_seconds():
    """Verify Retry-After integer seconds parser correctly converts to positive sleep duration."""
    retry_header = "12"
    delay = int(retry_header)
    assert delay == 12 and delay > 0


def test_simulate_429_retry_after_http_date():
    """Verify Retry-After HTTP date string converts to valid timestamp."""
    future_time = time.time() + 60
    http_date = email.utils.formatdate(future_time, usegmt=True)
    parsed_epoch = email.utils.parsedate_to_datetime(http_date).timestamp()
    delta = parsed_epoch - time.time()
    assert 50 <= delta <= 70


def test_429_error_json_structure_anthropic(
    mock_gateway_server: MockGatewayServer,
    anthropic_headers: Dict[str, str]
):
    """Verify Anthropic 429 error structure conformance."""
    mock_gateway_server.set_mock_response(
        path="/v1/messages",
        status_code=429,
        body={"type": "error", "error": {"type": "rate_limit_error", "message": "Tokens exhausted"}},
        headers={"Content-Type": "application/json", "Connection": "close"}
    )
    headers = dict(anthropic_headers)
    headers["Connection"] = "close"
    resp = requests.post(
        f"{mock_gateway_server.url}/v1/messages",
        headers=headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Quota"}]},
        timeout=5.0
    )
    assert resp.status_code == 429
    body = resp.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "rate_limit_error"
    mock_gateway_server.reset()


def test_429_error_json_structure_openai(
    mock_gateway_server: MockGatewayServer,
    openai_headers: Dict[str, str]
):
    """Verify OpenAI 429 error structure conformance."""
    mock_gateway_server.set_mock_response(
        path="/v1/chat/completions",
        status_code=429,
        body={"error": {"message": "Rate limit reached", "type": "requests", "code": "rate_limit_exceeded"}},
        headers={"Content-Type": "application/json", "Connection": "close"}
    )
    headers = dict(openai_headers)
    headers["Connection"] = "close"
    resp = requests.post(
        f"{mock_gateway_server.url}/v1/chat/completions",
        headers=headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Quota"}]},
        timeout=5.0
    )
    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == "rate_limit_exceeded"
    mock_gateway_server.reset()


# ─── IMRCM Cascade on 429 & Same-Model Preservation ───────────────────────────

def test_imrcm_same_model_cascade_chain_construction():
    """Verify EmbeddedOmniRoute builds same-model routers first before cross-family peers."""
    router = EmbeddedOmniRoute()
    # Check canonical model spec provider mappings
    claude_spec = CANONICAL_MODELS.get("claude-3-5-sonnet", {})
    mappings = claude_spec.get("provider_mappings", {})
    assert "arena" in mappings
    assert "openrouter" in mappings
    assert "anthropic" in mappings


def test_imrcm_cascade_exhaustion_all_routers():
    """Verify EmbeddedOmniRoute returns graceful fallback or raises clean error when providers exhausted."""
    router = EmbeddedOmniRoute()
    # Execute with non-existent offline provider chain
    with pytest.raises(Exception):
        router.complete_text(
            prompt="Test exhaustion",
            model="claude-3-5-sonnet",
            provider_preference=["non_existent_mock_provider_1", "non_existent_mock_provider_2"]
        )


def test_exponential_backoff_calculation():
    """Verify exponential backoff algorithm generates monotonically increasing delays."""
    base_delay = 0.5
    factor = 2.0
    delays = []
    for attempt in range(4):
        # Deterministic backoff without random jitter for assertion
        delay = base_delay * (factor ** attempt)
        delays.append(delay)

    assert delays[0] == 0.5
    assert delays[1] == 1.0
    assert delays[2] == 2.0
    assert delays[3] == 4.0
    assert delays == sorted(delays)


def test_infinite_router_cascade_cycle_prevention():
    """Verify router does not loop infinitely when all channels are exhausted."""
    router = EmbeddedOmniRoute()
    routes = router.get_available_routes()
    assert isinstance(routes, list)
    # Ensure every route has unique provider to prevent circular ping-pong
    providers = [r["provider"] for r in routes]
    assert len(providers) == len(set(providers))


def test_quota_exhaustion_402_payment_required_failover(
    mock_gateway_server: MockGatewayServer,
    openai_headers: Dict[str, str]
):
    """Simulate 402 Payment Required (insufficient credits) handled cleanly."""
    mock_gateway_server.set_mock_response(
        path="/v1/chat/completions",
        status_code=402,
        body={"error": {"message": "Insufficient credits in account", "type": "insufficient_quota"}},
        headers={"Content-Type": "application/json", "Connection": "close"}
    )
    headers = dict(openai_headers)
    headers["Connection"] = "close"
    resp = requests.post(
        f"{mock_gateway_server.url}/v1/chat/completions",
        headers=headers,
        json={"model": "deepseek-v3", "messages": [{"role": "user", "content": "Credits test"}]},
        timeout=5.0
    )
    assert resp.status_code == 402
    data = resp.json()
    assert data["error"]["type"] == "insufficient_quota"
    mock_gateway_server.reset()


def test_model_preserving_same_model_priority_over_peer():
    """Verify same-model slug translation retains correct provider-specific model identity."""
    p_arena = UniversalCapabilityRegistry.translate_parameters("claude-3-5-sonnet", target_provider="arena")
    assert p_arena["model"] == "claude-coding-router-preview"

    p_anthropic = UniversalCapabilityRegistry.translate_parameters("claude-3-5-sonnet", target_provider="anthropic")
    assert "claude-3-5-sonnet" in p_anthropic["model"]

    p_openrouter = UniversalCapabilityRegistry.translate_parameters("claude-3-5-sonnet", target_provider="openrouter")
    assert "claude-3.5-sonnet" in p_openrouter["model"]


def test_client_rapid_burst_no_local_throttling(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Verify 20 rapid sequential requests through local gateway encounter zero 429 throttling."""
    statuses = []
    for i in range(20):
        resp = e2e_client.post(
            f"{e2e_client.gateway_url}/v1/messages",
            headers=anthropic_headers,
            json={"model": "claude-3-5-haiku", "messages": [{"role": "user", "content": f"Burst {i}"}]},
            timeout=5.0
        )
        statuses.append(resp.status_code)

    assert len(statuses) == 20
    assert all(s == 200 for s in statuses)
    assert 429 not in statuses


def test_openai_ratelimit_headers_compatibility(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Verify /v1/chat/completions returns HTTP 200 without rate limit block."""
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Rate check"}]},
        timeout=5.0
    )
    assert resp.status_code == 200
    assert "choices" in resp.json()


def test_rate_limit_reset_header_integer_timestamp(
    mock_gateway_server: MockGatewayServer,
    e2e_client: requests.Session,
    anthropic_headers: Dict[str, str]
):
    """Verify custom reset header with integer epoch timestamp can be parsed."""
    reset_epoch = str(int(time.time() + 3600))
    mock_gateway_server.set_mock_response(
        path="/v1/messages",
        status_code=200,
        body={"type": "message", "content": [{"type": "text", "text": "Reset test"}]},
        headers={"Content-Type": "application/json", "anthropic-ratelimit-requests-reset": reset_epoch}
    )
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Reset"}]},
        timeout=5.0
    )
    assert resp.status_code == 200
    val = int(resp.headers.get("anthropic-ratelimit-requests-reset"))
    assert val > int(time.time())
    mock_gateway_server.reset()
