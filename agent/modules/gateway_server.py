"""
gateway_server.py — Universal High-Performance AI Gateway & Cross-Platform Sessions Hub.

Exposes a local reverse proxy & interactive web console on http://0.0.0.0:8080:
  1. GET  / & GET /sessions         — Interactive Cross-Platform Sessions Router UI.
  2. POST /v1/chat/completions      — OpenAI-compatible endpoint with automatic failover & history sync.
  3. POST /v1/messages              — Anthropic-compatible endpoint with history sync.
  4. GET  /v1/models                — Live catalog of all discovered frontier models.
  5. GET  /v1/conversations         — Unified cross-model conversation history & threads.
  6. /v1/sessions & /v1/sessions/*  — Manage, route, export, and interact with sessions across
                                      Claude, Arena.AI, ChatGPT, OpenRouter, Groq, and OmniRoute.
  7. GET  /health                   — Health check and latency metrics.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.modules import db
from agent.modules.capability_registry import UniversalCapabilityRegistry
from agent.modules.conversation_store import (
    export_conversation_for_provider,
    get_conversation,
    get_conversation_messages,
    list_conversations,
    save_message as store_save_message,
)
from agent.modules.omniroute_embedded import EmbeddedOmniRoute
from agent.modules.session_router import (
    SessionRouter,
    import_legacy_unified_db,
    infer_platform_from_model,
    normalize_platform,
    to_upstream_provider,
)
from agent.modules.sessions_ui import SESSIONS_DASHBOARD_HTML

logger = logging.getLogger("GatewayServer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [Gateway] %(message)s")

DB_PATH = db.DB_PATH


def init_db() -> None:
    """Idempotently initialize unified conversation tables and import legacy sessions."""
    db.init_db()
    import_legacy_unified_db()


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
    **kwargs: Any,
) -> int:
    """
    Backwards-compatible save_message supporting both calling signatures:
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

        meta: Dict[str, Any] = {"effort": effort, "latency_ms": latency_ms}
        if "metadata" in kwargs and isinstance(kwargs["metadata"], dict):
            meta.update(kwargs["metadata"])

        return store_save_message(
            conversation_id=session_id,
            role=role,
            content=content,
            model=model,
            provider=provider,
            thinking_content=th_content,
            metadata=meta,
        )
    except Exception as e:
        logger.error(f"Failed to persist message to unified db: {e}")
        return -1


