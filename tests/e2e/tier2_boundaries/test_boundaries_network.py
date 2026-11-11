"""
test_boundaries_network.py — Tier 2 Boundary Tests for Network Failures & Streaming Resilience.

Covers:
  - Immediate connection drops (502 Bad Gateway, 503 Service Unavailable, 504 Gateway Timeout)
  - Client socket timeouts & read deadlines
  - Partial SSE chunk delivery & client disconnect mid-stream
  - Truncated streams lacking [DONE] or message_stop
  - Malformed SSE event/data headers and comments (: ping)
  - Corrupt / non-JSON request bodies and malformed Content-Length
  - Invalid HTTP verbs (PUT, DELETE) and 404 handling
  - Full stream reconstruction via extract_sse_text
"""

from __future__ import annotations

import io
import json
from typing import Any, Dict
import pytest
import requests

from tests.e2e.conftest import (
    MockGatewayServer,
    extract_sse_text,
    parse_anthropic_sse,
    parse_openai_sse,
    parse_sse_events
)


# ─── Upstream Network Errors & Status Codes ──────────────────────────────────

def test_immediate_connection_drop_502_bad_gateway(
    mock_gateway_server: MockGatewayServer,
    e2e_client: requests.Session,
    anthropic_headers: Dict[str, str]
):
    """Test handling when provider/router immediately returns 502 Bad Gateway."""
    mock_gateway_server.set_mock_response(
        path="/v1/messages",
        status_code=502,
        body={"error": {"type": "bad_gateway", "message": "Upstream router connection reset"}},
        headers={"Content-Type": "application/json"}
    )
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Hi"}]},
        timeout=5.0
    )
    assert resp.status_code == 502
    data = resp.json()
    assert data["error"]["type"] == "bad_gateway"
    mock_gateway_server.reset()


def test_upstream_503_service_unavailable(
    mock_gateway_server: MockGatewayServer,
    e2e_client: requests.Session,
    openai_headers: Dict[str, str]
):
    """Test handling 503 Service Unavailable during provider outages."""
    mock_gateway_server.set_mock_response(
        path="/v1/chat/completions",
        status_code=503,
        body={"error": {"type": "service_unavailable", "message": "GPU cluster temporarily overloaded"}},
        headers={"Content-Type": "application/json", "Retry-After": "5"}
    )
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
        timeout=5.0
    )
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "5"
    mock_gateway_server.reset()


def test_upstream_504_gateway_timeout(
    mock_gateway_server: MockGatewayServer,
    e2e_client: requests.Session,
    anthropic_headers: Dict[str, str]
):
    """Test handling 504 Gateway Timeout from slow upstream models."""
    mock_gateway_server.set_mock_response(
        path="/v1/messages",
        status_code=504,
        body={"error": {"type": "gateway_timeout", "message": "Inference deadline exceeded"}},
        headers={"Content-Type": "application/json"}
    )
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Hi"}]},
        timeout=5.0
    )
    assert resp.status_code == 504
    mock_gateway_server.reset()


def test_socket_timeout_client_handling():
    """Test client-side socket read timeout handling with unroutable address."""
    with pytest.raises((requests.exceptions.Timeout, requests.exceptions.RequestException)):
        # 192.0.2.1 is TEST-NET-1 (RFC 5737), guaranteed non-routable test address
        requests.get("http://192.0.2.1:81", timeout=0.1)


# ─── Partial & Truncated SSE Streams ──────────────────────────────────────────

def test_partial_sse_chunk_delivery_anthropic(mock_gateway_server: MockGatewayServer, anthropic_headers: Dict[str, str]):
    """Test client consuming only the first 2 SSE events and closing stream early."""
    headers = dict(anthropic_headers)
    headers["Connection"] = "close"
    resp = requests.post(
        f"{mock_gateway_server.url}/v1/messages",
        headers=headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Partial stream"}], "stream": True},
        stream=True,
        timeout=5.0
    )
    assert resp.status_code == 200

    collected = []
    # Consume only 2 events and then close response socket
    for item in parse_sse_events(resp):
        collected.append(item)
        if len(collected) >= 2:
            break
    resp.close()

    assert len(collected) == 2
    assert collected[0]["event"] in ("message_start", "message")


def test_partial_sse_chunk_delivery_openai(mock_gateway_server: MockGatewayServer, openai_headers: Dict[str, str]):
    """Test client consuming only the first chunk from OpenAI SSE stream."""
    headers = dict(openai_headers)
    headers["Connection"] = "close"
    resp = requests.post(
        f"{mock_gateway_server.url}/v1/chat/completions",
        headers=headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Partial chunk"}], "stream": True},
        stream=True,
        timeout=5.0
    )
    assert resp.status_code == 200

    collected = []
    for item in parse_sse_events(resp):
        collected.append(item)
        if len(collected) >= 1:
            break
    resp.close()

    assert len(collected) == 1
    assert isinstance(collected[0]["data"], dict)


