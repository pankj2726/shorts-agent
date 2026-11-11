"""
conftest.py — Master Fixture Hub for E2E Test Suite.

Provides:
  - `zero_cost_guard` fixture override (allows testing frontier/paid models).
  - `e2e_db` fixture (isolated SQLite database for conversations & messages).
  - `mock_gateway_server` fixture (in-process mock HTTP/SSE gateway daemon on port 20129/ephemeral).
  - SSE stream parsing helpers (`parse_sse_events`, `parse_anthropic_sse`, `parse_openai_sse`, `extract_sse_text`).
  - Pre-configured HTTP client sessions (`e2e_client`).
"""

from __future__ import annotations

import http.server
import json
import logging
import os
from pathlib import Path
import re
import socket
import sqlite3
import sys
import threading
import time
from typing import Any, Dict, Generator, List, Optional, Tuple
import urllib.parse
import uuid

import pytest
import requests

# ─── Ensure Project Root in sys.path ──────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("e2e_conftest")


# ─── Fixture Override: Zero-Cost Guard ────────────────────────────────────────

@pytest.fixture(autouse=True)
def zero_cost_guard():
    """
    Overrides the legacy video pipeline zero_cost_guard fixture so that
    E2E testing of frontier and paid models (claude-3-5-sonnet, gpt-4o,
    deepseek-v3, mistral-large-2411, etc.) is fully permitted without raising
    ZeroCostViolationError.
    """
    yield


# ─── Isolated SQLite Database Engine ─────────────────────────────────────────

