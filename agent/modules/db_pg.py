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
from typing import Any, Optional, Sequence

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
        self.lastrowid: Optional[int] = None

    def _wrap(self, row) -> Optional[Row]:
        if row is None:
            return None
        cols = [d.name for d in self._cur.description]
        return Row(dict(zip(cols, row)), cols)

    def fetchone(self) -> Optional[Row]:
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
    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()


def _is_insert(sql: str) -> bool:
    return bool(re.match(r"\s*INSERT\b", sql, re.IGNORECASE))


def _last_sequence_value(conn) -> Optional[int]:
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

    raw = psycopg.connect(database_url, autocommit=False)
    raw.execute(
        "SELECT pg_catalog.set_config('search_path', %s, false)", (schema,)
    )
    return Connection(raw)
