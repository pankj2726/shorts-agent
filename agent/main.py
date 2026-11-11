"""
main.py — Pipeline orchestrator.

Usage (from GitHub Actions):
  python -m agent.main pipeline   # full daily pipeline
  python -m agent.main engage     # comment engagement
  python -m agent.main analytics  # daily/weekly analytics
  python -m agent.main commands   # Telegram command handler
  python -m agent.main smoke      # smoke test (Phase 1 exit criterion)
"""
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

def _load_env_file() -> None:
    candidates = [
        Path(r"C:\Secrets\shorts-agent.env"),
        Path(r"C:\Secrets\.env"),
        Path(__file__).parent.parent / ".env",
        Path(".env"),
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k and not os.environ.get(k):
                            os.environ[k] = v
            except Exception:
                pass
            break

_load_env_file()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("main")


def _notify_safe(event: str, text: str, level: str = "info") -> None:
    try:
        from agent.modules.notify import notify
        notify(event, text, level=level)
    except Exception:
        pass


def run_pipeline() -> None:
    from agent.modules import db, quota
    from agent.modules.notify import notify
    from agent.modules.research import run_research
    from agent.modules.strategy import run_strategy
    from agent.modules.script import run_script
    from agent.modules.tts import generate_voiceover
    from agent.modules.assets import fetch_stock_clips, pick_music
    from agent.modules.render import render_short
    from agent.modules.qa import check_render
    from agent.modules.publish import upload_short, send_for_manual_review

    import tempfile
    from pathlib import Path

    logger.info("=== Pipeline start ===")
    conn = db.get_conn()
    run_id = conn.execute(
        "INSERT INTO runs (run_type, status) VALUES ('pipeline', 'running')"
    ).lastrowid
    conn.commit()
    conn.close()

    try:
        # 1. Research
        research = run_research()

        # 2. Strategy
        idea = run_strategy(research)
        if not idea:
            raise RuntimeError("Strategy returned no idea")

        # 3. Script + QA gate #1
        script = run_script(idea)
        if not script:
            raise RuntimeError("Script generation failed both attempts")

        video_id = script["video_id"]
        work_dir = Path(tempfile.mkdtemp(prefix=f"short_{video_id}_"))

        # 4. TTS voiceover
        lang = os.environ.get("LANGUAGE", "en")
        tts = generate_voiceover(script["script"], work_dir, language=lang, video_id=video_id)

        # 5. Stock assets
        keywords = [s["search_keyword"] for s in script.get("scene_list", [])]
        if not keywords:
            keywords = [idea.get("idea", "productivity")]
        clips = fetch_stock_clips(keywords, work_dir, video_id)
        music = pick_music()

        # 6. Render + QA gate #2
        for attempt in range(1, 3):
            render_info = render_short(video_id, script, tts, clips, music, work_dir)
            qa_result = check_render(render_info)
            if qa_result["passed"]:
                break
            else:
                from agent.modules import notify as n
                notify("QA_FAILED", f"Render QA gate #2 failed (attempt {attempt}): {qa_result['reason']}", level="warn")
                if attempt == 2:
                    raise RuntimeError(f"Render QA gate #2 failed twice: {qa_result['reason']}")

        # Update DB
        conn = db.get_conn()
        conn.execute(
            "UPDATE videos SET render_path=?, render_qa_ok=1 WHERE id=?",
            (render_info["video_path"], video_id),
        )
        conn.commit()
        conn.close()

        # 7. Publish or send for manual review
        approval_required = os.environ.get("APPROVAL_REQUIRED", "true").lower() == "true"
        if approval_required:
            _wait_for_approval(video_id, render_info["video_path"], script)

        # Determine publish time (next scheduled slot)
        from agent.modules.publish import upload_short, send_for_manual_review
        publish_times = os.environ.get("PUBLISH_TIMES", "13:00,19:00").split(",")
        # Simplified: schedule for tomorrow at first slot
        from datetime import date, timedelta
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        publish_at = f"{tomorrow}T{publish_times[0].strip()}:00+05:30"  # IST

        yt_id = upload_short(
            video_id=video_id,
            video_path=render_info["video_path"],
            title=script["title"],
            description=script["description"],
            tags=script.get("tags", []),
            publish_at=publish_at,
            contains_synthetic=bool(script.get("contains_synthetic")),
        )

        if not yt_id:
            # Fallback: send to Telegram for manual scheduling
            send_for_manual_review(
                video_id, render_info["video_path"], script["title"], script
            )

        # Mark pipeline run done
        conn = db.get_conn()
        conn.execute(
            "UPDATE runs SET status='done', finished_at=?, details=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), f"video_id={video_id} yt_id={yt_id}", run_id),
        )
        conn.commit()
        conn.close()
        logger.info("=== Pipeline complete ===")

    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("Pipeline failed: %s\n%s", exc, tb)
        _notify_safe(
            "PIPELINE_FAILED",
            f"❌ Pipeline error: {str(exc)[:400]}\nRun: {os.environ.get('GH_RUN_URL', '?')}",
            level="error",
        )
        conn = db.get_conn()
        conn.execute(
            "UPDATE runs SET status='failed', finished_at=?, details=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), str(exc)[:500], run_id),
        )
        conn.commit()
        conn.close()
        sys.exit(1)


def _wait_for_approval(video_id: int, video_path: str, script: dict) -> None:
    """Poll Telegram for approval/rejection for up to 2 hours."""
    from agent.modules.notify import notify, poll_updates, answer_callback

    notify(
        "RENDER_DONE",
        f"🔔 Approve to upload?\nTitle: {script['title']}\nHook: {script['hook']}",
        level="action",
        video_path=video_path,
        buttons=[[
            ("✅ Approve", f"approve:{video_id}"),
            ("❌ Reject", f"reject:{video_id}"),
            ("🔄 Regenerate", f"regen:{video_id}"),
        ]],
    )

    allowed_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    deadline = time.time() + 2 * 3600  # 2-hour timeout
    offset = 0

    while time.time() < deadline:
        updates = poll_updates(offset=offset, timeout=30)
        for upd in updates:
            offset = upd["update_id"] + 1
            cq = upd.get("callback_query")
            if not cq:
                continue
            chat_id = str(cq["message"]["chat"]["id"])
            if chat_id != allowed_chat:
                continue  # ignore other chats
            data = cq.get("data", "")
            answer_callback(cq["id"], "Got it!")
            if data == f"approve:{video_id}":
                logger.info("main: approved by operator")
                return
            elif data in (f"reject:{video_id}", f"regen:{video_id}"):
                raise RuntimeError(f"Operator rejected/regenerated video_id={video_id}")

    raise RuntimeError(f"Approval timeout for video_id={video_id} — not published")


def run_engage() -> None:
    from agent.modules.comments import run_engage as _engage
    _engage()


def run_analytics() -> None:
    report_type = os.environ.get("REPORT_TYPE", "daily")
    if report_type == "weekly":
        from agent.modules.analytics import run_weekly
        run_weekly()
    else:
        from agent.modules.analytics import run_daily
        run_daily()


def run_commands() -> None:
    """Handle /status /pause /resume from Telegram."""
    from agent.modules.notify import poll_updates, notify, answer_callback

    allowed_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    updates = poll_updates(offset=0, timeout=0)  # non-blocking

    for upd in updates:
        msg = upd.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if chat_id != allowed_chat:
            continue
        text = msg.get("text", "").strip().lower()

        if text == "/status":
            from agent.modules import quota as q
            from agent.modules.db import get_conn
            qs = q.daily_summary()
            conn = get_conn()
            last_run = conn.execute(
                "SELECT run_type, status, started_at FROM runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            status_text = (
                f"🟢 Status\n"
                f"Quota: {qs['used']}u used / {qs['remaining']}u left\n"
                f"Last run: {dict(last_run) if last_run else 'none'}\n"
                f"PAUSE_PUBLISHING: {os.environ.get('PAUSE_PUBLISHING', 'false')}"
            )
            notify("STATUS", status_text, level="info")

        elif text == "/pause":
            _set_repo_variable("PAUSE_PUBLISHING", "true")
            notify("AUTO_PAUSE", "⏸ Publishing paused by operator command", level="warn")

        elif text == "/resume":
            _set_repo_variable("PAUSE_PUBLISHING", "false")
            notify("RESUMED", "▶️ Publishing resumed by operator command", level="success")


def _set_repo_variable(name: str, value: str) -> None:
    """Update a GitHub Actions repository variable via the API."""
    gh_pat = os.environ.get("GH_PAT", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not gh_pat or not repo:
        logger.warning("commands: GH_PAT or GITHUB_REPOSITORY not set, cannot update %s", name)
        return
    import requests
    resp = requests.patch(
        f"https://api.github.com/repos/{repo}/actions/variables/{name}",
        headers={
            "Authorization": f"Bearer {gh_pat}",
            "Accept": "application/vnd.github+json",
        },
        json={"name": name, "value": value},
        timeout=10,
    )
    if resp.status_code not in (204, 200):
        logger.error("commands: failed to set %s=%s: %s", name, value, resp.text[:200])


def run_smoke() -> None:
    """Phase 1 smoke test: verify all integrations."""
    from agent.modules.notify import notify as _notify
    _notify("PIPELINE_ONLINE", f"Smoke test started at {datetime.now(timezone.utc).isoformat()}", level="info")

    errors = []

    # 1. Gemini
    try:
        from agent.modules.llm import complete
        result = complete("Say hello in exactly 3 words.")
        logger.info("smoke: Gemini OK: %s", result[:50])
    except Exception as e:
        errors.append(f"Gemini: {e}")

    # 2. Visual Assets (Pexels or No-Key Fallback)
    pexels_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if pexels_key:
        try:
            import requests
            resp = requests.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": pexels_key},
                params={"query": "office", "per_page": 1},
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("smoke: Pexels API OK (%d results)", resp.json().get("total_results", 0))
        except Exception as e:
            errors.append(f"Pexels: {e}")
    else:
        logger.info("smoke: Pexels API key omitted — using zero-key AI image/motion fallback (Pollinations + Procedural)")

    # 3. edge-tts
    try:
        import asyncio, edge_tts, tempfile
        from pathlib import Path
        td = Path(tempfile.mkdtemp())
        async def _test():
            comm = edge_tts.Communicate("Hello world", "en-IN-NeerjaNeural")
            with open(td / "test.mp3", "wb") as f:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
        asyncio.run(_test())
        logger.info("smoke: edge-tts OK")
    except Exception as e:
        errors.append(f"edge-tts: {e}")

    # 4. ffmpeg
    try:
        import subprocess
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        if result.returncode == 0:
            logger.info("smoke: ffmpeg OK")
        else:
            errors.append("ffmpeg: non-zero exit")
    except Exception as e:
        errors.append(f"ffmpeg: {e}")

    # 5. YouTube read access
    try:
        from agent.modules.research import get_public_youtube
        svc = get_public_youtube()
        resp = svc.videos().list(part="snippet", chart="mostPopular", regionCode="IN", maxResults=1).execute()
        logger.info("smoke: YouTube API OK (%d items)", len(resp.get("items", [])))
    except Exception as e:
        errors.append(f"YouTube: {e}")

    if errors:
        msg = "Smoke test FAILED:\n" + "\n".join(f"  ❌ {e}" for e in errors)
        logger.error(msg)
        _notify("PIPELINE_ONLINE", msg, level="error")
        sys.exit(1)
    else:
        msg = "✅ All integrations OK: Gemini, Pexels, edge-tts, ffmpeg, YouTube"
        logger.info(msg)
        _notify("PIPELINE_ONLINE", msg, level="success")


def run_orchestrated_pipeline() -> None:
    """Runs coordinated multi-agent pipeline via Central Orchestrator."""
    from agent.modules.orchestration import get_orchestrator
    logger.info("=== Starting Coordinated Multi-Agent Pipeline ===")
    orch = get_orchestrator()
    result = orch.run_production_pipeline()
    logger.info("Coordinated pipeline result: Task=%s Verdict=%s", result.get("task_id"), result.get("qa_verdict"))
    print(f"Pipeline completed: {result.get('task_id')} (QA: {result.get('qa_verdict')})")


def show_comms_log() -> None:
    """Displays recent inter-agent communication records from logs/agent-comms.jsonl."""
    from agent.modules.orchestration import MessageLogger
    limit = 10
    if len(sys.argv) > 2:
        try:
            limit = int(sys.argv[2])
        except ValueError:
            if sys.argv[2] == "--limit" and len(sys.argv) > 3:
                limit = int(sys.argv[3])
    logger_instance = MessageLogger()
    messages = logger_instance.read_messages(limit=limit)
    print(f"=== Agent Communications Log (Last {len(messages)} messages) ===")
    if not messages:
        print("No communication records found in logs/agent-comms.jsonl.")
        return
    for msg in messages:
        print(f"[{msg.timestamp}] {msg.sender} -> {msg.recipient} | Action: {msg.action} | Task: {msg.task_id} | Conf: {msg.confidence}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m agent.main [pipeline|orchestrate|engage|analytics|commands|smoke|comms]")
        sys.exit(1)

    cmd = sys.argv[1]
    dispatch = {
        "pipeline": run_pipeline,
        "orchestrate": run_orchestrated_pipeline,
        "engage": run_engage,
        "analytics": run_analytics,
        "commands": run_commands,
        "smoke": run_smoke,
        "comms": show_comms_log,
    }
    fn = dispatch.get(cmd)
    if not fn:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
    fn()