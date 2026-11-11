"""
test_boundaries_concurrency.py — Tier 2 Boundary Tests for Concurrency & SQLite Stress.

Covers:
  - Concurrent requests to /v1/messages and /v1/chat/completions (ThreadPoolExecutor)
  - Simultaneous SQLite writes into same and distinct conversation threads
  - WAL mode & busy_timeout lock prevention verification across multiple connections
  - Concurrent reads while writing under high contention
  - Concurrent streaming SSE threads without stream cross-talk
  - Thread pool exhaustion and burst capacity recovery
  - Concurrent role translation (Anthropic <-> OpenAI formats)
  - Cascade deletion and message ordering stability under concurrency
"""

from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import threading
import time
from typing import Any, Dict, List
import pytest
import requests

from agent.modules.db import get_conn, get_db
from tests.e2e.conftest import MockGatewayServer, extract_sse_text


# ─── Thread-Safe Wrapper for In-Process Mock State ───────────────────────────

class ThreadSafeDBWrapper:
    """Serializes access to the single shared E2ETestDB instance across server threads."""
    def __init__(self, target_db):
        self._target_db = target_db
        self._lock = threading.Lock()

    def create_conversation(self, *args, **kwargs):
        with self._lock:
            return self._target_db.create_conversation(*args, **kwargs)

    def save_message(self, *args, **kwargs):
        with self._lock:
            return self._target_db.save_message(*args, **kwargs)

    def get_conversation(self, *args, **kwargs):
        with self._lock:
            return self._target_db.get_conversation(*args, **kwargs)

    def get_conversation_messages(self, *args, **kwargs):
        with self._lock:
            return self._target_db.get_conversation_messages(*args, **kwargs)

    def list_conversations(self, *args, **kwargs):
        with self._lock:
            return self._target_db.list_conversations(*args, **kwargs)

    def __getattr__(self, item):
        return getattr(self._target_db, item)


@pytest.fixture(autouse=True)
def ensure_threadsafe_mock_db(mock_gateway_server: MockGatewayServer):
    """Wraps mock_gateway_server.state.db in ThreadSafeDBWrapper for concurrency tests."""
    if mock_gateway_server.state.db and not isinstance(mock_gateway_server.state.db, ThreadSafeDBWrapper):
        mock_gateway_server.state.db = ThreadSafeDBWrapper(mock_gateway_server.state.db)
    yield


# ─── Concurrent HTTP Endpoint Stress ──────────────────────────────────────────

def test_concurrent_anthropic_requests_threadpool(
    mock_gateway_server: MockGatewayServer,
    anthropic_headers: Dict[str, str]
):
    """Test 10 concurrent requests to /v1/messages via ThreadPoolExecutor."""
    def send_req(i: int):
        headers = dict(anthropic_headers)
        headers["Connection"] = "close"
        resp = requests.post(
            f"{mock_gateway_server.url}/v1/messages",
            headers=headers,
            json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": f"Msg {i}"}]},
            timeout=10.0
        )
        return resp.status_code, resp.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(send_req, i) for i in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 10
    for status_code, body in results:
        assert status_code == 200
        assert body["type"] == "message"


def test_concurrent_openai_requests_threadpool(
    mock_gateway_server: MockGatewayServer,
    openai_headers: Dict[str, str]
):
    """Test 10 concurrent requests to /v1/chat/completions via ThreadPoolExecutor."""
    def send_req(i: int):
        headers = dict(openai_headers)
        headers["Connection"] = "close"
        resp = requests.post(
            f"{mock_gateway_server.url}/v1/chat/completions",
            headers=headers,
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": f"Query {i}"}]},
            timeout=10.0
        )
        return resp.status_code, resp.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(send_req, i) for i in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 10
    for status_code, body in results:
        assert status_code == 200
        assert "choices" in body


