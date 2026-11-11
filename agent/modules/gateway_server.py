"""
gateway_server.py — Universal High-Performance AI Gateway & Cross-Model Conversation Hub.

Exposes a local reverse proxy on http://0.0.0.0:8080:
  1. POST /v1/chat/completions — OpenAI-compatible endpoint with automatic Arena/OpenRouter/Groq failover.
  2. POST /v1/messages         — Anthropic-compatible endpoint.
  3. GET  /v1/models           — Live catalog of all 16 available models.
  4. GET  /v1/conversations    — Unified cross-model conversation history.
  5. GET  /health              — Health check and latency metrics.

Maintains persistent conversation history in SQLite, allowing chats from Arena.AI,
Claude, and ChatGPT to be unified, inspected, and controlled from any device (PC, Android, Web).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.modules.capability_registry import UniversalCapabilityRegistry
from agent.modules.omniroute_embedded import EmbeddedOmniRoute

logger = logging.getLogger("GatewayServer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [Gateway] %(message)s")

DB_PATH = PROJECT_ROOT / "agent" / "state" / "unified_conversations.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                model TEXT NOT NULL,
                provider TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                reasoning_effort TEXT,
                latency_ms INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON messages(session_id)")
        conn.commit()


init_db()


def save_message(session_id: str, model: str, provider: str, role: str, content: str, effort: str = None, latency_ms: int = 0):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO messages (session_id, model, provider, role, content, reasoning_effort, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (session_id, model, provider, role, content, effort, latency_ms))
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to persist message to unified db: {e}")


def get_conversation_history(session_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            if session_id:
                cur = conn.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC LIMIT ?", (session_id, limit))
            else:
                cur = conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Failed to query conversation history: {e}")
        return []


router = EmbeddedOmniRoute()


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class GatewayHandler(BaseHTTPRequestHandler):

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With, X-Session-ID")

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {
                "status": "healthy",
                "active_model": UniversalCapabilityRegistry.get_active_model(),
                "active_effort": UniversalCapabilityRegistry.get_active_effort(),
                "available_models": len(UniversalCapabilityRegistry.get_all_models())
            }
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        elif self.path in ("/v1/models", "/models"):
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            models = UniversalCapabilityRegistry.get_all_models()
            data = []
            for m in models:
                data.append({
                    "id": m["canonical_id"],
                    "object": "model",
                    "created": 1700000000,
                    "owned_by": m.get("primary_provider", "arena"),
                    "permission": [],
                    "root": m["canonical_id"],
                    "parent": None
                })
            self.wfile.write(json.dumps({"object": "list", "data": data}).encode("utf-8"))

        elif self.path.startswith("/v1/conversations") or self.path == "/conversations":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            history = get_conversation_history(limit=100)
            self.wfile.write(json.dumps({"conversations": history}).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path in ("/v1/chat/completions", "/chat/completions"):
            self._handle_chat_completions()
        elif self.path in ("/v1/messages", "/messages"):
            self._handle_anthropic_messages()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_chat_completions(self):
        t0 = time.time()
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        req_json = json.loads(body.decode("utf-8"))

        session_id = self.headers.get("X-Session-ID", req_json.get("session_id", "default_session"))
        model = req_json.get("model", UniversalCapabilityRegistry.get_active_model())
        messages = req_json.get("messages", [])
        effort = req_json.get("reasoning_effort", UniversalCapabilityRegistry.get_active_effort())

        # Extract last user message and system prompt
        system_prompt = None
        user_prompt = ""
        for m in messages:
            if m.get("role") == "system":
                system_prompt = m.get("content", "")
            elif m.get("role") == "user":
                user_prompt = m.get("content", "")

        # Save user message to unified conversation DB
        save_message(session_id, model, "client", "user", user_prompt, effort)

        try:
            # Route text through omniroute embedded
            res = router.complete_text(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=model,
                effort=effort
            )
            reply_text = res.get("text", "")
            used_provider = res.get("provider", "unknown")
            used_model = res.get("canonical_model", model)
            latency_ms = int((time.time() - t0) * 1000)

            # Save assistant message to unified DB
            save_message(session_id, used_model, used_provider, "assistant", reply_text, effort, latency_ms)

            # Format standard OpenAI response
            resp_payload = {
                "id": f"chatcmpl-{int(time.time()*1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": used_model,
                "provider": used_provider,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": reply_text
                    },
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": len(user_prompt.split()),
                    "completion_tokens": len(reply_text.split()),
                    "total_tokens": len(user_prompt.split()) + len(reply_text.split())
                }
            }

            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp_payload).encode("utf-8"))

        except Exception as e:
            logger.error(f"Chat completion route failed: {e}")
            self.send_response(500)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            err_payload = {"error": {"message": str(e), "type": "gateway_error", "code": 500}}
            self.wfile.write(json.dumps(err_payload).encode("utf-8"))

    def _handle_anthropic_messages(self):
        t0 = time.time()
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        req_json = json.loads(body.decode("utf-8"))

        session_id = self.headers.get("X-Session-ID", "anthropic_session")
        model = req_json.get("model", "claude-3-5-sonnet")
        messages = req_json.get("messages", [])
        system = req_json.get("system")

        user_content = ""
        for m in messages:
            if m.get("role") == "user":
                user_content = m.get("content", "")

        save_message(session_id, model, "client", "user", user_content)

        try:
            res = router.complete_text(
                prompt=user_content,
                system_prompt=system,
                model=model
            )
            reply_text = res.get("text", "")
            used_provider = res.get("provider", "arena")
            used_model = res.get("canonical_model", model)
            latency_ms = int((time.time() - t0) * 1000)

            save_message(session_id, used_model, used_provider, "assistant", reply_text, latency_ms=latency_ms)

            resp_payload = {
                "id": f"msg_{int(time.time()*1000)}",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": reply_text}],
                "model": used_model,
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": len(user_content.split()), "output_tokens": len(reply_text.split())}
            }

            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp_payload).encode("utf-8"))

        except Exception as e:
            logger.error(f"Anthropic message route failed: {e}")
            self.send_response(500)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"type": "error", "error": {"type": "api_error", "message": str(e)}}).encode("utf-8"))


def run_server(port: int = 8080):
    server_address = ("0.0.0.0", port)
    httpd = ThreadedHTTPServer(server_address, GatewayHandler)
    logger.info(f"⚡ Shorts Forge Universal AI Gateway running at http://0.0.0.0:{port}")
    logger.info("   -> Supports /v1/chat/completions (OpenAI / ChatGPT format)")
    logger.info("   -> Supports /v1/messages (Anthropic / Claude format)")
    logger.info("   -> Supports /v1/models (16 Discovered Frontier Models)")
    logger.info("   -> Supports /v1/conversations (Unified Multi-Model Chat Store)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down Gateway Server.")
        httpd.server_close()


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run_server(p)