def get_conversation_history(session_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Query messages from the unified store with session_id compatibility alias."""
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

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, X-Requested-With, X-Session-ID, X-Conversation-ID, X-Platform",
        )

    def _send_json(self, status_code: int, payload: Any) -> None:
        self.send_response(status_code)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def _send_html(self, status_code: int, html: str) -> None:
        self.send_response(status_code)
        self._send_cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        body = self.rfile.read(length)
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    def _resolve_session_id(self, req_json: Dict[str, Any], default: str) -> str:
        """Resolve conversation/session ID cleanly from headers or JSON body."""
        return (
            self.headers.get("X-Session-ID")
            or self.headers.get("X-Conversation-ID")
            or req_json.get("session_id")
            or req_json.get("conversation_id")
            or default
        )

    def _session_router(self) -> SessionRouter:
        return SessionRouter(router=router)

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/sessions", "/dashboard"):
            self._send_html(200, SESSIONS_DASHBOARD_HTML)

        elif path == "/health":
            self._send_json(200, {
                "status": "healthy",
                "active_model": UniversalCapabilityRegistry.get_active_model(),
                "active_effort": UniversalCapabilityRegistry.get_active_effort(),
                "available_models": len(UniversalCapabilityRegistry.get_all_models()),
                "sessions_router": True,
            })

        elif path in ("/v1/models", "/models"):
            models = UniversalCapabilityRegistry.get_all_models()
            data = [
                {
                    "id": m["canonical_id"],
                    "object": "model",
                    "created": 1700000000,
                    "owned_by": m.get("primary_provider", "arena"),
                    "permission": [],
                    "root": m["canonical_id"],
                    "parent": None,
                }
                for m in models
            ]
            self._send_json(200, {"object": "list", "data": data})

        elif path == "/v1/sessions" or path == "/sessions/api":
            sr = self._session_router()
            platform = qs.get("platform", [None])[0]
            origin_only = qs.get("origin_only", ["false"])[0].lower() == "true"
            query = qs.get("q", [None])[0]
            limit = int(qs.get("limit", [50])[0])
            offset = int(qs.get("offset", [0])[0])
            sessions = sr.list_sessions(
                platform=platform, origin_only=origin_only, query=query, limit=limit, offset=offset
            )
            self._send_json(200, {"sessions": sessions, "count": len(sessions), "platform_filter": platform})

        elif path.startswith("/v1/sessions/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3:
                sid = parts[2]
                session = self._session_router().get_session(sid)
                if not session:
                    self._send_json(404, {"error": "Session not found"})
                else:
                    self._send_json(200, session)
            elif len(parts) == 4 and parts[3] == "export":
                sid = parts[2]
                platform = qs.get("platform", ["chatgpt"])[0]
                model = qs.get("model", [None])[0]
                try:
                    exported = self._session_router().export_session(sid, target_platform=platform, target_model=model)
                    self._send_json(200, exported)
                except KeyError:
                    self._send_json(404, {"error": "Session not found"})
            else:
                self._send_json(404, {"error": "Unknown session endpoint"})

        elif path.startswith("/v1/conversations") or path == "/conversations":
            parts = path.strip("/").split("/")
            if len(parts) >= 3 and parts[0] == "v1" and parts[1] == "conversations":
                conv_id = parts[2]
                conv = get_conversation(conv_id)
                if not conv:
                    self._send_json(200, {"error": "Conversation not found"})
                else:
                    msgs = get_conversation_messages(conv_id, target_format="canonical")
                    self._send_json(200, {"conversation": conv, "messages": msgs})
            else:
                history = get_conversation_history(limit=100)
                threads = list_conversations(limit=50)
                self._send_json(200, {"conversations": history, "threads": threads})

        else:
            self.send_response(404)
            self._send_cors_headers()
            self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/v1/chat/completions", "/chat/completions"):
            self._handle_chat_completions()
        elif path in ("/v1/messages", "/messages"):
            self._handle_anthropic_messages()
        elif path == "/v1/sessions":
            self._handle_create_session()
        elif path.startswith("/v1/sessions/"):
            self._handle_session_action(path)
        else:
            self.send_response(404)
            self._send_cors_headers()
            self.end_headers()

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "v1" and parts[1] == "sessions":
            sid = parts[2]
            try:
                req = self._read_json_body()
                sr = self._session_router()
                if not sr.get_session(sid):
                    self._send_json(404, {"error": "Session not found"})
                    return
                if "title" in req:
                    sr.rename_session(sid, req["title"])
                if "platform" in req or "model" in req:
                    sr.route_session(sid, req.get("platform") or "arena", target_model=req.get("model"))
                self._send_json(200, sr.get_session(sid))
            except Exception as e:
                self._send_json(400, {"error": str(e)})
        else:
            self._send_json(404, {"error": "Not found"})

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] in ("v1",) and parts[1] in ("sessions", "conversations"):
            sid = parts[2]
            deleted = self._session_router().delete_session(sid)
            if not deleted:
                self._send_json(404, {"error": "Session not found"})
            else:
                self._send_json(200, {"deleted": True, "session_id": sid})
        else:
            self._send_json(404, {"error": "Not found"})

    def _handle_create_session(self) -> None:
        try:
            req = self._read_json_body()
            sr = self._session_router()
            session = sr.create_session(
                session_id=req.get("session_id") or req.get("conversation_id"),
                title=req.get("title"),
                platform=req.get("platform") or req.get("provider") or "claude",
                model=req.get("model"),
                messages=req.get("messages"),
                visible_on_platforms=req.get("visible_on_platforms"),
                metadata=req.get("metadata"),
            )
            self._send_json(201, session)
        except Exception as e:
            self._send_json(400, {"error": str(e)})

    def _handle_session_action(self, path: str) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 4:
            self._send_json(404, {"error": "Unknown session action"})
            return
        sid, action = parts[2], parts[3]
        sr = self._session_router()
        try:
            req = self._read_json_body()
            if action == "route":
                plat = req.get("platform") or req.get("provider") or "arena"
                res = sr.route_session(sid, target_platform=plat, target_model=req.get("model"))
                self._send_json(200, res)
            elif action == "interact":
                prompt = req.get("prompt", "")
                if not prompt and isinstance(req.get("messages"), list) and req["messages"]:
                    prompt = str(req["messages"][-1].get("content", ""))
                res = sr.interact(
                    session_id=sid,
                    prompt=prompt,
                    target_platform=req.get("platform") or req.get("provider"),
                    target_model=req.get("model"),
                    effort=req.get("effort") or req.get("reasoning_effort"),
                    system_prompt=req.get("system"),
                )
                self._send_json(200, res)
            elif action == "link":
                plat = req.get("platform") or req.get("provider")
                if not plat:
                    self._send_json(400, {"error": "platform is required"})
                    return
                res = sr.link_platform(sid, plat)
                self._send_json(200, res)
            else:
                self._send_json(404, {"error": f"Unknown action '{action}'"})
        except KeyError:
            self._send_json(404, {"error": "Session not found"})
        except Exception as e:
            logger.error(f"Session action '{action}' failed: {e}")
            self._send_json(500, {"error": {"message": str(e), "type": "session_router_error"}})

    def _sync_inbound_messages(self, session_id: str, messages: List[Dict[str, Any]], model: str) -> None:
        """Synchronize incoming messages into the unified conversation store without duplicating prior turns."""
        existing_msgs = db.get_messages_by_conversation(session_id, order="ASC")
        existing_count = len(existing_msgs)

        def _flat_content(c: Any) -> str:
            if isinstance(c, list):
                return "".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
            return str(c or "")

        if existing_count == 0:
            for m in messages:
                save_message(session_id, model, "client", m.get("role", "user"), _flat_content(m.get("content", "")))
        elif len(messages) > existing_count:
            for m in messages[existing_count:]:
                save_message(session_id, model, "client", m.get("role", "user"), _flat_content(m.get("content", "")))
        elif len(messages) == 1:
            m = messages[0]
            save_message(session_id, model, "client", m.get("role", "user"), _flat_content(m.get("content", "")))

    def _mark_platform_links(self, session_id: str, client_platform: Optional[str], used_provider: str, used_model: str) -> None:
        """Keep session metadata updated so any turn links all participating platforms."""
        sr = self._session_router()
        conv = db.get_conversation(session_id)
        if not conv:
            return
        meta = dict(conv.get("metadata") or {})
        routed = list(meta.get("routed_platforms") or [])
        for raw_p in (client_platform, used_provider, infer_platform_from_model(used_model)):
            if raw_p and raw_p != "client":
                np = normalize_platform(raw_p)
                if np not in routed:
                    routed.append(np)
        if routed:
            meta["routed_platforms"] = routed
            meta.setdefault("origin_platform", routed[0])
        db.update_conversation(session_id, default_provider=used_provider, default_model=used_model, metadata=meta)
        _ = sr  # keeps reference clarity

    def _handle_chat_completions(self) -> None:
        t0 = time.time()
        req_json = self._read_json_body()

        session_id = self._resolve_session_id(req_json, "default_session")
        model = req_json.get("model", UniversalCapabilityRegistry.get_active_model())
        messages = req_json.get("messages", [])
        effort = req_json.get("reasoning_effort", UniversalCapabilityRegistry.get_active_effort())
        platform_hint = self.headers.get("X-Platform") or req_json.get("platform") or req_json.get("provider")

        self._sync_inbound_messages(session_id, messages, model)

        target_prov = to_upstream_provider(platform_hint) if platform_hint else "openai"
        export_payload = export_conversation_for_provider(session_id, provider=target_prov, model=model)
        exported_messages = export_payload.get("messages", [])

        system_prompt = None
        dialogue_turns: List[str] = []
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

        provider_pref = None
        if platform_hint:
            pref = to_upstream_provider(platform_hint)
            provider_pref = [pref] + [p for p in ("arena", "openai", "anthropic", "openrouter", "groq", "pollinations") if p != pref]

        try:
            call_kwargs: Dict[str, Any] = {
                "prompt": full_prompt,
                "system_prompt": system_prompt,
                "model": model,
                "effort": effort,
            }
            if provider_pref:
                call_kwargs["provider_preference"] = provider_pref
            res = router.complete_text(**call_kwargs)
            reply_text = res.get("text", "")
            used_provider = res.get("provider", "unknown")
            used_model = res.get("canonical_model", model)
            latency_ms = int((time.time() - t0) * 1000)

            save_message(
                session_id,
                used_model,
                used_provider,
                "assistant",
                reply_text,
                effort=effort,
                latency_ms=latency_ms,
            )
            self._mark_platform_links(session_id, platform_hint, used_provider, used_model)

            resp_payload = {
                "id": f"chatcmpl-{int(time.time() * 1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "session_id": session_id,
                "conversation_id": session_id,
                "model": used_model,
                "provider": used_provider,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": reply_text},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": len(full_prompt.split()),
                    "completion_tokens": len(reply_text.split()),
                    "total_tokens": len(full_prompt.split()) + len(reply_text.split()),
                },
            }
            self._send_json(200, resp_payload)

        except Exception as e:
            logger.error(f"Chat completion route failed: {e}")
            self._send_json(500, {"error": {"message": str(e), "type": "gateway_error", "code": 500}})

    def _handle_anthropic_messages(self) -> None:
        t0 = time.time()
        req_json = self._read_json_body()

        session_id = self._resolve_session_id(req_json, "anthropic_session")
        model = req_json.get("model", "claude-3-5-sonnet")
        messages = req_json.get("messages", [])
        system = req_json.get("system")
        platform_hint = self.headers.get("X-Platform") or req_json.get("platform") or "claude"

        if system:
            db.create_message(session_id, role="system", content=system, provider="anthropic", model=model)

        self._sync_inbound_messages(session_id, messages, model)

        export_payload = export_conversation_for_provider(session_id, provider="anthropic", model=model)
        exported_messages = export_payload.get("messages", [])
        sys_prompt = export_payload.get("system", system)

        dialogue_turns: List[str] = []
        for m in exported_messages:
            r = m.get("role")
            c = m.get("content", "")
            if isinstance(c, list):
                c = "".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
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
                model=model,
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
                latency_ms=latency_ms,
            )
            self._mark_platform_links(session_id, platform_hint, used_provider, used_model)

            resp_payload = {
                "id": f"msg_{int(time.time() * 1000)}",
                "type": "message",
                "role": "assistant",
                "session_id": session_id,
                "conversation_id": session_id,
                "content": [{"type": "text", "text": reply_text}],
                "model": used_model,
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": len(full_prompt.split()), "output_tokens": len(reply_text.split())},
            }
            self._send_json(200, resp_payload)

        except Exception as e:
            logger.error(f"Anthropic message route failed: {e}")
            self._send_json(500, {"type": "error", "error": {"type": "api_error", "message": str(e)}})


def run_server(port: int = 8080) -> None:
    server_address = ("0.0.0.0", port)
    httpd = ThreadedHTTPServer(server_address, GatewayHandler)
    logger.info(f"⚡ Shorts Forge Universal AI Gateway running at http://0.0.0.0:{port}")
    logger.info("   -> Interactive Sessions UI: http://localhost:%d/sessions", port)
    logger.info("   -> Sessions API: /v1/sessions (manage, route & interact across Claude/Arena/ChatGPT)")
    logger.info("   -> OpenAI API:   /v1/chat/completions")
    logger.info("   -> Claude API:   /v1/messages")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down Gateway Server.")
        httpd.server_close()


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run_server(p)