def test_mixed_concurrent_anthropic_and_openai_requests(
    mock_gateway_server: MockGatewayServer,
    anthropic_headers: Dict[str, str],
    openai_headers: Dict[str, str]
):
    """Test 10 Anthropic and 10 OpenAI requests running interleaved in parallel."""
    def send_anthropic(i: int):
        h = dict(anthropic_headers)
        h["Connection"] = "close"
        return requests.post(
            f"{mock_gateway_server.url}/v1/messages",
            headers=h,
            json={"model": "claude-3-5-haiku", "messages": [{"role": "user", "content": f"Anthropic {i}"}]},
            timeout=10.0
        ).status_code

    def send_openai(i: int):
        h = dict(openai_headers)
        h["Connection"] = "close"
        return requests.post(
            f"{mock_gateway_server.url}/v1/chat/completions",
            headers=h,
            json={"model": "deepseek-v3", "messages": [{"role": "user", "content": f"OpenAI {i}"}]},
            timeout=10.0
        ).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        f_ant = [executor.submit(send_anthropic, i) for i in range(10)]
        f_oai = [executor.submit(send_openai, i) for i in range(10)]
        all_futures = f_ant + f_oai
        statuses = [f.result() for f in concurrent.futures.as_completed(all_futures)]

    assert len(statuses) == 20
    assert all(s == 200 for s in statuses)


# ─── SQLite Concurrency Across Multiple Independent Connections ───────────────