class E2ETestDB:
    """
    Isolated SQLite database manager implementing the unified cross-model
    conversations and messages schema.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_schema()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                timeout=15.0,
                check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _init_schema(self) -> None:
        """Create conversations and messages tables and indexes."""
        with self.conn as cur:
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id          TEXT PRIMARY KEY,
                    title       TEXT,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata    TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id              TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role            TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    model           TEXT NOT NULL,
                    provider        TEXT NOT NULL,
                    thinking        TEXT,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    latency_ms      INTEGER DEFAULT 0,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata        TEXT DEFAULT '{}',
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conv_id ON messages(conversation_id);
                CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
                CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at);
            """)

    def create_conversation(
        self,
        conversation_id: Optional[str] = None,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Create or update a conversation thread without clobbering existing titles."""
        cid = conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
        meta_str = json.dumps(metadata or {})
        default_title = title or f"Conversation {cid[:8]}"
        with self.conn as cur:
            if title is not None:
                cur.execute("""
                    INSERT INTO conversations (id, title, metadata, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        metadata = CASE WHEN ? != '{}' THEN excluded.metadata ELSE conversations.metadata END,
                        updated_at = CURRENT_TIMESTAMP
                """, (cid, title, meta_str, meta_str))
            else:
                cur.execute("""
                    INSERT INTO conversations (id, title, metadata, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                """, (cid, default_title, meta_str))
        return cid

    def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        model: str,
        provider: str,
        thinking: Optional[str] = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
        message_id: Optional[str] = None
    ) -> str:
        """Save a message into the conversation thread, auto-creating conversation if missing."""
        # Ensure conversation exists
        self.create_conversation(conversation_id=conversation_id)
        mid = message_id or f"msg_{uuid.uuid4().hex[:12]}"
        meta_str = json.dumps(metadata or {})
        with self.conn as cur:
            cur.execute("""
                INSERT INTO messages (
                    id, conversation_id, role, content, model, provider,
                    thinking, tokens_in, tokens_out, latency_ms, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (mid, conversation_id, role, content, model, provider,
                  thinking, tokens_in, tokens_out, latency_ms, meta_str))
            # Update parent conversation timestamp
            cur.execute("""
                UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """, (conversation_id,))
        return mid

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve conversation metadata."""
        cur = self.conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        row = cur.fetchone()
        if not row:
            return None
        res = dict(row)
        try:
            res["metadata"] = json.loads(res.get("metadata") or "{}")
        except Exception:
            pass
        return res

    def get_conversation_messages(
        self,
        conversation_id: str,
        format: str = "raw"
    ) -> List[Dict[str, Any]]:
        """
        Retrieve messages for a conversation formatted for raw, Anthropic, or OpenAI.
        """
        cur = self.conn.execute("""
            SELECT * FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC, rowid ASC
        """, (conversation_id,))
        rows = [dict(r) for r in cur.fetchall()]

        if format == "raw":
            return rows

        if format == "anthropic":
            # Map into Anthropic role/content format
            messages = []
            for r in rows:
                role = "assistant" if r["role"] in ("assistant", "model") else "user"
                content_blocks = []
                if r.get("thinking"):
                    content_blocks.append({"type": "thinking", "thinking": r["thinking"]})
                content_blocks.append({"type": "text", "text": r["content"]})
                messages.append({"role": role, "content": content_blocks})
            return messages

        if format == "openai":
            # Map into OpenAI role/content format
            messages = []
            for r in rows:
                role = r["role"]
                if role not in ("system", "user", "assistant", "developer"):
                    role = "assistant" if role == "model" else "user"
                messages.append({"role": role, "content": r["content"]})
            return messages

        return rows

    def list_conversations(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List active conversation threads."""
        cur = self.conn.execute("""
            SELECT c.*, COUNT(m.id) as message_count
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            LIMIT ?
        """, (limit,))
        return [dict(r) for r in cur.fetchall()]

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def fetch_all(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        cur = self.conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def fetch_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    def clear(self) -> None:
        with self.conn as cur:
            cur.execute("DELETE FROM messages")
            cur.execute("DELETE FROM conversations")

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


@pytest.fixture
def e2e_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[E2ETestDB, None, None]:
    """
    Provides an isolated SQLite database instance with conversations and messages schema.
    Also monkeypatches agent.modules.db.DB_PATH and gateway_server.DB_PATH if present.
    """
    db_file = tmp_path / "e2e_conversations.db"
    db_instance = E2ETestDB(db_file)

    # Patch known references in modules
    try:
        import agent.modules.db as app_db
        monkeypatch.setattr(app_db, "DB_PATH", db_file, raising=False)
    except Exception:
        pass

    try:
        import agent.modules.gateway_server as app_gateway
        monkeypatch.setattr(app_gateway, "DB_PATH", db_file, raising=False)
    except Exception:
        pass

    yield db_instance
    db_instance.close()


# ─── SSE Stream Parser Helpers ────────────────────────────────────────────────

def parse_sse_events(response: requests.Response) -> Generator[Dict[str, Any], None, None]:
    """
    Parses an SSE HTTP response stream synchronously.
    Yields event dicts with keys: 'event', 'data' (decoded JSON or string), and 'raw'.
    Handles data: [DONE] termination cleanly.
    """
    current_event = "message"
    current_data_lines: List[str] = []
    current_id: Optional[str] = None

    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.strip()

        # Empty line denotes event boundary in SSE specification
        if not line:
            if current_data_lines:
                data_str = "\n".join(current_data_lines)
                if data_str == "[DONE]":
                    yield {
                        "event": "done",
                        "data": "[DONE]",
                        "id": current_id,
                        "raw": data_str
                    }
                else:
                    try:
                        parsed_json = json.loads(data_str)
                        yield {
                            "event": current_event,
                            "data": parsed_json,
                            "id": current_id,
                            "raw": data_str
                        }
                    except json.JSONDecodeError:
                        yield {
                            "event": current_event,
                            "data": data_str,
                            "id": current_id,
                            "raw": data_str
                        }
                # Reset event buffer
                current_event = "message"
                current_data_lines = []
                current_id = None
            continue

        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            current_data_lines.append(line[5:].strip())
        elif line.startswith("id:"):
            current_id = line[3:].strip()
        elif line.startswith(":"):
            # SSE comment line; ignore
            continue

    # Flush any remaining buffer at end of stream
    if current_data_lines:
        data_str = "\n".join(current_data_lines)
        if data_str == "[DONE]":
            yield {"event": "done", "data": "[DONE]", "id": current_id, "raw": data_str}
        else:
            try:
                yield {"event": current_event, "data": json.loads(data_str), "id": current_id, "raw": data_str}
            except json.JSONDecodeError:
                yield {"event": current_event, "data": data_str, "id": current_id, "raw": data_str}


def parse_anthropic_sse(response: requests.Response) -> Generator[Dict[str, Any], None, None]:
    """
    Consumes an Anthropic `/v1/messages` SSE stream and yields JSON data payloads
    for events like message_start, content_block_delta, message_delta, etc.
    Stops cleanly when `message_stop` is reached.
    """
    for item in parse_sse_events(response):
        if isinstance(item["data"], dict):
            yield item["data"]
            if item["data"].get("type") == "message_stop":
                break


def parse_openai_sse(response: requests.Response) -> Generator[Dict[str, Any], None, None]:
    """
    Consumes an OpenAI `/v1/chat/completions` SSE stream and yields decoded chunk
    payloads until [DONE].
    """
    for item in parse_sse_events(response):
        if item.get("data") == "[DONE]":
            break
        if isinstance(item["data"], dict):
            yield item["data"]


def extract_sse_text(response: requests.Response) -> str:
    """
    Helper to consume an entire SSE stream and concatenate all content deltas
    (supporting both Anthropic delta.text and OpenAI delta.content).
    """
    chunks: List[str] = []
    for item in parse_sse_events(response):
        data = item.get("data")
        if data == "[DONE]":
            break
        if isinstance(data, dict):
            # Anthropic text_delta
            if data.get("type") == "content_block_delta":
                delta = data.get("delta", {})
                if delta.get("type") == "text_delta":
                    chunks.append(delta.get("text", ""))
            elif data.get("type") == "message_stop":
                break
            # OpenAI chunk delta
            elif "choices" in data and len(data["choices"]) > 0:
                choice = data["choices"][0]
                delta = choice.get("delta", {})
                if "content" in delta and delta["content"]:
                    chunks.append(delta["content"])
                if choice.get("finish_reason") in ("stop", "end_turn"):
                    break
    return "".join(chunks)


# ─── In-Process Mock HTTP/SSE Gateway Server ──────────────────────────────────

class MockGatewayState:
    """Shared state for the Mock Gateway Server."""

    def __init__(self, db: Optional[E2ETestDB] = None):
        self.db = db
        self.recorded_requests: List[Dict[str, Any]] = []
        self.mock_responses: Dict[str, Tuple[int, Any, Dict[str, str]]] = {}
        self.custom_stream_generators: Dict[str, Any] = {}
        self.lock = threading.Lock()
        self.rate_limit_headers = {
            "anthropic-ratelimit-requests-limit": "10000",
            "anthropic-ratelimit-requests-remaining": "9999",
            "anthropic-ratelimit-tokens-limit": "2000000",
            "anthropic-ratelimit-tokens-remaining": "1995000",
        }

    def record(self, method: str, path: str, headers: Dict[str, str], body: Any):
        with self.lock:
            self.recorded_requests.append({
                "method": method,
                "path": path,
                "headers": headers,
                "body": body,
                "timestamp": time.time(),
            })

    def set_mock_response(
        self,
        path: str,
        status_code: int = 200,
        body: Any = None,
        headers: Optional[Dict[str, str]] = None
    ):
        with self.lock:
            self.mock_responses[path] = (status_code, body or {}, headers or {})

    def clear_mocks(self):
        with self.lock:
            self.mock_responses.clear()
            self.custom_stream_generators.clear()
            self.recorded_requests.clear()


class MockGatewayHandler(http.server.BaseHTTPRequestHandler):
    """
    HTTP Request Handler emulating Gateway Server endpoints:
      - /v1/messages (Anthropic)
      - /v1/chat/completions (OpenAI)
      - /v1/models (Catalog)
      - /v1/conversations (History)
      - /health
      - OPTIONS (CORS preflight)
    """

    state: MockGatewayState  # Assigned on server creation

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress standard logging during tests unless DEBUG is set
        if os.environ.get("GATEWAY_TEST_DEBUG"):
            super().log_message(format, *args)

    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, x-api-key, anthropic-version, anthropic-workspace-id"
        )
        self.send_header("Access-Control-Max-Age", "86400")

    def _read_json_body(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            raw = self.rfile.read(content_length).decode("utf-8")
            try:
                return json.loads(raw)
            except Exception:
                return {"_raw": raw}
        return {}

    def do_OPTIONS(self):
        self.state.record("OPTIONS", self.path, dict(self.headers), None)
        self.send_response(200)
        self._send_cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        self.state.record("GET", self.path, dict(self.headers), None)

        # Check explicit mock override
        if path in self.state.mock_responses:
            status, body, headers = self.state.mock_responses[path]
            self.send_response(status)
            self._send_cors()
            for k, v in headers.items():
                self.send_header(k, v)
            if isinstance(body, (dict, list)):
                self.send_header("Content-Type", "application/json")
                out = json.dumps(body).encode("utf-8")
            else:
                out = str(body).encode("utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return

        if path == "/health":
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "application/json")
            out = json.dumps({
                "status": "ok",
                "service": "mock_gateway_server",
                "version": "1.0.0"
            }).encode("utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return

        if path == "/v1/models":
            models_list = [
                {"id": "claude-3-5-haiku", "object": "model", "owned_by": "anthropic"},
                {"id": "claude-3-5-sonnet", "object": "model", "owned_by": "anthropic"},
                {"id": "mistral-large-2411", "object": "model", "owned_by": "mistral"},
                {"id": "deepseek-v3", "object": "model", "owned_by": "deepseek"},
                {"id": "llama-3.1-405b", "object": "model", "owned_by": "meta"},
                {"id": "grok-2", "object": "model", "owned_by": "xai"},
                {"id": "gemini-1.5-pro", "object": "model", "owned_by": "google"},
                {"id": "claude-coding-router-preview", "object": "model", "owned_by": "arena"},
                {"id": "coding-router-preview", "object": "model", "owned_by": "arena"},
                {"id": "claude-fable-5-1", "object": "model", "owned_by": "arena"},
                {"id": "gpt-4o", "object": "model", "owned_by": "openai"},
            ]
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "application/json")
            out = json.dumps({"object": "list", "data": models_list}).encode("utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return

        if path == "/v1/conversations":
            convs = []
            if self.state.db:
                convs = self.state.db.list_conversations()
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "application/json")
            out = json.dumps({"object": "list", "data": convs}).encode("utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return

        if path.startswith("/v1/conversations/"):
            cid = path.split("/v1/conversations/")[1].split("/")[0]
            fmt = urllib.parse.parse_qs(parsed.query).get("format", ["raw"])[0]
            msgs = []
            if self.state.db:
                msgs = self.state.db.get_conversation_messages(cid, format=fmt)
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "application/json")
            out = json.dumps({"conversation_id": cid, "messages": msgs}).encode("utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return

        self.send_response(404)
        self._send_cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()
        self.state.record("POST", self.path, dict(self.headers), body)

        # Check explicit mock override
        if path in self.state.mock_responses:
            status, resp_body, headers = self.state.mock_responses[path]
            self.send_response(status)
            self._send_cors()
            for k, v in headers.items():
                self.send_header(k, v)
            if isinstance(resp_body, (dict, list)):
                self.send_header("Content-Type", "application/json")
                out = json.dumps(resp_body).encode("utf-8")
            else:
                out = str(resp_body).encode("utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return

        model = body.get("model", "default-model")
        stream = body.get("stream", False)
        conversation_id = body.get("conversation_id") or self.headers.get("x-conversation-id") or f"conv_{uuid.uuid4().hex[:8]}"

        # ─── Anthropic /v1/messages ──────────────────────────────────────────
        if path == "/v1/messages":
            self.send_response(200)
            self._send_cors()

            # Inject synthetic unbottlenecked rate limit headers
            for k, v in self.state.rate_limit_headers.items():
                self.send_header(k, v)

            msg_id = f"msg_{uuid.uuid4().hex[:14]}"
            reply_text = f"Mock response from {model}"

            # Save to isolated DB if available
            if self.state.db:
                # Save user turn if present
                in_msgs = body.get("messages", [])
                if in_msgs and isinstance(in_msgs, list):
                    last_msg = in_msgs[-1]
                    user_content = last_msg.get("content", "")
                    if isinstance(user_content, list):
                        user_content = " ".join(b.get("text", "") for b in user_content if isinstance(b, dict))
                    self.state.db.save_message(
                        conversation_id=conversation_id,
                        role="user",
                        content=str(user_content),
                        model=model,
                        provider="client"
                    )
                # Save assistant turn
                self.state.db.save_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=reply_text,
                    model=model,
                    provider="anthropic",
                    tokens_in=10,
                    tokens_out=len(reply_text.split())
                )

            if stream:
                self.close_connection = True
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()

                # Stream standard Anthropic SSE events
                events = [
                    ("message_start", {
                        "type": "message_start",
                        "message": {
                            "id": msg_id,
                            "type": "message",
                            "role": "assistant",
                            "model": model,
                            "content": [],
                            "stop_reason": None,
                            "usage": {"input_tokens": 12, "output_tokens": 1}
                        }
                    }),
                    ("content_block_start", {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""}
                    }),
                    ("content_block_delta", {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": reply_text}
                    }),
                    ("content_block_stop", {
                        "type": "content_block_stop",
                        "index": 0
                    }),
                    ("message_delta", {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": len(reply_text.split())}
                    }),
                    ("message_stop", {
                        "type": "message_stop"
                    }),
                ]

                for ev_name, ev_payload in events:
                    msg_chunk = f"event: {ev_name}\ndata: {json.dumps(ev_payload)}\n\n".encode("utf-8")
                    self.wfile.write(msg_chunk)
                    self.wfile.flush()
                return

            # Non-streaming response
            self.send_header("Content-Type", "application/json")
            resp_payload = {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": reply_text}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": len(reply_text.split())}
            }
            out = json.dumps(resp_payload).encode("utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return

        # ─── OpenAI /v1/chat/completions ──────────────────────────────────────
        if path == "/v1/chat/completions":
            self.send_response(200)
            self._send_cors()

            chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            reply_text = f"Mock completion from {model}"

            # Save to isolated DB if available
            if self.state.db:
                in_msgs = body.get("messages", [])
                if in_msgs and isinstance(in_msgs, list):
                    last_msg = in_msgs[-1]
                    self.state.db.save_message(
                        conversation_id=conversation_id,
                        role="user",
                        content=str(last_msg.get("content", "")),
                        model=model,
                        provider="client"
                    )
                self.state.db.save_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=reply_text,
                    model=model,
                    provider="openai",
                    tokens_in=10,
                    tokens_out=len(reply_text.split())
                )

            if stream:
                self.close_connection = True
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()

                # Stream standard OpenAI SSE chunks
                chunks = [
                    {"id": chat_id, "object": "chat.completion.chunk", "created": int(time.time()),
                     "model": model, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]},
                    {"id": chat_id, "object": "chat.completion.chunk", "created": int(time.time()),
                     "model": model, "choices": [{"index": 0, "delta": {"content": reply_text}, "finish_reason": None}]},
                    {"id": chat_id, "object": "chat.completion.chunk", "created": int(time.time()),
                     "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                ]

                for chunk in chunks:
                    chunk_str = f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                    self.wfile.write(chunk_str)
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return

            # Non-streaming response
            self.send_header("Content-Type", "application/json")
            resp_payload = {
                "id": chat_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": reply_text},
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 12, "completion_tokens": len(reply_text.split()), "total_tokens": 12 + len(reply_text.split())}
            }
            out = json.dumps(resp_payload).encode("utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return

        self.send_response(404)
        self._send_cors()
        self.send_header("Content-Length", "0")
        self.end_headers()


class ThreadedMockServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class MockGatewayServer:
    """Wrapper holding server lifecycle, URL, port, and test state."""

    def __init__(self, host: str, port: int, state: MockGatewayState, server: ThreadedMockServer):
        self.host = host
        self.port = port
        self.state = state
        self.server = server
        self.url = f"http://{host}:{port}"

    def reset(self):
        self.state.clear_mocks()

    def set_mock_response(self, path: str, status_code: int = 200, body: Any = None, headers: Optional[Dict[str, str]] = None):
        self.state.set_mock_response(path, status_code, body, headers)


def _find_available_port(preferred: int = 20129) -> int:
    """Check if preferred port is free; if not, find an open port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass

    # Ephemeral fallback
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mock_gateway_server(e2e_db: E2ETestDB) -> Generator[MockGatewayServer, None, None]:
    """
    Spins up an in-process mock HTTP/SSE Gateway server in a background thread
    on test port 20129 (or fallback ephemeral port).
    Provides testing for /v1/messages, /v1/chat/completions, /v1/models,
    /v1/conversations, CORS, and synthetic rate limit headers.
    """
    port = _find_available_port(20129)
    host = "127.0.0.1"

    state = MockGatewayState(db=e2e_db)

    class CustomHandler(MockGatewayHandler):
        pass

    CustomHandler.state = state

    server = ThreadedMockServer((host, port), CustomHandler)
    actual_port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Wait for server readiness
    ready = False
    for _ in range(50):
        try:
            resp = requests.get(f"http://{host}:{actual_port}/health", timeout=0.2)
            if resp.status_code == 200:
                ready = True
                break
        except Exception:
            time.sleep(0.05)

    if not ready:
        server.shutdown()
        server.server_close()
        raise RuntimeError(f"Mock gateway server failed to start on port {actual_port}")

    gateway = MockGatewayServer(host, actual_port, state, server)
    yield gateway

    # Teardown
    try:
        server.shutdown()
        server.server_close()
    except Exception:
        pass


# ─── Client Sessions & Request Fixtures ──────────────────────────────────────

@pytest.fixture
def e2e_client(mock_gateway_server: MockGatewayServer) -> requests.Session:
    """
    Returns a preconfigured synchronous requests.Session configured to target
    the mock gateway server.
    """
    session = requests.Session()
    # Attach helper method for convenience
    session.gateway_url = mock_gateway_server.url  # type: ignore
    return session


@pytest.fixture
def anthropic_headers() -> Dict[str, str]:
    return {
        "x-api-key": "sk-ant-test-in-process-key",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }


@pytest.fixture
def openai_headers() -> Dict[str, str]:
    return {
        "Authorization": "Bearer sk-openai-test-in-process-key",
        "Content-Type": "application/json"
    }


@pytest.fixture
def workspace_headers() -> Dict[str, str]:
    return {
        "x-api-key": "sk-ant-org-test-key",
        "anthropic-version": "2023-06-01",
        "anthropic-workspace-id": "wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm",
        "Content-Type": "application/json"
    }
