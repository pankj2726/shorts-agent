"""
strategy.py — Idea selection (Strategist agent).
"""
import json
import logging
import os
from pathlib import Path

from agent.modules import db, llm
from agent.modules.notify import notify

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
PROMPT_DIR = Path(__file__).parent.parent / "prompts"


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _get_recent_titles(limit: int = 40) -> list[str]:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT title FROM videos ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [r["title"] for r in rows if r["title"]]


def run_strategy(research_data: dict) -> dict:
    """
    Run the Strategist agent and return the selected idea as a dict.
    Saves the idea to the database.
    """
    channel_bible = _load_text(CONFIG_DIR / "channel_bible.md")
    banned_topics = _load_text(CONFIG_DIR / "banned_topics.md")
    prompt_template = _load_text(PROMPT_DIR / "strategist.md")
    recent_titles = _get_recent_titles()

    prompt = f"""
{prompt_template}

## Channel Bible
{channel_bible}

## Banned Topics
{banned_topics}

## Recent titles (last 40 — do not repeat similar ideas)
{json.dumps(recent_titles, indent=2)}

## Trending topics
{json.dumps(research_data.get('trending', [])[:10], indent=2)}

## Rising search queries
{json.dumps(research_data.get('trend_queries', []), indent=2)}

Now propose 10 ideas and select the best one. Output strict JSON only.
"""

    notify("STRATEGY_START", "Generating video ideas", level="info")
    result = llm.complete_json(prompt)

    selected = result.get("selected", {})
    idea_text = selected.get("idea", "")
    angle = selected.get("angle", "")
    hook = selected.get("hook_options", [""])[0]

    # Save to DB
    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO ideas (idea, angle, hook, format, status) VALUES (?, ?, ?, ?, 'pending')",
        (idea_text, angle, hook, selected.get("format", "")),
    )
    idea_id = cur.lastrowid
    conn.commit()
    conn.close()

    selected["idea_id"] = idea_id
    notify(
        "IDEA_SELECTED",
        f"💡 {idea_text}\nAngle: {angle}\nHook options: {', '.join(selected.get('hook_options', []))}",
        level="info",
    )
    logger.info("strategy: selected idea_id=%s: %s", idea_id, idea_text)
    return selected