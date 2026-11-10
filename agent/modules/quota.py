"""
quota.py — YouTube Data API v3 quota ledger.

Verified unit costs (2026-11-09):
  videos.insert      1600
  videos.update        50
  thumbnails.set       50
  comments.insert      50
  commentThreads.list   1
  videos.list           1
  playlistItems.list    1
  channels.list         1
  search.list         100

Daily limit: 10,000 units per project.
Resets: midnight Pacific (~12:30–13:30 IST).
Failed requests still cost quota — validate before every call.
Never shard quota across Cloud projects.
"""
import logging
import sqlite3
from datetime import date
from pathlib import Path

from agent.modules.notify import notify

logger = logging.getLogger(__name__)

DAILY_LIMIT = 10_000
WARN_THRESHOLD = 0.20  # pause when < 20% remaining

# Canonical cost table (units per call)
UNIT_COSTS: dict[str, int] = {
    "videos.insert": 1600,
    "videos.update": 50,
    "thumbnails.set": 50,
    "comments.insert": 50,
    "comments.setModerationStatus": 50,
    "commentThreads.list": 1,
    "commentThreads.insert": 50,
    "videos.list": 1,
    "playlistItems.list": 1,
    "channels.list": 1,
    "search.list": 100,
}

_DB_PATH = Path(__file__).parent.parent / "state" / "db.sqlite"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quota_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            day       TEXT    NOT NULL,
            call      TEXT    NOT NULL,
            units     INTEGER NOT NULL,
            ts        DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def used_today() -> int:
    """Return total quota units consumed today."""
    today = date.today().isoformat()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(units), 0) FROM quota_log WHERE day = ?", (today,)
        ).fetchone()
    return int(row[0]) if row else 0


def remaining() -> int:
    """Return units remaining today."""
    return DAILY_LIMIT - used_today()


def pct_remaining() -> float:
    """Return fraction remaining (0.0–1.0)."""
    return remaining() / DAILY_LIMIT


def check(call: str) -> bool:
    """
    Return True if the quota allows the given call.
    Returns False and sends a warning notification if below 20%.
    Use this BEFORE every API call.
    """
    cost = UNIT_COSTS.get(call, 50)  # default 50 if unknown
    rem = remaining()
    if rem < cost:
        notify(
            "QUOTA_LOW",
            f"Cannot make {call} (costs {cost}u): only {rem}u remain today. "
            "Publishing paused. Resets ~12:30–13:30 IST.",
            level="warn",
        )
        logger.warning("quota: insufficient for %s (%du needed, %du left)", call, cost, rem)
        return False
    if pct_remaining() < WARN_THRESHOLD:
        notify(
            "QUOTA_LOW",
            f"⚠️ Quota below 20%: {rem}u remain. Next call: {call} ({cost}u).",
            level="warn",
        )
    return True


def consume(call: str, units: int | None = None) -> None:
    """
    Record quota consumption after a successful (or failed) API call.
    Pass `units` explicitly only if the call cost differs from the table.
    """
    today = date.today().isoformat()
    cost = units if units is not None else UNIT_COSTS.get(call, 50)
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO quota_log (day, call, units) VALUES (?, ?, ?)",
            (today, call, cost),
        )
        conn.commit()
    logger.debug("quota: consumed %du for %s (total today: %du)", cost, call, used_today())


def daily_summary() -> dict:
    """Return a summary dict for reporting."""
    today = date.today().isoformat()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT call, SUM(units) FROM quota_log WHERE day = ? GROUP BY call",
            (today,),
        ).fetchall()
    breakdown = {r[0]: r[1] for r in rows}
    return {
        "date": today,
        "used": used_today(),
        "remaining": remaining(),
        "pct_remaining": round(pct_remaining() * 100, 1),
        "breakdown": breakdown,
    }
