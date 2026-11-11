"""
test_limit_headers.py — Feature 15: Synthetic Unbottlenecked Rate Limit Headers.

Validates that the gateway daemon injects synthetic enterprise rate limit headers
into /v1/messages responses to prevent external client apps and Android clients
from artificially throttling or presenting paywall blockers to users:
  - anthropic-ratelimit-requests-limit: 10000
  - anthropic-ratelimit-requests-remaining: 9999
  - anthropic-ratelimit-tokens-limit: 2000000
  - anthropic-ratelimit-tokens-remaining: 1995000
"""

from __future__ import annotations

import requests
import pytest


def test_anthropic_requests_limit_header(e2e_client, anthropic_headers):
    """Verify anthropic-ratelimit-requests-limit header is present with unbottlenecked enterprise value."""
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers={**anthropic_headers, "Connection": "close"},
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert resp.status_code == 200
    assert resp.headers.get("anthropic-ratelimit-requests-limit") == "10000"
    resp.close()


def test_anthropic_requests_remaining_header(e2e_client, anthropic_headers):
    """Verify anthropic-ratelimit-requests-remaining header indicates abundant remaining capacity."""
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers={**anthropic_headers, "Connection": "close"},
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert resp.status_code == 200
    assert resp.headers.get("anthropic-ratelimit-requests-remaining") == "9999"
    resp.close()


def test_anthropic_tokens_limit_header(e2e_client, anthropic_headers):
    """Verify anthropic-ratelimit-tokens-limit header exposes 2,000,000 token limit."""
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers={**anthropic_headers, "Connection": "close"},
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert resp.status_code == 200
    assert resp.headers.get("anthropic-ratelimit-tokens-limit") == "2000000"
    resp.close()


def test_anthropic_tokens_remaining_header(e2e_client, anthropic_headers):
    """Verify anthropic-ratelimit-tokens-remaining header indicates abundant token capacity."""
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers={**anthropic_headers, "Connection": "close"},
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert resp.status_code == 200
    assert resp.headers.get("anthropic-ratelimit-tokens-remaining") == "1995000"
    resp.close()


def test_headers_present_in_both_streaming_and_json(e2e_client, anthropic_headers):
    """Verify all synthetic rate limit headers are preserved identically across streaming and JSON modes."""
    # JSON mode
    resp_json = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers={**anthropic_headers, "Connection": "close"},
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "hi"}], "stream": False}
    )

    # Streaming mode
    resp_stream = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers={**anthropic_headers, "Connection": "close"},
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        stream=True
    )

    for header in [
        "anthropic-ratelimit-requests-limit",
        "anthropic-ratelimit-requests-remaining",
        "anthropic-ratelimit-tokens-limit",
        "anthropic-ratelimit-tokens-remaining",
    ]:
        assert header in resp_json.headers
        assert header in resp_stream.headers
        assert resp_json.headers[header] == resp_stream.headers[header]

    resp_json.close()
    resp_stream.close()
