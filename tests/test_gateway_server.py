"""
test_gateway_server.py — Unit test for Gateway Server & Unified Conversation Store.
"""

import json
import sqlite3
import threading
import time
import urllib.request
import pytest
from pathlib import Path

from agent.modules.gateway_server import ThreadedHTTPServer, GatewayHandler, DB_PATH, get_conversation_history


@pytest.fixture(scope="module")
def start_gateway():
    port = 8899
    server = ThreadedHTTPServer(("127.0.0.1", port), GatewayHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    yield port
    server.shutdown()
    server.server_close()


def test_gateway_health(start_gateway):
    port = start_gateway
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "healthy"
        assert data["available_models"] >= 16


def test_gateway_models_endpoint(start_gateway):
    port = start_gateway
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=3) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["object"] == "list"
        model_ids = [m["id"] for m in data["data"]]
        assert "claude-3-5-sonnet" in model_ids
        assert "o1" in model_ids
        assert "deepseek-v3" in model_ids


def test_unified_conversation_db():
    from agent.modules.gateway_server import save_message
    save_message("test_sess", "claude-3-5-sonnet", "arena", "user", "What is quantum gravity?")
    save_message("test_sess", "claude-3-5-sonnet", "arena", "assistant", "Quantum gravity seeks to unify...")

    history = get_conversation_history("test_sess", limit=10)
    assert len(history) >= 2
    assert history[0]["session_id"] == "test_sess"
    assert history[0]["content"] == "What is quantum gravity?"
