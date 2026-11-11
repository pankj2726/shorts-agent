"""
db.py — SQLite state store (idempotent schema migrations).

Tables:
  ideas        — idea backlog
  videos       — produced videos and their YouTube IDs
  assets       — per-asset license log
  comments     — comment tracking
  quota_log    — (also managed by quota.py, referenced here for completeness)
  learnings    — weekly learning bullets
  runs         — pipeline run history
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "state" / "db.sqlite"


def get_conn() -> sqlite3.Connection:
    """Return a connection with row_factory set to sqlite3.Row."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _migrate(conn)
    return conn


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
        event_type      TEXT    NOT NULL,  -- AUTH_HANDSHAKE | TOKEN_ISSUED | MODEL_ROUTED | ANOMALY_DETECTED | ACCESS_REVOKED
        actor_email     TEXT    NOT NULL,  -- e.g. pankajbhati140@gmail.com
        provider        TEXT,
        model           TEXT,
        status          TEXT    NOT NULL,  -- SUCCESS | FAILED | BLOCKED | SUSPICIOUS
        ip_or_source    TEXT,              -- local_runner | github_actions_ci
        details         TEXT,              -- JSON metadata (never raw secrets)
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
