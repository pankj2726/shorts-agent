"""
State-store configuration and backend selection.

The PostgreSQL wire path cannot be exercised here: this environment has no
outbound network and no local server. Everything in this file that touches the
driver is therefore limited to pure functions (URL validation, redaction, DDL
text). Tests that would need a live database are marked and skipped with a
reason, never silently passed.
"""
from __future__ import annotations

import pytest

from agent.modules import db

# ─── Backend selection ───────────────────────────────────────────────────────

def test_default_backend_is_sqlite():
    assert db.backend() == "sqlite"


def test_blank_backend_falls_back_to_sqlite(monkeypatch):
    """An empty Actions variable must not select postgres by accident."""
    monkeypatch.setenv("DB_BACKEND", "")
    assert db.backend() == "sqlite"


def test_backend_is_case_insensitive_and_trimmed(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "  Postgres  ")
    assert db.backend() == "postgres"


def test_postgres_without_a_url_raises_a_actionable_error(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError) as exc:
        db.get_conn()
    assert "DATABASE_URL" in str(exc.value)


def test_sqlite_path_creates_every_expected_table(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state" / "db.sqlite")
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        conn.close()
    names = {r["name"] for r in rows}
    for table in ("ideas", "videos", "assets", "comments",
                  "quota_log", "learnings", "runs", "approvals"):
        assert table in names, f"{table} missing from {sorted(names)}"


def test_sqlite_schema_migration_is_idempotent(tmp_path, monkeypatch):
    """Re-running _migrate must not duplicate or alter the application tables."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "db.sqlite")
    for _ in range(3):
        conn = db.get_conn()
        conn.close()
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        conn.close()
    names = [r["name"] for r in rows]
    app_tables = [n for n in names if not n.startswith("sqlite_")]
    # sqlite_master also holds sqlite_sequence, created by AUTOINCREMENT, so
    # assert on the application tables rather than on a raw total.
    assert sorted(app_tables) == sorted(
        ["ideas", "videos", "assets", "comments", "quota_log", "learnings",
         "runs", "approvals"]
    )
    assert len(app_tables) == len(set(app_tables)), f"duplicates in {app_tables}"


def test_rows_support_name_and_index_access(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "db.sqlite")
    conn = db.get_conn()
    try:
        cur = conn.execute("INSERT INTO ideas (idea) VALUES (?)", ("test idea",))
        new_id = cur.lastrowid
        row = conn.execute(
            "SELECT id, idea FROM ideas WHERE id = ?", (new_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["idea"] == "test idea"
    assert row[1] == "test idea"


# ─── PostgreSQL URL validation (pure functions, no driver needed) ────────────

def test_valid_url_reports_no_warnings():
    from agent.modules import db_pg

    findings = db_pg.validate_url(
        "postgresql://user:pass@db.example.co:5432/postgres?sslmode=require"
    )
    assert findings["warnings"] == []
    assert findings["host"] == "db.example.co:5432"


def test_missing_sslmode_is_warned_about():
    from agent.modules import db_pg

    findings = db_pg.validate_url("postgresql://user:pass@db.example.co/postgres")
    assert any("sslmode" in w for w in findings["warnings"])


def test_wrong_scheme_is_rejected_clearly():
    from agent.modules import db_pg

    with pytest.raises(ValueError) as exc:
        db_pg.validate_url("mysql://user:pass@db.example.co/app")
    assert "postgresql://" in str(exc.value)


def test_empty_url_is_rejected():
    from agent.modules import db_pg

    with pytest.raises(ValueError):
        db_pg.validate_url("   ")


def test_postgres_scheme_alias_is_accepted():
    from agent.modules import db_pg

    assert db_pg.validate_url("postgres://u:p@h/db?sslmode=require")["host"] == "h"


# ─── Credential redaction ────────────────────────────────────────────────────

def test_redaction_never_leaks_the_password():
    from agent.modules import db_pg

    url = "postgresql://admin:Sup3rS3cret@db.example.co:5432/postgres?sslmode=require"
    redacted = db_pg.redact_url(url)
    assert "Sup3rS3cret" not in redacted
    assert "admin" in redacted          # user is not secret
    assert "db.example.co:5432" in redacted
    assert "***" in redacted


def test_redaction_handles_an_unparseable_string():
    from agent.modules import db_pg

    assert db_pg.redact_url("not-a-url") == "<unparseable>"


# ─── Schema DDL ──────────────────────────────────────────────────────────────

def test_ddl_covers_every_table_the_sqlite_path_creates():
    from agent.modules import db_pg

    for table in db_pg.EXPECTED_TABLES:
        assert f"shorts.{table}" in db_pg.SCHEMA_DDL, f"{table} absent from DDL"


def test_ddl_is_idempotent():
    from agent.modules import db_pg

    assert "CREATE SCHEMA IF NOT EXISTS" in db_pg.SCHEMA_DDL
    assert db_pg.SCHEMA_DDL.count("CREATE TABLE IF NOT EXISTS") == 8


def test_ddl_uses_postgres_types_not_sqlite_ones():
    """AUTOINCREMENT and PRAGMA would be silently wrong on postgres."""
    from agent.modules import db_pg

    assert "AUTOINCREMENT" not in db_pg.SCHEMA_DDL
    assert "PRAGMA" not in db_pg.SCHEMA_DDL
    assert "BIGSERIAL" in db_pg.SCHEMA_DDL
    assert "TIMESTAMPTZ" in db_pg.SCHEMA_DDL


def test_every_expected_table_is_declared_exactly_once():
    from agent.modules import db_pg

    for table in db_pg.EXPECTED_TABLES:
        assert db_pg.SCHEMA_DDL.count(f"CREATE TABLE IF NOT EXISTS shorts.{table} (") == 1


# ─── Requires a live PostgreSQL server ───────────────────────────────────────

@pytest.mark.skip(
    reason="needs a live PostgreSQL server; not available in this sandbox "
           "(no outbound network, no local cluster). The DDL above was applied "
           "once to the managed project via the Supabase connector and verified "
           "through information_schema."
)
def test_ensure_schema_against_a_live_server():
    raise NotImplementedError
