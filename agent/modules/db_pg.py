"""
db_pg.py — PostgreSQL adapter matching the sqlite3 surface used by this codebase.

The pipeline talks to SQLite through a very small API: `conn.execute(sql, params)`,
`conn.commit()`, `conn.close()`, plus `cursor.fetchone()`, `cursor.fetchall()` and
`cursor.lastrowid`, with rows addressed both as `row["col"]` and `row[0]`.

This module reproduces exactly that surface on psycopg so the 37 existing call
sites do not have to change. It is opt-in: `db.get_conn()` only reaches this
module when DB_BACKEND=postgres.

SQL differences handled here:
  * `?` placeholders            -> `%s`
  * `INSERT OR IGNORE`          -> `INSERT ... ON CONFLICT DO NOTHING`
  * `datetime('now', ?)`        -> `(now() + ?::interval)`
  * `cursor.lastrowid`          -> `SELECT lastval()`
  * `sqlite3.Row` addressing    -> the Row class below
"""
from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any, Self

logger = logging.getLogger(__name__)

# Schema created by the `create_shorts_state_schema` migration.
DEFAULT_SCHEMA = "shorts"

_QMARK = re.compile(r"\?")
_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\b", re.IGNORECASE)
_SQLITE_DATETIME = re.compile(
    r"datetime\(\s*'now'\s*,\s*\?\s*\)", re.IGNORECASE
)


class Row(dict):
    """A row that supports both `row["col"]` and `row[0]`, like sqlite3.Row."""

    __slots__ = ("_order",)

    def __init__(self, mapping: dict, order: Sequence[str]):
        super().__init__(mapping)
        object.__setattr__(self, "_order", list(order))

    def __getitem__(self, key):
        if isinstance(key, int):
            return super().__getitem__(self._order[key])
        return super().__getitem__(key)

    def keys(self):  # preserve column order, as sqlite3.Row does
        return list(self._order)


def translate_sql(sql: str) -> str:
    """Rewrite the SQLite-specific constructs this codebase actually uses."""
    out = _SQLITE_DATETIME.sub("(now() + %s::interval)", sql)
    out = _QMARK.sub("%s", out)
    if _INSERT_OR_IGNORE.search(out):
        out = _INSERT_OR_IGNORE.sub("INSERT", out)
        if not re.search(r"\bON\s+CONFLICT\b", out, re.IGNORECASE):
            out = out.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return out


class Cursor:
    """Wraps a psycopg cursor to expose the sqlite3 cursor surface."""

    def __init__(self, pg_cursor):
        self._cur = pg_cursor
        self.lastrowid: int | None = None

    def _wrap(self, row) -> Row | None:
        if row is None:
            return None
        cols = [d.name for d in self._cur.description]
        return Row(dict(zip(cols, row)), cols)

    def fetchone(self) -> Row | None:
        return self._wrap(self._cur.fetchone())

    def fetchall(self) -> list[Row]:
        rows = self._cur.fetchall()
        return [self._wrap(r) for r in rows]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def __iter__(self):
        for row in self._cur:
            yield self._wrap(row)


class Connection:
    """Wraps a psycopg connection to expose the sqlite3 connection surface."""

    def __init__(self, pg_conn):
        self._conn = pg_conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Cursor:
        cur = self._conn.cursor()
        cur.execute(translate_sql(sql), tuple(params or ()))
        wrapped = Cursor(cur)
        if _is_insert(sql):
            wrapped.lastrowid = _last_sequence_value(self._conn)
        return wrapped

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    # sqlite3 connections are usable as context managers; match that.
    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()


def _is_insert(sql: str) -> bool:
    return bool(re.match(r"\s*INSERT\b", sql, re.IGNORECASE))


def _last_sequence_value(conn) -> int | None:
    """PostgreSQL's equivalent of sqlite's lastrowid for a SERIAL column."""
    try:
        cur = conn.execute("SELECT lastval()")
        value = cur.fetchone()
        return int(value[0]) if value else None
    except Exception as exc:  # noqa: BLE001 - lastval() errors if no sequence ran
        logger.debug("db_pg: lastval() unavailable (%s)", exc)
        conn.rollback()
        return None


def connect(database_url: str, schema: str = DEFAULT_SCHEMA) -> Connection:
    """Open a connection with search_path pointed at the shorts schema."""
    import psycopg

    validate_url(database_url)
    raw = psycopg.connect(database_url, autocommit=False)
    raw.execute(
        "SELECT pg_catalog.set_config('search_path', %s, false)", (schema,)
    )
    return Connection(raw)


