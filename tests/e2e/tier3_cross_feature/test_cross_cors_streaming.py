"""
test_cross_cors_streaming.py — Tier 3 Cross-Feature Combination Tests.

Covers Feature Interaction:
  - Feature 16: External Client & Android Compatibility (CORS preflight OPTIONS *, LAN access).
  - Feature 13 / Feature 14: Streaming SSE endpoints (/v1/messages, /v1/chat/completions).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
import pytest
import requests

from tests.e2e.conftest import (
    MockGatewayServer,
    parse_anthropic_sse,
    parse_openai_sse,
    extract_sse_text,
)
import tests.e2e.conftest as conftest_module


@pytest.fixture(autouse=True)
def force_ephemeral_port(monkeypatch: pytest.MonkeyPatch):
    """Ensure each mock gateway server binds to a unique ephemeral port."""
    monkeypatch.setattr(conftest_module, "_find_available_port", lambda preferred=0: 0)


class TestCrossCorsStreaming:
    """Pairwise combination testing: Mobile CORS Preflight <-> Continuous SSE Streaming."""

    def test_cors_preflight_and_anthropic_sse_stream(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session
    ) -> None:
        """
        Verify that a mobile browser / Android webview client sending an OPTIONS
        preflight to /v1/messages receives valid CORS headers, followed by
        a continuous SSE stream with Access-Control-Allow-Origin intact.
        """
        # 1. CORS Preflight OPTIONS
        preflight_headers = {
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, x-api-key, anthropic-version",
            "Connection": "close"
        }
        opt_resp = e2e_client.options(
            f"{mock_gateway_server.url}/v1/messages",
            headers=preflight_headers
        )
        assert opt_resp.status_code == 200
        assert opt_resp.headers.get("Access-Control-Allow-Origin") == "*"
        assert "POST" in opt_resp.headers.get("Access-Control-Allow-Methods", "")

        # 2. Continuous SSE Stream POST
        post_headers = {
            "Origin": "http://localhost:3000",
            "Content-Type": "application/json",
            "x-api-key": "sk-ant-test-mobile-key",
            "anthropic-version": "2023-06-01",
            "Connection": "close"
        }
        payload = {
            "model": "claude-3-5-sonnet",
            "stream": True,
            "messages": [{"role": "user", "content": "Stream 3 viral hooks for mobile app."}],
            "max_tokens": 512
        }

        stream_resp = e2e_client.post(
            f"{mock_gateway_server.url}/v1/messages",
            json=payload,
            headers=post_headers,
            stream=True
        )
        assert stream_resp.status_code == 200
        assert stream_resp.headers.get("Access-Control-Allow-Origin") == "*"
        assert "text/event-stream" in stream_resp.headers.get("Content-Type", "")

        # Verify stream content
        events = list(parse_anthropic_sse(stream_resp))
        assert len(events) >= 3
        types = [e.get("type") for e in events]
        assert "message_start" in types
        assert "content_block_delta" in types

    def test_cors_preflight_and_openai_sse_stream(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session
    ) -> None:
        """
        Verify that a client sending an OPTIONS preflight to /v1/chat/completions
        receives valid CORS headers, followed by an OpenAI SSE stream ending in [DONE].
        """
        # 1. CORS Preflight OPTIONS
        preflight_headers = {
            "Origin": "capacitor://localhost",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, Authorization",
            "Connection": "close"
        }
        opt_resp = e2e_client.options(
            f"{mock_gateway_server.url}/v1/chat/completions",
            headers=preflight_headers
        )
        assert opt_resp.status_code == 200
        assert opt_resp.headers.get("Access-Control-Allow-Origin") == "*"

        # 2. Stream POST
        post_headers = {
            "Origin": "capacitor://localhost",
            "Content-Type": "application/json",
            "Authorization": "Bearer sk-openai-test-key",
            "Connection": "close"
        }
        payload = {
            "model": "gpt-4o",
            "stream": True,
            "messages": [{"role": "user", "content": "Mobile test message."}]
        }

        stream_resp = e2e_client.post(
            f"{mock_gateway_server.url}/v1/chat/completions",
            json=payload,
            headers=post_headers,
            stream=True
        )
        assert stream_resp.status_code == 200
        assert stream_resp.headers.get("Access-Control-Allow-Origin") == "*"
        assert "text/event-stream" in stream_resp.headers.get("Content-Type", "")

        chunks = list(parse_openai_sse(stream_resp))
        assert len(chunks) >= 2
        content = "".join(
            c["choices"][0]["delta"].get("content", "")
            for c in chunks
            if "choices" in c and len(c["choices"]) > 0
        )
        assert "Mock completion" in content

    def test_cors_get_models_and_conversations_for_mobile_client(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session
    ) -> None:
        """
        Verify GET /v1/models and GET /v1/conversations respond with open CORS headers
        when requested from an external mobile origin.
        """
        mobile_headers = {"Origin": "http://10.92.57.208:3000", "Connection": "close"}

        # 1. Models endpoint
        res_models = e2e_client.get(f"{mock_gateway_server.url}/v1/models", headers=mobile_headers)
        assert res_models.status_code == 200
        assert res_models.headers.get("Access-Control-Allow-Origin") == "*"
        models_data = res_models.json()["data"]
        model_ids = [m["id"] for m in models_data]
        assert "claude-3-5-sonnet" in model_ids
        assert "claude-coding-router-preview" in model_ids

        # 2. Conversations endpoint
        res_convs = e2e_client.get(f"{mock_gateway_server.url}/v1/conversations", headers=mobile_headers)
        assert res_convs.status_code == 200
        assert res_convs.headers.get("Access-Control-Allow-Origin") == "*"
        assert "data" in res_convs.json()

    def test_custom_mobile_headers_allowed_in_preflight(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session
    ) -> None:
        """
        Verify that enterprise headers required by Android/mobile clients
        (anthropic-workspace-id, x-api-key, anthropic-version) are returned
        in Access-Control-Allow-Headers during preflight.
        """
        req_headers = "Content-Type, Authorization, x-api-key, anthropic-version, anthropic-workspace-id"
        opt_resp = e2e_client.options(
            f"{mock_gateway_server.url}/v1/messages",
            headers={
                "Origin": "http://127.0.0.1:8080",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": req_headers,
                "Connection": "close"
            }
        )
        assert opt_resp.status_code == 200
        allowed_headers = opt_resp.headers.get("Access-Control-Allow-Headers", "")
        for expected in ["anthropic-version", "anthropic-workspace-id", "x-api-key"]:
            assert expected in allowed_headers, f"Expected '{expected}' in Access-Control-Allow-Headers: {allowed_headers}"

    def test_simulated_lan_ip_client_cors_handshake_and_stream(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session
    ) -> None:
        """
        Verify that an external client on the local network (e.g. Android phone connected
        to PC via LAN IP 10.92.57.208) can execute full CORS handshake and stream response.
        """
        lan_origin = "http://10.92.57.208:5173"
        preflight = e2e_client.options(
            f"{mock_gateway_server.url}/v1/messages",
            headers={
                "Origin": lan_origin,
                "Access-Control-Request-Method": "POST",
                "Connection": "close"
            }
        )
        assert preflight.status_code == 200
        assert preflight.headers.get("Access-Control-Allow-Origin") == "*"

        # Send streamed generation from LAN origin
        stream_resp = e2e_client.post(
            f"{mock_gateway_server.url}/v1/messages",
            json={
                "model": "coding-router-preview",
                "stream": True,
                "messages": [{"role": "user", "content": "Script generation over LAN."}],
                "max_tokens": 128
            },
            headers={"Origin": lan_origin, "Connection": "close"},
            stream=True
        )
        assert stream_resp.status_code == 200
        assert stream_resp.headers.get("Access-Control-Allow-Origin") == "*"
        text = extract_sse_text(stream_resp)
        assert "coding-router-preview" in text
