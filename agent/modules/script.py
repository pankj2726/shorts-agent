"""
script.py — Script generation (Scriptwriter agent).
"""
import json
import logging
from pathlib import Path

from agent.modules import db, llm, qa
from agent.modules.notify import notify

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
PROMPT_DIR = Path(__file__).parent.parent / "prompts"


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def run_script(idea: dict) -> dict | None:
    """
    Generate a script for the given idea dict (from strategy.py).
    Returns the script dict, or None if QA fails twice.
    """
    channel_bible = _load_text(CONFIG_DIR / "channel_bible.md")
    hook_rules = _load_text(CONFIG_DIR / "hook_rules.md")
    prompt_template = _load_text(PROMPT_DIR / "scriptwriter.md")

    prompt = f"""
{prompt_template}

## Channel Bible
{channel_bible}

## Hook Rules
{hook_rules}

## Selected idea
Idea: {idea.get('idea')}
Angle: {idea.get('angle')}
Target viewer: {idea.get('target_viewer')}
Hook options: {json.dumps(idea.get('hook_options', []))}
Format: {idea.get('format')}
Why it should work: {idea.get('why_it_should_work')}

Write the complete script. Output strict JSON only.
"""

    for attempt in range(1, 3):  # max 2 attempts before discarding
        notify("SCRIPT_GENERATING", f"Attempt {attempt}/2", level="info")
        try:
            result = llm.complete_json(prompt)
        except Exception as e:
            logger.error("script: LLM failed on attempt %d: %s", attempt, e)
            if attempt == 2:
                notify("QA_FAILED", f"Script LLM failed twice: {e}", level="warn")
                return None
            continue

        # QA Gate #1
        qa_result = qa.check_script(result)
        if qa_result["passed"]:
            # Save to DB
            idea_id = idea.get("idea_id")
            conn = db.get_conn()
            conn.execute(
                """UPDATE ideas SET status = 'scripting' WHERE id = ?""", (idea_id,)
            )
            cur = conn.execute(
                """INSERT INTO videos
                   (idea_id, title, description, tags, script, script_qa_ok, contains_synthetic)
                   VALUES (?, ?, ?, ?, ?, 1, ?)""",
                (
                    idea_id,
                    result.get("title", ""),
                    result.get("description", ""),
                    json.dumps(result.get("tags", [])),
                    result.get("script", ""),
                    1 if result.get("contains_synthetic") else 0,
                ),
            )
            video_id = cur.lastrowid
            conn.commit()
            conn.close()
            result["video_id"] = video_id
            notify(
                "SCRIPT_READY",
                f"📝 {result.get('title')}\n\nHook: {result.get('hook')}\n\n{result.get('script', '')[:500]}",
                level="action",
            )
            logger.info("script: video_id=%s script ready", video_id)
            return result
        else:
            reason = qa_result.get("reason", "unknown")
            notify("QA_FAILED", f"Script QA gate #1 failed (attempt {attempt}): {reason}", level="warn")
            logger.warning("script: QA gate #1 failed: %s", reason)
            if attempt == 2:
                # Discard idea
                idea_id = idea.get("idea_id")
                if idea_id:
                    conn = db.get_conn()
                    conn.execute("UPDATE ideas SET status = 'discarded' WHERE id = ?", (idea_id,))
                    conn.commit()
                    conn.close()
                return None
            # Retry with the failure reason in the prompt
            prompt += f"\n\nPREVIOUS ATTEMPT FAILED QA: {reason}\nFix all issues and try again."

    return None