# ─── Configuration validation ────────────────────────────────────────────────

def validate_url(database_url: str) -> dict:
    """
    Check a DATABASE_URL before it is handed to psycopg.

    Returns a dict of findings so callers can surface them. Raises ValueError
    only for a string that cannot possibly work, so a typo is reported as
    "this is not a postgres URL" rather than as an opaque driver error.
    """
    url = (database_url or "").strip()
    if not url:
        raise ValueError("DATABASE_URL is empty")

    lowered = url.lower()
    if not lowered.startswith(("postgresql://", "postgres://")):
        raise ValueError(
            "DATABASE_URL must start with postgresql:// (got a different scheme); "
            "do not include the password in logs when correcting it"
        )

    findings: dict = {"warnings": []}
    if "sslmode=" not in lowered:
        findings["warnings"].append(
            "no sslmode parameter; managed providers such as Supabase require TLS"
        )
    if "@" not in url.split("://", 1)[-1]:
        findings["warnings"].append("no user:password@host section found")
    # Never echo the credentials back — report shape only.
    findings["host"] = _host_of(url)
    findings["redacted"] = redact_url(url)
    return findings


def _host_of(url: str) -> str:
    """Host (and port) from a URL, with no credentials attached."""
    try:
        tail = url.split("://", 1)[1]
        tail = tail.split("@", 1)[-1]
        return tail.split("/", 1)[0]
    except IndexError:
        return ""


