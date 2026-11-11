"""
gateway_server.py — Universal High-Performance AI Gateway & Cross-Model Conversation Hub.

Exposes a local reverse proxy on http://0.0.0.0:8080:
  1. POST /v1/chat/completions — OpenAI-compatible endpoint with automatic Arena/OpenRouter/Groq failover.
  2. POST /v1/messages         — Anthropic-compatible endpoint.
  3. GET  /v1/models           — Live catalog of all 16 available models.
  4. GET  /v1/conversations    — Unified cross-model conversation history & threads.
  5. GET  /health              — Health check and latency metrics.

Maintains persistent conversation history in SQLite via agent.modules.conversation_store,
allowing chats from Arena.AI, Claude, and ChatGPT to be unified, inspected, and continued
from any device without context amnesia.
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

from agent.modules import db
from agent.modules.capability_registry import UniversalCapabilityRegistry
from agent.modules.conversation_store import (
    save_conversation,
    save_message as store_save_message,
    get_conversation,
    get_conversation_messages,
    list_conversations,
    export_conversation_for_provider
)
from agent.modules.omniroute_embedded import EmbeddedOmniRoute

logger = logging.getLogger("GatewayServer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [Gateway] %(message)s")

# Central unified database path from db module
DB_PATH = db.DB_PATH


def init_db():
    """Idempotently initialize unified conversation tables."""
    db.init_db()


init_db()


def save_message(
    session_id: str,
    arg2: str = "",
    arg3: str = "",
    arg4: str = "",
    arg5: str = "",
    effort: Optional[str] = None,
    latency_ms: int = 0,
    thinking_content: Optional[str] = None,
    **kwargs: Any
) -> int:
    """
    Backwards-compatible save_message that bridges both calling signatures:
    Legacy: save_message(session_id, model, provider, role, content, effort=None, latency_ms=0)
    Store:  save_message(conversation_id, role, content, model=None, provider=None, ...)
    """
    try:
        if arg2 in ("user", "assistant", "system", "developer", "tool", "function"):
            role = arg2
            content = arg3
            model = kwargs.get("model", arg4 if arg4 else None)
            provider = kwargs.get("provider", arg5 if arg5 else None)
            th_content = kwargs.get("thinking_content", thinking_content)
        else:
            model = arg2
            provider = arg3
            role = arg4 if arg4 else "user"
            content = arg5
            th_content = thinking_content

        meta = {"effort": effort, "latency_ms": latency_ms}
        if "metadata" in kwargs and isinstance(kwargs["metadata"], dict):
            meta.update(kwargs["metadata"])

        return store_save_message(
            conversation_id=session_id,
            role=role,
            content=content,
            model=model,
            provider=provider,
            thinking_content=th_content,
            metadata=meta
        )
    except Exception as e:
        logger.error(f"Failed to persist message to unified db: {e}")
        return -1


def get_conversation_history(session_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Query messages from the unified store, ensuring session_id alias is present
    for backwards compatibility with existing tests and clients.
    """
    try:
        if session_id:
            rows = db.get_messages_by_conversation(session_id, limit=limit, order="ASC")
        else:
            with db.get_db() as conn:
                cur = conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))
                rows = [dict(r) for r in cur.fetchall()]

        for r in rows:
            r["session_id"] = r.get("conversation_id", session_id)
            if isinstance(r.get("metadata"), str):
                try:
                    r["metadata"] = json.loads(r["metadata"])
                except Exception:
                    r["metadata"] = {}
        return rows
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

            parts = self.path.strip("/").split("/")
            if len(parts) >= 3 and parts[0] == "v1" and parts[1] == "conversations":
                conv_id = parts[2]
                conv = get_conversation(conv_id)
                if not conv:
                    self.wfile.write(json.dumps({"error": "Conversation not found"}).encode("utf-8"))
                else:
                    msgs = get_conversation_messages(conv_id, target_format="canonical")
                    self.wfile.write(json.dumps({"conversation": conv, "messages": msgs}).encode("utf-8"))
            else:
                history = get_conversation_history(limit=100)
                threads = list_conversations(limit=50)
                self.wfile.write(json.dumps({"conversations": history, "threads": threads}).encode("utf-8"))

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

    def _sync_inbound_messages(self, session_id: str, messages: List[Dict[str, Any]], model: str):
        """Synchronize incoming messages into the unified conversation store without duplicating prior turns."""
        existing_msgs = db.get_messages_by_conversation(session_id, order="ASC")
        existing_count = len(existing_msgs)

        # If conversation is empty in store, ingest all incoming messages
        if existing_count == 0:
            for m in messages:
                role = m.get("role", "user")
                content = m.get("content", "")
                if isinstance(content, list):
                    text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                    content = "".join(text_parts)
                save_message(session_id, model, "client", role, str(content))
        else:
            # If client sends full history that has more turns than our store, append only new turns
            if len(messages) > existing_count:
                new_turns = messages[existing_count:]
                for m in new_turns:
                    role = m.get("role", "user")
                    content = m.get("content", "")
                    if isinstance(content, list):
                        text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                        content = "".join(text_parts)
                    save_message(session_id, model, "client", role, str(content))
            elif len(messages) == 1:
                # Client sent just the latest message
                m = messages[0]
                save_message(session_id, model, "client", m.get("role", "user"), str(m.get("content", "")))

    def _handle_chat_completions(self):
        t0 = time.time()
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        req_json = json.loads(body.decode("utf-8"))

        session_id = self.headers.get("X-Session-ID", req_json.get("session_id", "default_session"))
        model = req_json.get("model", UniversalCapabilityRegistry.get_active_model())
        messages = req_json.get("messages", [])
        effort = req_json.get("reasoning_effort", UniversalCapabilityRegistry.get_active_effort())

        # Sync full dialogue into unified conversation store
        self._sync_inbound_messages(session_id, messages, model)

        # Export multi-turn conversation from unified store
        export_payload = export_conversation_for_provider(session_id, provider="openai", model=model)
        exported_messages = export_payload.get("messages", [])

        # Extract system prompt and construct full conversational prompt
        system_prompt = None
        dialogue_turns = []
        for m in exported_messages:
            r = m.get("role")
            c = m.get("content", "")
            if r in ("system", "developer"):
                system_prompt = c
            elif r in ("user", "assistant"):
                dialogue_turns.append(f"{r.capitalize()}: {c}")

        if len(dialogue_turns) > 1:
            full_prompt = "\n\n".join(dialogue_turns)
        elif dialogue_turns:
            full_prompt = dialogue_turns[0].split(": ", 1)[-1]
        else:
            full_prompt = ""

        try:
            res = router.complete_text(
                prompt=full_prompt,
                system_prompt=system_prompt,
                model=model,
                effort=effort
            )
            reply_text = res.get("text", "")
            used_provider = res.get("provider", "unknown")
            used_model = res.get("canonical_model", model)
            latency_ms = int((time.time() - t0) * 1000)

            # Persist assistant reply to unified store
            save_message(
                session_id,
                used_model,
                used_provider,
                "assistant",
                reply_text,
                effort=effort,
                latency_ms=latency_ms
            )

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
                    "prompt_tokens": len(full_prompt.split()),
                    "completion_tokens": len(reply_text.split()),
                    "total_tokens": len(full_prompt.split()) + len(reply_text.split())
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

        if system:
            db.create_message(session_id, role="system", content=system)

        self._sync_inbound_messages(session_id, messages, model)

        export_payload = export_conversation_for_provider(session_id, provider="anthropic", model=model)
        exported_messages = export_payload.get("messages", [])
        sys_prompt = export_payload.get("system", system)

        dialogue_turns = []
        for m in exported_messages:
            r = m.get("role")
            c = m.get("content", "")
            if isinstance(c, list):
                text_parts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
                c = "".join(text_parts)
            dialogue_turns.append(f"{r.capitalize()}: {c}")

        if len(dialogue_turns) > 1:
            full_prompt = "\n\n".join(dialogue_turns)
        elif dialogue_turns:
            full_prompt = dialogue_turns[0].split(": ", 1)[-1]
        else:
            full_prompt = ""

        try:
            res = router.complete_text(
                prompt=full_prompt,
                system_prompt=sys_prompt,
                model=model
            )
            reply_text = res.get("text", "")
            used_provider = res.get("provider", "arena")
            used_model = res.get("canonical_model", model)
            latency_ms = int((time.time() - t0) * 1000)

            save_message(
                session_id,
                used_model,
                used_provider,
                "assistant",
                reply_text,
                latency_ms=latency_ms
            )

            resp_payload = {
                "id": f"msg_{int(time.time()*1000)}",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": reply_text}],
                "model": used_model,
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": len(full_prompt.split()), "output_tokens": len(reply_text.split())}
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
