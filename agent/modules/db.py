"""
db.py — SQLite state store (idempotent schema migrations).

Tables:
  ideas             — idea backlog
  videos            — produced videos and their YouTube IDs
  assets            — per-asset license log
  comments          — comment tracking
  quota_log         — (also managed by quota.py, referenced here for completeness)
  learnings         — weekly learning bullets
  runs              — pipeline run history
  audit_logs        — security & API audit trails
  conversations     — unified cross-model conversation threads
  messages          — multi-turn dialogue messages with thinking isolation
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union

DB_PATH = Path(__file__).parent.parent / "state" / "db.sqlite"


def get_conn(db_path: Optional[Path] = None, timeout: float = 30.0) -> sqlite3.Connection:
    """Return an active connection with WAL, foreign keys, busy timeout, and Row factory."""
    target_path = Path(db_path) if db_path else DB_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    _migrate(conn)
    return conn


@contextmanager
def get_db(db_path: Optional[Path] = None, timeout: float = 30.0) -> Iterator[sqlite3.Connection]:
    """Context manager providing a managed SQLite connection with auto-commit/rollback and guaranteed closure."""
    conn = get_conn(db_path=db_path, timeout=timeout)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _managed_conn(conn: Optional[sqlite3.Connection] = None) -> Iterator[sqlite3.Connection]:
    """Internal helper to either reuse an existing connection or manage a new transactional one."""
    if conn is not None:
        yield conn
    else:
        with get_db() as c:
            yield c


def init_db(db_path: Optional[Path] = None) -> None:
    """Explicitly initialize the database schema and indexes idempotently."""
    conn = get_conn(db_path=db_path)
    conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ideas (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        idea        TEXT    NOT NULL,
        angle       TEXT,
        hook        TEXT,
        format      TEXT,
        score       REAL,
        status      TEXT    DEFAULT 'pending',   -- pending | scripting | done | discarded
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS videos (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        idea_id         INTEGER REFERENCES ideas(id),
        title           TEXT,
        description     TEXT,
        tags            TEXT,       -- JSON array
        script          TEXT,
        script_qa_ok    INTEGER DEFAULT 0,
        render_path     TEXT,
        render_qa_ok    INTEGER DEFAULT 0,
        yt_video_id     TEXT,
        yt_status       TEXT,       -- private | public | scheduled | failed
        publish_at      TEXT,       -- ISO8601
        contains_synthetic INTEGER DEFAULT 0,
        quota_used      INTEGER DEFAULT 0,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        published_at    DATETIME
    );

    CREATE TABLE IF NOT EXISTS assets (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id    INTEGER REFERENCES videos(id),
        asset_type  TEXT,   -- clip | image | music | font
        source_url  TEXT,
        license     TEXT,
        local_path  TEXT,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS comments (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        yt_video_id     TEXT,
        comment_id      TEXT UNIQUE,
        author          TEXT,
        text            TEXT,
        category        TEXT,   -- question|praise|critique|spam|sensitive|other
        replied         INTEGER DEFAULT 0,
        reply_text      TEXT,
        flagged         INTEGER DEFAULT 0,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS quota_log (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        day     TEXT    NOT NULL,
        call    TEXT    NOT NULL,
        units   INTEGER NOT NULL,
        ts      DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS learnings (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        week        TEXT,   -- ISO week, e.g. 2026-W46
        bullet      TEXT,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS runs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_type    TEXT,   -- pipeline | engage | analytics | commands
        status      TEXT,   -- running | done | failed
        details     TEXT,
        started_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        finished_at DATETIME
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type      TEXT    NOT NULL,
        actor_email     TEXT    NOT NULL,
        provider        TEXT,
        model           TEXT,
        status          TEXT    NOT NULL,
        ip_or_source    TEXT,
        details         TEXT,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    -- ========================================================================
    -- Unified Cross-Model Conversation Store Tables
    -- ========================================================================
    CREATE TABLE IF NOT EXISTS conversations (
        id                  TEXT PRIMARY KEY,
        title               TEXT,
        created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
        default_provider    TEXT,
        default_model       TEXT,
        metadata            TEXT DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS messages (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id     TEXT    NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role                TEXT    NOT NULL CHECK(role IN ('user', 'assistant', 'system', 'developer', 'tool', 'function')),
        content             TEXT    NOT NULL,
        provider            TEXT,
        model               TEXT,
        thinking_content    TEXT,
        metadata            TEXT    DEFAULT '{}',
        created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_messages_conversation_id_id 
        ON messages(conversation_id, id ASC);

    CREATE INDEX IF NOT EXISTS idx_messages_conversation_id_created 
        ON messages(conversation_id, created_at ASC);

    CREATE INDEX IF NOT EXISTS idx_conversations_updated_at 
        ON conversations(updated_at DESC);

    CREATE TRIGGER IF NOT EXISTS trg_messages_update_conversation_insert
    AFTER INSERT ON messages
    BEGIN
        UPDATE conversations
        SET updated_at = CURRENT_TIMESTAMP
        WHERE id = NEW.conversation_id;
    END;

    CREATE TRIGGER IF NOT EXISTS trg_messages_update_conversation_update
    AFTER UPDATE ON messages
    BEGIN
        UPDATE conversations
        SET updated_at = CURRENT_TIMESTAMP
        WHERE id = NEW.conversation_id;
    END;
    """)
    conn.commit()


