"""
test_session_router.py — Unit and integration tests for Cross-Platform Sessions Routing.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.modules import db
from agent.modules.gateway_server import GatewayHandler, ThreadedHTTPServer
from agent.modules.session_router import SessionRouter


@pytest.fixture(scope="module")
def gateway_server():
    port = 20131
    server = ThreadedHTTPServer(("127.0.0.1", port), GatewayHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.25)
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def test_claude_session_visible_on_arena_and_chatgpt(tmp_path):
    """A Claude session must appear across Arena.AI, ChatGPT, and Claude account views."""
    test_db = tmp_path / "sessions_vis.db"
    orig_db = db.DB_PATH
    db.DB_PATH = test_db
    try:
        db.init_db(test_db)
        sr = SessionRouter()
        sid = f"claude_sess_{int(time.time() * 1000)}"
        sr.create_session(
            session_id=sid,
            title="Claude Architecture Thread",
            platform="claude",
            model="claude-3-5-sonnet",
            messages=[
                {"role": "user", "content": "Design a resilient queue."},
                {"role": "assistant", "content": "Use an idempotent outbox table."},
            ],
        )

        arena_list = sr.list_sessions(platform="arena")
        chatgpt_list = sr.list_sessions(platform="chatgpt")
        claude_list = sr.list_sessions(platform="claude")

        assert any(s["session_id"] == sid for s in arena_list)
        assert any(s["session_id"] == sid for s in chatgpt_list)
        assert any(s["session_id"] == sid for s in claude_list)

        s = sr.get_session(sid)
        assert s is not None
        assert s["origin_platform"] == "claude"
        assert "arena" in s["visible_on_platforms"]
        assert "chatgpt" in s["visible_on_platforms"]
    finally:
        db.DB_PATH = orig_db


def test_cross_platform_route_and_interact_lifecycle(tmp_path):
    """Continue a Claude session seamlessly via Arena.AI and then ChatGPT."""
    test_db = tmp_path / "sessions_interact.db"
    orig_db = db.DB_PATH
    db.DB_PATH = test_db
    try:
        db.init_db(test_db)
        captured = []

        def fake_complete(prompt, system_prompt=None, model=None, effort=None, provider_preference=None):
            prov = (provider_preference or ["arena"])[0]
            captured.append({"prompt": prompt, "prov": prov, "model": model})
            return {
                "text": f"Reply from {prov}/{model}",
                "provider": prov,
                "canonical_model": model,
            }

        sr = SessionRouter()
        sid = "cross_plat_flow"
        sr.create_session(
            session_id=sid,
            title="Cross-Platform Flow",
            platform="claude",
            model="claude-3-5-sonnet",
            messages=[
                {"role": "user", "content": "Remember token ALPHA-42."},
                {"role": "assistant", "content": "Noted ALPHA-42."},
            ],
        )

        with patch.object(sr.router, "complete_text", side_effect=fake_complete):
            r_arena = sr.interact(sid, "What was my token?", target_platform="arena", target_model="coding-router-preview")
            assert r_arena["provider"] == "arena"
            assert "ALPHA-42" in captured[-1]["prompt"]

            r_gpt = sr.interact(sid, "Summarize in 5 words.", target_platform="chatgpt", target_model="gpt-4o")
            assert r_gpt["provider"] == "openai"
            assert "Reply from arena/coding-router-preview" in captured[-1]["prompt"]

        final_sess = sr.get_session(sid)
        assert final_sess is not None
        assert set(["claude", "arena", "chatgpt"]).issubset(set(final_sess["routed_platforms"]))
        assert final_sess["message_count"] == 6

        exported_claude = sr.export_session(sid, target_platform="claude", target_model="claude-3-5-sonnet")
        assert len(exported_claude["messages"]) == 6

        exported_gpt = sr.export_session(sid, target_platform="chatgpt", target_model="o3-mini")
        assert len(exported_gpt["messages"]) == 6
    finally:
        db.DB_PATH = orig_db


def test_gateway_sessions_rest_and_ui(gateway_server):
    """Verify Gateway /sessions HTML UI and /v1/sessions REST endpoints end-to-end."""
    # 1. HTML Dashboard
    with urllib.request.urlopen(f"{gateway_server}/sessions", timeout=5) as resp:
        assert resp.status == 200
        html = resp.read().decode("utf-8")
        assert "Cross-Platform Sessions Router" in html

    # 2. Create session via POST /v1/sessions
    sid = f"gw_sess_{int(time.time() * 1000)}"
    req_create = urllib.request.Request(
        f"{gateway_server}/v1/sessions",
        data=json.dumps({
            "session_id": sid,
            "title": "Claude Web Session",
            "platform": "claude",
            "model": "claude-3-5-sonnet",
            "messages": [{"role": "user", "content": "Hello from Claude"}],
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req_create, timeout=5) as resp:
        assert resp.status == 201
        created = json.loads(resp.read().decode("utf-8"))
        assert created["session_id"] == sid

    # 3. Verify visible on Arena.AI & ChatGPT filters
    for plat in ("arena", "chatgpt", "claude"):
        with urllib.request.urlopen(f"{gateway_server}/v1/sessions?platform={plat}", timeout=5) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert any(s["session_id"] == sid for s in data["sessions"])

    # 4. Route and interact via POST /v1/sessions/{id}/interact
    with patch(
        "agent.modules.gateway_server.router.complete_text",
        return_value={"text": "Routed response", "provider": "openai", "canonical_model": "gpt-4o"},
    ):
        req_int = urllib.request.Request(
            f"{gateway_server}/v1/sessions/{sid}/interact",
            data=json.dumps({"prompt": "Continue on ChatGPT", "platform": "chatgpt", "model": "gpt-4o"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_int, timeout=5) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            assert res["reply"] == "Routed response"
            assert "chatgpt" in res["session"]["routed_platforms"]

    # 5. Export in Claude format
    with urllib.request.urlopen(f"{gateway_server}/v1/sessions/{sid}/export?platform=claude", timeout=5) as resp:
        assert resp.status == 200
        exp = json.loads(resp.read().decode("utf-8"))
        assert exp["platform"] == "claude"
        assert len(exp["messages"]) >= 2

    # 6. Delete cleanup
    req_del = urllib.request.Request(f"{gateway_server}/v1/sessions/{sid}", method="DELETE")
    with urllib.request.urlopen(req_del, timeout=5) as resp:
        assert resp.status == 200
        assert json.loads(resp.read().decode("utf-8"))["deleted"] is True


def test_plan_3_4_5_files_wiped():
    """Confirm Plan 3.0 / Directive 4.0 / Directive 5.0 artifacts were completely removed."""
    root = Path(__file__).resolve().parent.parent
    removed_paths = [
        root / "architecture" / "WORKFORCE_NETWORK_3_4_5.md",
        root / "agent" / "modules" / "tester_router.py",
        root / "tests" / "test_tester_router.py",
        root / "agent" / "state" / "tester_router_state.json",
    ]
    for p in removed_paths:
        assert not p.exists(), f"Expected Plan 3/4/5 file to be wiped: {p}"


def test_telegram_session_routing_commands(monkeypatch, tmp_path):
    """Verify /sessions, /session, and /route Telegram operator commands."""
    test_db = tmp_path / "tg_sessions.db"
    orig_db = db.DB_PATH
    db.DB_PATH = test_db
    sent = []

    def fake_notify(event, text, level="info", **kwargs):
        sent.append((event, text))

    try:
        db.init_db(test_db)
        SessionRouter().create_session(
            session_id="tg_s1",
            title="Claude TG Session",
            platform="claude",
            model="claude-3-5-sonnet",
            messages=[{"role": "user", "content": "Hello TG"}],
        )
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setattr("agent.modules.telegram_control.notify", fake_notify)

        from agent.modules.telegram_control import ShortsForgeCommandHandler
        handler = ShortsForgeCommandHandler()

        base_msg = {"chat": {"id": "12345"}, "from": {"id": "12345", "username": "pankaj"}}
        assert handler.process_update({"message": {**base_msg, "text": "/sessions"}}) == "SESSIONS_DELIVERED"
        assert "tg_s1" in sent[-1][1]

        assert handler.process_update({"message": {**base_msg, "text": "/session tg_s1"}}) == "SESSION_DELIVERED"
        assert "Hello TG" in sent[-1][1]

        assert handler.process_update({"message": {**base_msg, "text": "/route tg_s1 chatgpt gpt-4o"}}) == "SESSION_ROUTED"
        assert "chatgpt" in sent[-1][1]
    finally:
        db.DB_PATH = orig_db
