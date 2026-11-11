"""
test_gateway_daemon.py — Feature 12: Inbound Gateway HTTP Daemon Lifecycle & Core Routes.

Validates:
  1. /health endpoint reports service health and readiness.
  2. /v1/models endpoint returns catalog of registered frontier models.
  3. /v1/conversations endpoint exposes stored conversation threads.
  4. Proper 404 response on unrecognized routes.
  5. Multi-threaded daemon configuration allowing concurrent client sessions.
"""

from __future__ import annotations

import requests
import pytest

from tests.e2e.conftest import ThreadedMockServer


def test_mock_gateway_health_endpoint(e2e_client):
    """Verify /health endpoint returns 200 OK and healthy status."""
    resp = e2e_client.get(f"{e2e_client.gateway_url}/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") in ("ok", "healthy")


def test_mock_gateway_models_endpoint(e2e_client):
    """Verify /v1/models returns an OpenAI-compatible list of models."""
    resp = e2e_client.get(f"{e2e_client.gateway_url}/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("object") == "list"
    models = data.get("data", [])
    assert len(models) >= 5

    model_ids = {m["id"] for m in models}
    assert "claude-3-5-sonnet" in model_ids or "claude-3-5-haiku" in model_ids


def test_mock_gateway_conversations_endpoint(e2e_client, e2e_db):
    """Verify /v1/conversations returns list of conversation threads."""
    e2e_db.create_conversation("conv_gateway_daemon_01", title="Daemon Thread 1")

    resp = e2e_client.get(f"{e2e_client.gateway_url}/v1/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("object") == "list"
    items = data.get("data", [])
    assert any(c.get("id") == "conv_gateway_daemon_01" for c in items)


def test_mock_gateway_404_on_unknown_path(e2e_client):
    """Verify unmapped endpoints return HTTP 404."""
    resp = e2e_client.get(f"{e2e_client.gateway_url}/v1/invalid_endpoint_xyz")
    assert resp.status_code == 404


def test_gateway_server_threaded_configuration():
    """Verify ThreadedMockServer enables daemon threads and socket address reuse."""
    assert ThreadedMockServer.daemon_threads is True
    assert ThreadedMockServer.allow_reuse_address is True