# ============================================================================
# Conversation Management (CRUD)
# ============================================================================

def create_conversation(
    conv_id: Optional[str] = None,
    title: Optional[str] = None,
    default_provider: Optional[str] = None,
    default_model: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> str:
    """Create a new conversation thread or update metadata (UPSERT semantics)."""
    cid = conv_id or f"conv_{uuid.uuid4().hex[:12]}"
    title_val = title or "New Conversation"
    meta_json = json.dumps(metadata or {})

    with _managed_conn(conn) as c:
        c.execute("""
            INSERT INTO conversations (id, title, default_provider, default_model, metadata)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = COALESCE(excluded.title, conversations.title),
                default_provider = COALESCE(excluded.default_provider, conversations.default_provider),
                default_model = COALESCE(excluded.default_model, conversations.default_model),
                metadata = CASE WHEN excluded.metadata != '{}' THEN excluded.metadata ELSE conversations.metadata END,
                updated_at = CURRENT_TIMESTAMP
        """, (cid, title_val, default_provider, default_model, meta_json))
        if conn is not None:
            c.commit()

    return cid


def get_conversation(
    conv_id: str,
    conn: Optional[sqlite3.Connection] = None
) -> Optional[Dict[str, Any]]:
    """Fetch a single conversation by ID with message count and parsed JSON metadata."""
    with _managed_conn(conn) as c:
        cur = c.execute("""
            SELECT c.*, COUNT(m.id) as message_count
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id
            WHERE c.id = ?
            GROUP BY c.id
        """, (conv_id,))
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("metadata"), str):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except Exception:
                d["metadata"] = {}
        return d