def test_truncated_sse_stream_without_done_event():
    """Verify parse_sse_events parser handles stream abruptly severed without [DONE]."""
    # Create mock response object yielding raw lines without [DONE]
    class MockTruncatedResponse:
        def iter_lines(self, decode_unicode=True):
            yield 'event: message\n'
            yield 'data: {"id": "1", "choices": [{"delta": {"content": "Hello"}}]}\n'
            yield ''
            yield 'event: message\n'
            yield 'data: {"id": "2", "choices": [{"delta": {"content": " world"}}]}\n'
            yield ''
            # Stream cuts off abruptly here

    mock_resp = MockTruncatedResponse()
    events = list(parse_sse_events(mock_resp))  # type: ignore
    assert len(events) == 2
    assert events[0]["data"]["choices"][0]["delta"]["content"] == "Hello"
    assert events[1]["data"]["choices"][0]["delta"]["content"] == " world"


def test_malformed_chunk_header_missing_colon():
    """Verify parse_sse_events parser gracefully handles lines without standard SSE field format."""
    class MockMalformedResponse:
        def iter_lines(self, decode_unicode=True):
            yield 'corrupt_line_without_field\n'
            yield 'data: {"valid": true}\n'
            yield ''

    events = list(parse_sse_events(MockMalformedResponse()))  # type: ignore
    assert len(events) == 1
    assert events[0]["data"] == {"valid": True}


def test_sse_comment_lines_filtered():
    """Verify SSE comments starting with ':' (keep-alive heartbeats) are ignored."""
    class MockCommentResponse:
        def iter_lines(self, decode_unicode=True):
            yield ': ping keepalive\n'
            yield ': another comment\n'
            yield 'data: {"ping": "pong"}\n'
            yield ''

    events = list(parse_sse_events(MockCommentResponse()))  # type: ignore
    assert len(events) == 1
    assert events[0]["data"] == {"ping": "pong"}


def test_extra_consecutive_newlines_in_sse():
    """Verify parser handles multiple consecutive empty lines without emitting dummy events."""
    class MockSpacedResponse:
        def iter_lines(self, decode_unicode=True):
            yield ''
            yield ''
            yield 'data: {"message": "ok"}\n'
            yield ''
            yield ''
            yield ''

    events = list(parse_sse_events(MockSpacedResponse()))  # type: ignore
    assert len(events) == 1
    assert events[0]["data"] == {"message": "ok"}


def test_malformed_json_in_sse_data():
    """Verify that unparseable non-JSON data strings in SSE data are yielded as raw strings."""
    class MockCorruptedJSONResponse:
        def iter_lines(self, decode_unicode=True):
            yield 'data: {this is invalid json\n'
            yield ''

    events = list(parse_sse_events(MockCorruptedJSONResponse()))  # type: ignore
    assert len(events) == 1
    assert events[0]["data"] == "{this is invalid json"


# ─── HTTP Method & Payload Boundaries ─────────────────────────────────────────

def test_invalid_http_methods_anthropic(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test sending PUT to /v1/messages returns 404, 405, or 501 Not Implemented."""
    resp = e2e_client.put(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json={"model": "claude-3-5-sonnet"},
        timeout=5.0
    )
    assert resp.status_code in (404, 405, 501)


def test_invalid_http_methods_openai(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test sending DELETE to /v1/chat/completions returns 404, 405, or 501 Not Implemented."""
    resp = e2e_client.delete(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        timeout=5.0
    )
    assert resp.status_code in (404, 405, 501)


def test_malformed_content_length_zero_post(mock_gateway_server: MockGatewayServer, anthropic_headers: Dict[str, str]):
    """Test POST request with empty body (Content-Length: 0)."""
    headers = {k: v for k, v in anthropic_headers.items() if k != "Content-Type"}
    headers["Content-Length"] = "0"
    headers["Connection"] = "close"
    resp = requests.post(
        f"{mock_gateway_server.url}/v1/messages",
        headers=headers,
        data="",
        timeout=5.0
    )
    assert resp.status_code in (200, 400)


def test_corrupt_json_request_body(mock_gateway_server: MockGatewayServer, openai_headers: Dict[str, str]):
    """Test sending raw malformed JSON syntax in POST body."""
    raw_bad_json = '{"model": "gpt-4o", "messages": [{"role": "user", "content": broken_syntax}]}'
    headers = dict(openai_headers)
    headers["Connection"] = "close"
    resp = requests.post(
        f"{mock_gateway_server.url}/v1/chat/completions",
        headers=headers,
        data=raw_bad_json,
        timeout=5.0
    )
    assert resp.status_code in (200, 400, 500)


def test_large_payload_chunked_streaming_integrity(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Verify that full text response can be accurately reconstructed via extract_sse_text."""
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Echo test"}], "stream": True},
        stream=True,
        timeout=5.0
    )
    assert resp.status_code == 200
    accumulated_text = extract_sse_text(resp)
    assert isinstance(accumulated_text, str)
    assert len(accumulated_text) > 0
    assert "Mock response from claude-3-5-sonnet" in accumulated_text


def test_unregistered_route_404(e2e_client: requests.Session):
    """Test request to completely unknown route returns 404 with CORS headers."""
    resp = e2e_client.get(f"{e2e_client.gateway_url}/v1/unknown_endpoint_xyz", timeout=5.0)
    assert resp.status_code == 404
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"


def test_cors_preflight_options_response_headers(e2e_client: requests.Session):
    """Test OPTIONS preflight request returns standard CORS headers."""
    resp = e2e_client.options(f"{e2e_client.gateway_url}/v1/messages", timeout=5.0)
    assert resp.status_code == 200
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")