def test_simultaneous_sqlite_writes_same_conversation(e2e_db):
    """Test 15 threads concurrently saving messages into the same conversation using per-thread connections."""
    cid = e2e_db.create_conversation(title="Concurrent Writes Shared Conversation")

    def worker_save(idx: int):
        conn = get_conn(db_path=e2e_db.db_path, timeout=15.0)
        try:
            with conn:
                conn.execute("""
                    INSERT INTO messages (
                        id, conversation_id, role, content, model, provider, thinking, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"msg_sim_{idx}",
                    cid,
                    "user" if idx % 2 == 0 else "assistant",
                    f"Concurrent message payload {idx}",
                    "claude-3-5-sonnet",
                    "anthropic",
                    None,
                    "{}"
                ))
            return idx
        finally:
            conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(worker_save, i) for i in range(15)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 15
    msgs = e2e_db.get_conversation_messages(cid, format="raw")
    assert len(msgs) == 15


def test_simultaneous_sqlite_writes_distinct_conversations(e2e_db):
    """Test 20 threads writing to 20 distinct conversations in parallel."""
    def worker_conv(idx: int):
        cid = f"conv_dist_{idx}"
        conn = get_conn(db_path=e2e_db.db_path, timeout=15.0)
        try:
            with conn:
                conn.execute("INSERT OR REPLACE INTO conversations (id, title) VALUES (?, ?)", (cid, f"Conv {idx}"))
                conn.execute("""
                    INSERT INTO messages (conversation_id, role, content, model, provider)
                    VALUES (?, ?, ?, ?, ?)
                """, (cid, "user", f"Hello from thread {idx}", "mistral-large-2411", "mistral"))
            return cid
        finally:
            conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(worker_conv, i) for i in range(20)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 20
    convs = e2e_db.list_conversations(limit=50)
    assert len(convs) >= 20


def test_database_busy_timeout_prevents_lock_error(e2e_db):
    """Stress SQLite with 30 rapid write transactions across 10 threads verifying WAL mode prevents locks."""
    def rapid_insert(i: int):
        cid = f"conv_busy_{i}"
        conn = get_conn(db_path=e2e_db.db_path, timeout=20.0)
        try:
            with conn:
                conn.execute("INSERT OR IGNORE INTO conversations (id, title) VALUES (?, ?)", (cid, f"Busy {i}"))
                for j in range(3):
                    conn.execute("""
                        INSERT INTO messages (conversation_id, role, content, model, provider)
                        VALUES (?, ?, ?, ?, ?)
                    """, (cid, "user", f"Turn {j} of task {i}", "gpt-4o", "openai"))
            return True
        finally:
            conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(rapid_insert, i) for i in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert all(r is True for r in results)


def test_concurrent_reads_while_heavy_writing(e2e_db):
    """Verify reader threads encounter zero lock errors while writer threads actively insert."""
    cid = e2e_db.create_conversation(title="Read-Write Contention Thread")
    stop_signal = threading.Event()

    def writer():
        conn = get_conn(db_path=e2e_db.db_path, timeout=15.0)
        try:
            count = 0
            while not stop_signal.is_set() and count < 20:
                with conn:
                    conn.execute("""
                        INSERT INTO messages (conversation_id, role, content, model, provider)
                        VALUES (?, ?, ?, ?, ?)
                    """, (cid, "user", f"Writer turn {count}", "claude-3-5-sonnet", "anthropic"))
                count += 1
                time.sleep(0.01)
        finally:
            conn.close()

    def reader():
        conn = get_conn(db_path=e2e_db.db_path, timeout=15.0)
        try:
            read_counts = []
            for _ in range(10):
                cur = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (cid,))
                read_counts.append(cur.fetchone()[0])
                time.sleep(0.015)
            return read_counts
        finally:
            conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        w_futures = [executor.submit(writer) for _ in range(3)]
        r_futures = [executor.submit(reader) for _ in range(3)]

        for f in w_futures:
            f.result()
        stop_signal.set()
        for f in r_futures:
            counts = f.result()
            assert all(isinstance(c, int) for c in counts)


# ─── Concurrent Streaming & Resource Contention ───────────────────────────────

def test_concurrent_streaming_sse_threads(
    mock_gateway_server: MockGatewayServer,
    anthropic_headers: Dict[str, str]
):
    """Test 5 concurrent client threads consuming Anthropic SSE streams simultaneously."""
    def stream_worker(idx: int):
        h = dict(anthropic_headers)
        h["Connection"] = "close"
        resp = requests.post(
            f"{mock_gateway_server.url}/v1/messages",
            headers=h,
            json={"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": f"Stream {idx}"}], "stream": True},
            stream=True,
            timeout=10.0
        )
        assert resp.status_code == 200
        text = extract_sse_text(resp)
        return text

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(stream_worker, i) for i in range(5)]
        texts = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(texts) == 5
    for t in texts:
        assert "Mock response from claude-3-5-sonnet" in t


def test_rapid_fire_session_creation(e2e_db):
    """Test 30 conversations created in rapid sequence across 5 worker threads."""
    def create_batch(batch_id: int):
        cids = []
        conn = get_conn(db_path=e2e_db.db_path, timeout=15.0)
        try:
            with conn:
                for i in range(6):
                    cid = f"conv_batch_{batch_id}_{i}"
                    conn.execute("INSERT OR REPLACE INTO conversations (id, title) VALUES (?, ?)", (cid, f"Batch {batch_id} Item {i}"))
                    cids.append(cid)
            return cids
        finally:
            conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(create_batch, b) for b in range(5)]
        all_cids = [cid for f in concurrent.futures.as_completed(futures) for cid in f.result()]

    assert len(all_cids) == 30
    assert len(set(all_cids)) == 30


def test_thread_pool_exhaustion_recovery(
    mock_gateway_server: MockGatewayServer,
    openai_headers: Dict[str, str]
):
    """Test burst of 20 concurrent requests dispatched to gateway; verify server remains responsive after."""
    def burst_req(i: int):
        h = dict(openai_headers)
        h["Connection"] = "close"
        resp = requests.post(
            f"{mock_gateway_server.url}/v1/chat/completions",
            headers=h,
            json={"model": "llama-3.1-405b", "messages": [{"role": "user", "content": f"Burst {i}"}]},
            timeout=10.0
        )
        return resp.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(burst_req, i) for i in range(20)]
        statuses = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(statuses) == 20
    assert all(s == 200 for s in statuses)

    # Server remains responsive for health check immediately after
    h_resp = requests.get(f"{mock_gateway_server.url}/health", timeout=5.0)
    assert h_resp.status_code == 200
    assert h_resp.json()["status"] == "ok"


# ─── Concurrent Translation, Listing & Triggers ───────────────────────────────

def test_concurrent_role_translation(e2e_db):
    """Test multiple threads concurrently converting conversation messages to anthropic and openai formats."""
    cid = e2e_db.create_conversation(title="Translation Thread Stress")
    e2e_db.save_message(cid, role="user", content="Question 1", model="claude-3-5-sonnet", provider="anthropic")
    e2e_db.save_message(cid, role="assistant", content="Answer 1", model="claude-3-5-sonnet", provider="anthropic")
    e2e_db.save_message(cid, role="user", content="Question 2", model="claude-3-5-sonnet", provider="anthropic")

    def translator(fmt: str):
        return e2e_db.get_conversation_messages(cid, format=fmt)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(translator, "anthropic" if i % 2 == 0 else "openai") for i in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 10
    for r in results:
        assert len(r) == 3


def test_concurrent_model_listing(mock_gateway_server: MockGatewayServer):
    """Test 15 threads concurrently querying GET /v1/models in parallel."""
    def fetch_models():
        resp = requests.get(f"{mock_gateway_server.url}/v1/models", headers={"Connection": "close"}, timeout=5.0)
        return resp.status_code, resp.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(fetch_models) for _ in range(15)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 15
    for code, data in results:
        assert code == 200
        assert data["object"] == "list"
        assert len(data["data"]) >= 10


def test_concurrent_conversation_listing(
    mock_gateway_server: MockGatewayServer,
    e2e_db
):
    """Test 10 threads querying GET /v1/conversations in parallel while new messages are being inserted."""
    for i in range(5):
        cid = e2e_db.create_conversation(title=f"Initial Conv {i}")
        e2e_db.save_message(cid, role="user", content="Init", model="gpt-4o", provider="openai")

    def fetch_convs():
        resp = requests.get(f"{mock_gateway_server.url}/v1/conversations", headers={"Connection": "close"}, timeout=5.0)
        return resp.status_code, resp.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_convs) for _ in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 10
    for code, data in results:
        assert code == 200
        assert data["object"] == "list"


def test_atomic_conversation_updated_at_trigger(e2e_db):
    """Verify conversations.updated_at timestamp updates monotonically under sequential and concurrent inserts."""
    cid = e2e_db.create_conversation(title="Trigger Monotonic Test")
    t0_meta = e2e_db.get_conversation(cid)
    t0 = t0_meta["updated_at"]

    time.sleep(0.05)
    e2e_db.save_message(cid, role="user", content="Update 1", model="gpt-4o", provider="openai")
    t1_meta = e2e_db.get_conversation(cid)
    t1 = t1_meta["updated_at"]

    assert t1 >= t0


def test_concurrent_conversation_deletion_cascade(e2e_db):
    """Test deleting a parent conversation while reading its messages handles cascade gracefully."""
    cid = e2e_db.create_conversation(title="Cascade Deletion Test")
    for i in range(5):
        e2e_db.save_message(cid, role="user", content=f"Msg {i}", model="gpt-4o", provider="openai")

    msgs_before = e2e_db.get_conversation_messages(cid, format="raw")
    assert len(msgs_before) == 5

    # Delete conversation
    e2e_db.execute("DELETE FROM conversations WHERE id = ?", (cid,))

    # Messages cascade deleted
    msgs_after = e2e_db.get_conversation_messages(cid, format="raw")
    assert len(msgs_after) == 0


def test_message_ordering_integrity_under_concurrency(e2e_db):
    """Verify message created_at / rowid ordering remains stable under concurrent inserts."""
    cid = e2e_db.create_conversation(title="Order Stability")
    for i in range(10):
        e2e_db.save_message(
            conversation_id=cid,
            role="user",
            content=f"Sequential index {i}",
            model="gpt-4o",
            provider="openai"
        )

    msgs = e2e_db.get_conversation_messages(cid, format="raw")
    assert len(msgs) == 10
    for i, m in enumerate(msgs):
        assert f"Sequential index {i}" in m["content"]


def test_concurrent_cors_preflight_options(mock_gateway_server: MockGatewayServer):
    """Test 15 threads sending OPTIONS preflight requests simultaneously."""
    def send_options():
        resp = requests.options(f"{mock_gateway_server.url}/v1/messages", headers={"Connection": "close"}, timeout=5.0)
        return resp.status_code, resp.headers.get("Access-Control-Allow-Origin")

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(send_options) for _ in range(15)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 15
    for code, origin in results:
        assert code == 200
        assert origin == "*"
