"""
test_android_cors.py — Feature 16: External Client & Android Compatibility (CORS & Binding).

Validates:
  1. CORS preflight (OPTIONS) requests succeed on /v1/messages with permissive headers.
  2. CORS preflight (OPTIONS) requests succeed on /v1/chat/completions.
  3. Standard GET responses (/v1/models) include Access-Control-Allow-Origin: *.
  4. Standard POST responses (/v1/chat/completions) include Access-Control-Allow-Origin: *.
  5. Gateway server configuration binds to 0.0.0.0 for LAN and mobile connectivity.
"""

from __future__ import annotations

import requests
import pytest

from agent.modules import gateway_server


def test_cors_options_preflight_messages(e2e_client):
    """Verify OPTIONS preflight on /v1/messages returns 200 and standard CORS headers."""
    resp = e2e_client.options(f"{e2e_client.gateway_url}/v1/messages", headers={"Connection": "close"})
    assert resp.status_code == 200
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")
    headers_allowed = resp.headers.get("Access-Control-Allow-Headers", "")
    assert "Authorization" in headers_allowed or "Content-Type" in headers_allowed
    resp.close()


def test_cors_options_preflight_chat_completions(e2e_client):
    """Verify OPTIONS preflight on /v1/chat/completions returns 200 and CORS headers."""
    resp = e2e_client.options(f"{e2e_client.gateway_url}/v1/chat/completions", headers={"Connection": "close"})
    assert resp.status_code == 200
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")
    resp.close()


def test_cors_get_models_includes_cors_header(e2e_client):
    """Verify GET /v1/models response includes Access-Control-Allow-Origin: *."""
    resp = e2e_client.get(f"{e2e_client.gateway_url}/v1/models", headers={"Connection": "close"})
    assert resp.status_code == 200
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    resp.close()


def test_cors_post_chat_completions_includes_cors_header(e2e_client, openai_headers):
    """Verify POST /v1/chat/completions response includes Access-Control-Allow-Origin: *."""
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers={**openai_headers, "Connection": "close"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert resp.status_code == 200
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    resp.close()


def test_gateway_bind_host_configuration():
    """Verify gateway server default run_server binds to 0.0.0.0 for LAN/mobile access."""
    import inspect
    source = inspect.getsource(gateway_server.run_server)
    assert "0.0.0.0" in source