def update_conversation(
    conv_id: str,
    title: Optional[str] = None,
    default_provider: Optional[str] = None,
    default_model: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> bool:
    """Update conversation fields and refresh updated_at."""
    clauses = []
    params: List[Any] = []
    if title is not None:
        clauses.append("title = ?")
        params.append(title)
    if default_provider is not None:
        clauses.append("default_provider = ?")
        params.append(default_provider)
    if default_model is not None:
        clauses.append("default_model = ?")
        params.append(default_model)
    if metadata is not None:
        clauses.append("metadata = ?")
        params.append(json.dumps(metadata))

    clauses.append("updated_at = CURRENT_TIMESTAMP")
    params.append(conv_id)

    with _managed_conn(conn) as c:
        sql = f"UPDATE conversations SET {', '.join(clauses)} WHERE id = ?"
        cur = c.execute(sql, params)
        if conn is not None:
            c.commit()
        return cur.rowcount > 0


def delete_conversation(
    conv_id: str,
    conn: Optional[sqlite3.Connection] = None
) -> bool:
    """Delete a conversation by ID. Cascades delete to all messages in the thread."""
    with _managed_conn(conn) as c:
        cur = c.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        if conn is not None:
            c.commit()
        return cur.rowcount > 0


def list_conversations(
    limit: int = 50,
    offset: int = 0,
    conn: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """List conversations ordered by updated_at DESC with pagination and message counts."""
    with _managed_conn(conn) as c:
        cur = c.execute("""
            SELECT c.*, COUNT(m.id) as message_count
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))
        rows = cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("metadata"), str):
                try:
                    d["metadata"] = json.loads(d["metadata"])
                except Exception:
                    d["metadata"] = {}
            result.append(d)
        return result


# ============================================================================
# Message Management (CRUD)
# ============================================================================

VALID_ROLES = {"user", "assistant", "system", "developer", "tool", "function"}


def create_message(
    conversation_id: str,
    role: str,
    content: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    thinking_content: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> int:
    """
    Insert a message turn into a conversation.
    If the conversation does not exist, automatically creates it.
    If conversation has a placeholder title and role=='user', auto-generates title from content.
    Returns the integer message ID.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid message role '{role}'. Must be one of {VALID_ROLES}")

    meta_json = json.dumps(metadata or {})

    with _managed_conn(conn) as c:
        # Check parent conversation
        cur = c.execute("SELECT title FROM conversations WHERE id = ?", (conversation_id,))
        conv_row = cur.fetchone()
        if not conv_row:
            auto_title = content[:40].strip() if (role == "user" and content) else "New Conversation"
            c.execute("""
                INSERT INTO conversations (id, title, default_provider, default_model)
                VALUES (?, ?, ?, ?)
            """, (conversation_id, auto_title, provider, model))
        elif conv_row["title"] == "New Conversation" and role == "user" and content:
            auto_title = content[:40].strip()
            c.execute("UPDATE conversations SET title = ? WHERE id = ?", (auto_title, conversation_id))

        cur = c.execute("""
            INSERT INTO messages (conversation_id, role, content, provider, model, thinking_content, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (conversation_id, role, content, provider, model, thinking_content, meta_json))
        msg_id = cur.lastrowid

        # Explicitly touch updated_at in case triggers are disabled or external driver
        c.execute("UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (conversation_id,))

        if conn is not None:
            c.commit()

        return msg_id


def get_messages_by_conversation(
    conversation_id: str,
    limit: Optional[int] = None,
    offset: int = 0,
    order: str = "ASC",
    conn: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """Retrieve message turns for a conversation in chronological (ASC) or reverse (DESC) order."""
    ord_clean = "DESC" if order.upper() == "DESC" else "ASC"
    sql = f"SELECT * FROM messages WHERE conversation_id = ? ORDER BY id {ord_clean}"
    params: List[Any] = [conversation_id]
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

    with _managed_conn(conn) as c:
        cur = c.execute(sql, params)
        rows = cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("metadata"), str):
                try:
                    d["metadata"] = json.loads(d["metadata"])
                except Exception:
                    d["metadata"] = {}
            result.append(d)
        return result


def get_message(
    message_id: int,
    conn: Optional[sqlite3.Connection] = None
) -> Optional[Dict[str, Any]]:
    """Fetch a single message by ID."""
    with _managed_conn(conn) as c:
        cur = c.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("metadata"), str):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except Exception:
                d["metadata"] = {}
        return d


def delete_message(
    message_id: int,
    conn: Optional[sqlite3.Connection] = None
) -> bool:
    """Delete a single message by ID."""
    with _managed_conn(conn) as c:
        cur = c.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        if conn is not None:
            c.commit()
        return cur.rowcount > 0


def count_messages(
    conversation_id: str,
    conn: Optional[sqlite3.Connection] = None
) -> int:
    """Return total number of messages in a conversation thread."""
    with _managed_conn(conn) as c:
        cur = c.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,))
        return cur.fetchone()[0]