def redact_url(url: str) -> str:
    """A DATABASE_URL safe to put in a log line: password replaced, rest intact."""
    if "@" not in url or "://" not in url:
        return "<unparseable>"
    scheme, tail = url.split("://", 1)
    creds, rest = tail.split("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{rest}"


# ─── Schema migration ────────────────────────────────────────────────────────

SCHEMA_DDL = """
CREATE SCHEMA IF NOT EXISTS shorts;

CREATE TABLE IF NOT EXISTS shorts.ideas (
    id          BIGSERIAL PRIMARY KEY,
    idea        TEXT        NOT NULL,
    angle       TEXT,
    hook        TEXT,
    format      TEXT,
    score       DOUBLE PRECISION,
    status      TEXT        DEFAULT 'pending',
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shorts.videos (
    id                   BIGSERIAL PRIMARY KEY,
    idea_id              BIGINT REFERENCES shorts.ideas(id),
    title                TEXT,
    description          TEXT,
    tags                 TEXT,
    script               TEXT,
    script_qa_ok         INTEGER DEFAULT 0,
    render_path          TEXT,
    render_qa_ok         INTEGER DEFAULT 0,
    yt_video_id          TEXT,
    yt_status            TEXT,
    publish_at           TEXT,
    contains_synthetic   INTEGER DEFAULT 0,
    quota_used           INTEGER DEFAULT 0,
    created_at           TIMESTAMPTZ DEFAULT now(),
    published_at         TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS shorts.assets (
    id          BIGSERIAL PRIMARY KEY,
    video_id    BIGINT REFERENCES shorts.videos(id),
    asset_type  TEXT,
    source_url  TEXT,
    license     TEXT,
    local_path  TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shorts.comments (
    id           BIGSERIAL PRIMARY KEY,
    yt_video_id  TEXT,
    comment_id   TEXT UNIQUE,
    author       TEXT,
    text         TEXT,
    category     TEXT,
    replied      INTEGER DEFAULT 0,
    reply_text   TEXT,
    flagged      INTEGER DEFAULT 0,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shorts.quota_log (
    id     BIGSERIAL PRIMARY KEY,
    day    TEXT        NOT NULL,
    call   TEXT        NOT NULL,
    units  INTEGER     NOT NULL,
    ts     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shorts.learnings (
    id          BIGSERIAL PRIMARY KEY,
    week        TEXT,
    bullet      TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shorts.runs (
    id           BIGSERIAL PRIMARY KEY,
    run_type     TEXT,
    status       TEXT,   -- running | done | failed | blocked
    details      TEXT,
    started_at   TIMESTAMPTZ DEFAULT now(),
    finished_at  TIMESTAMPTZ
);

-- Publishing consults this table and nothing else. There is no env var that
-- can substitute for a row here; see agent/modules/approvals.py.
CREATE TABLE IF NOT EXISTS shorts.approvals (
    id           BIGSERIAL PRIMARY KEY,
    video_id     BIGINT REFERENCES shorts.videos (id),
    decision     TEXT,   -- approved | rejected
    approved_by  TEXT,   -- identity from SHORTS_APPROVER_ALLOWLIST
    channel      TEXT,   -- how the decision arrived (cli now, web/android in P4)
    content_hash TEXT,   -- sha256 of the approved render; binds the grant to bytes
    granted_at   TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS videos_yt_id_idx     ON shorts.videos (yt_video_id);
CREATE INDEX IF NOT EXISTS videos_published_idx ON shorts.videos (published_at);
CREATE INDEX IF NOT EXISTS comments_yt_id_idx   ON shorts.comments (yt_video_id);
CREATE INDEX IF NOT EXISTS quota_log_day_idx    ON shorts.quota_log (day);
CREATE INDEX IF NOT EXISTS runs_started_idx     ON shorts.runs (started_at);
CREATE INDEX IF NOT EXISTS approvals_video_idx  ON shorts.approvals (video_id);

-- P4.1: record authentication and single-use consumption. ADD COLUMN IF NOT
-- EXISTS is native to Postgres, so an already-migrated database gains these
-- without a rewrite. All four are nullable and additive: nothing is dropped,
-- re-typed or back-filled destructively.
ALTER TABLE shorts.approvals ADD COLUMN IF NOT EXISTS signature   TEXT;
ALTER TABLE shorts.approvals ADD COLUMN IF NOT EXISTS nonce       TEXT;
ALTER TABLE shorts.approvals ADD COLUMN IF NOT EXISTS consumed_at TIMESTAMPTZ;
ALTER TABLE shorts.approvals ADD COLUMN IF NOT EXISTS consumed_by TEXT;

-- Append-only log of every approval decision and refusal. Rows are never
-- updated or deleted, so the trail survives a later revoke or consume.
CREATE TABLE IF NOT EXISTS shorts.approval_audit (
    id           BIGSERIAL PRIMARY KEY,
    approval_id  BIGINT REFERENCES shorts.approvals (id),
    video_id     BIGINT,
    action       TEXT,   -- granted | consumed | revoked | refused
    actor        TEXT,
    reason       TEXT,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS approval_audit_video_idx ON shorts.approval_audit (video_id);
"""

EXPECTED_TABLES = (
    "ideas", "videos", "assets", "comments", "quota_log", "learnings", "runs",
    "approvals", "approval_audit",
)


def ensure_schema(database_url: str, schema: str = DEFAULT_SCHEMA) -> list[str]:
    """
    Create the state schema idempotently, mirroring db.py's _migrate().

    The SQLite path has always migrated on connect; Postgres previously had no
    equivalent, so it only worked against a database whose tables had been
    created out of band. Returns the tables present afterwards.
    """
    import psycopg

    # CREATE SCHEMA cannot run inside the same transaction as set_config for a
    # role that lacks the schema, so use autocommit for the DDL pass.
    raw = psycopg.connect(database_url, autocommit=True)
    try:
        raw.execute(SCHEMA_DDL)
    finally:
        raw.close()

    conn = connect(database_url, schema)
    try:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s",
            (schema,),
        ).fetchall()
        return sorted(r[0] for r in rows)
    finally:
        conn.close()


def diagnose(database_url: str, schema: str = DEFAULT_SCHEMA) -> dict:
    """
    Startup diagnostics for the postgres backend.

    Reports connection, schema presence and per-table row counts without ever
    printing credentials. Safe to call from a workflow step.
    """
    report: dict = {"backend": "postgres", "url": redact_url(database_url)}
    try:
        findings = validate_url(database_url)
        report["host"] = findings["host"]
        report["warnings"] = findings["warnings"]
    except ValueError as exc:
        report["ok"] = False
        report["error"] = str(exc)
        return report

    try:
        tables = ensure_schema(database_url, schema)
    except Exception as exc:  # noqa: BLE001 - surface any driver failure
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report

    missing = [t for t in EXPECTED_TABLES if t not in tables]
    report["ok"] = not missing
    report["schema"] = schema
    report["tables"] = tables
    report["missing_tables"] = missing

    conn = connect(database_url, schema)
    try:
        counts = {}
        for table in tables:
            # Names come from information_schema, not from a caller, but they
            # are interpolated rather than bound, so allow-list them anyway.
            if table not in EXPECTED_TABLES:
                continue
            # nosec B608 - `table` is restricted to EXPECTED_TABLES above
            row = conn.execute(f"SELECT count(*) FROM {schema}.{table}").fetchone()
            counts[table] = row[0]
        report["row_counts"] = counts
    finally:
        conn.close()
    return report
