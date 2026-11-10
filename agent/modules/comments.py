"""
comments.py — Comment Agent: fetch, classify, reply.

Hard limits (verified against YouTube API rules + spam policy, Oct 2026):
  - Own channel only
  - Official API only (youtube.force-ssl scope)
  - Max 1 automated reply per top-level thread
  - Never reply to same user > 2x per video
  - Skip comments containing URLs
  - Never include links in replies
  - Never claim to be human
  - Max 25 replies per run, 50 per day
  - 20s between replies
  - Draft-only for first 2 weeks
  - Never comment on other channels
"""
import logging
import os
import re
import time
from datetime import datetime, timezone

from agent.modules import db, llm, quota
from agent.modules.notify import notify

logger = logging.getLogger(__name__)

MAX_PER_RUN = 25
MAX_PER_DAY = int(os.environ.get("MAX_REPLIES_PER_DAY", "50"))
REPLY_DELAY_S = 20
DRAFT_DAYS = 14  # draft-only for first N days

_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)


def _get_youtube_service():
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleRequest
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.force-ssl"],
    )
    creds.refresh(GoogleRequest())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _get_recent_video_ids(days: int = 14) -> list[str]:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT yt_video_id FROM videos WHERE published_at >= datetime('now', ?) AND yt_video_id IS NOT NULL",
        (f"-{days} days",),
    ).fetchall()
    conn.close()
    return [r["yt_video_id"] for r in rows]


def _replies_today() -> int:
    conn = db.get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM comments WHERE replied=1 AND date(created_at)=date('now')"
    ).fetchone()
    conn.close()
    return row[0]


def _user_reply_count(yt_video_id: str, author: str) -> int:
    conn = db.get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM comments WHERE yt_video_id=? AND author=? AND replied=1",
        (yt_video_id, author),
    ).fetchone()
    conn.close()
    return row[0]


def _already_replied(comment_id: str) -> bool:
    conn = db.get_conn()
    row = conn.execute(
        "SELECT replied FROM comments WHERE comment_id=?", (comment_id,)
    ).fetchone()
    conn.close()
    return bool(row and row["replied"])


def _in_draft_mode() -> bool:
    """Draft-only for first DRAFT_DAYS days after any video was published."""
    conn = db.get_conn()
    row = conn.execute("SELECT MIN(published_at) FROM videos WHERE published_at IS NOT NULL").fetchone()
    conn.close()
    if not row or not row[0]:
        return True  # no videos yet
    first = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
    age_days = (datetime.now(timezone.utc) - first).days
    return age_days < DRAFT_DAYS


def run_engage() -> dict:
    """Main engagement run. Returns summary dict."""
    notify("ENGAGE_START", "Fetching and processing comments", level="info")
    video_ids = _get_recent_video_ids(14)
    if not video_ids:
        notify("ENGAGE_SKIP", "No recent videos found in DB", level="info")
        return {"replied": 0, "flagged": 0, "skipped": 0}

    draft_mode = _in_draft_mode()
    svc = _get_youtube_service()

    replied = 0
    flagged = 0
    skipped = 0
    daily_count = _replies_today()

    for yt_vid in video_ids:
        if daily_count >= MAX_PER_DAY:
            break
        if replied >= MAX_PER_RUN:
            break
        comments = _fetch_comments(svc, yt_vid)
        for c in comments:
            if daily_count >= MAX_PER_DAY or replied >= MAX_PER_RUN:
                break
            result = _process_comment(svc, c, yt_vid, draft_mode)
            if result == "replied":
                replied += 1
                daily_count += 1
                time.sleep(REPLY_DELAY_S)
            elif result == "flagged":
                flagged += 1
            else:
                skipped += 1

    notify(
        "COMMENTS_REPLIED",
        f"Replied: {replied} | Flagged: {flagged} | Skipped: {skipped} | Draft mode: {draft_mode}",
        level="info",
    )
    return {"replied": replied, "flagged": flagged, "skipped": skipped}


def _fetch_comments(svc, yt_video_id: str) -> list[dict]:
    if not quota.check("commentThreads.list"):
        return []
    try:
        resp = svc.commentThreads().list(
            part="snippet",
            videoId=yt_video_id,
            maxResults=100,
            order="relevance",
        ).execute()
        quota.consume("commentThreads.list")
        items = resp.get("items", [])
        return [
            {
                "comment_id": i["id"],
                "author": i["snippet"]["topLevelComment"]["snippet"]["authorDisplayName"],
                "text": i["snippet"]["topLevelComment"]["snippet"]["textDisplay"],
                "reply_count": i["snippet"]["totalReplyCount"],
                "yt_video_id": yt_video_id,
            }
            for i in items
        ]
    except Exception as e:
        quota.consume("commentThreads.list")
        logger.error("comments: fetch failed for %s: %s", yt_video_id, e)
        return []


def _process_comment(svc, comment: dict, yt_video_id: str, draft_mode: bool) -> str:
    cid = comment["comment_id"]
    author = comment["author"]
    text = comment["text"]

    # Skip if already processed
    if _already_replied(cid):
        return "skipped"
    # Skip if comment contains a URL
    if _URL_RE.search(text):
        _save_comment(comment, "other", replied=False)
        return "skipped"
    # Skip if user already replied to twice on this video
    if _user_reply_count(yt_video_id, author) >= 2:
        return "skipped"

    # Classify and generate reply via LLM
    from pathlib import Path
    prompt_path = Path(__file__).parent.parent / "prompts" / "comment_agent.md"
    system = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""

    prompt = f"""
Video title: (from DB for video {yt_video_id})
Comment author: {author}
Comment text: {text}

Classify this comment and generate an appropriate reply. Output strict JSON only.
"""
    try:
        result = llm.complete_json(prompt, system=system)
    except Exception as e:
        logger.error("comments: LLM failed: %s", e)
        return "skipped"

    action = result.get("action", "skip")
    category = result.get("category", "other")
    reply_text = result.get("reply")

    _save_comment(comment, category)

    if action in ("hold", "reject"):
        _moderate(svc, cid, action)
        return "skipped"

    if action == "flag_human":
        notify(
            "COMMENT_FLAGGED",
            f"🚩 Sensitive comment on {yt_video_id}\n\nAuthor: {author}\n\n{text[:300]}",
            level="action",
        )
        _save_comment(comment, category, flagged=True)
        return "flagged"

    if action == "reply" and reply_text:
        # Safety checks on reply
        if _URL_RE.search(reply_text):
            logger.warning("comments: LLM reply contained a URL — skipping")
            return "skipped"
        if not quota.check("comments.insert"):
            return "skipped"

        if draft_mode:
            # Save draft only
            conn = db.get_conn()
            conn.execute(
                "UPDATE comments SET reply_text=? WHERE comment_id=?",
                (reply_text, cid),
            )
            conn.commit()
            conn.close()
            logger.info("comments: DRAFT reply for %s: %s", cid, reply_text[:80])
            return "skipped"  # don't count as replied in draft mode

        # Post reply
        try:
            svc.comments().insert(
                part="snippet",
                body={
                    "snippet": {
                        "parentId": cid,
                        "textOriginal": reply_text[:500],
                    }
                },
            ).execute()
            quota.consume("comments.insert")
            conn = db.get_conn()
            conn.execute(
                "UPDATE comments SET replied=1, reply_text=? WHERE comment_id=?",
                (reply_text, cid),
            )
            conn.commit()
            conn.close()
            return "replied"
        except Exception as e:
            quota.consume("comments.insert")
            logger.error("comments: reply failed: %s", e)
            return "skipped"

    return "skipped"


def _save_comment(comment: dict, category: str, replied: bool = False, flagged: bool = False) -> None:
    conn = db.get_conn()
    conn.execute(
        """INSERT OR IGNORE INTO comments (yt_video_id, comment_id, author, text, category, replied, flagged)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (comment["yt_video_id"], comment["comment_id"], comment["author"],
         comment["text"][:1000], category, 1 if replied else 0, 1 if flagged else 0),
    )
    conn.commit()
    conn.close()


def _moderate(svc, comment_id: str, action: str) -> None:
    if not quota.check("comments.setModerationStatus"):
        return
    status = "heldForReview" if action == "hold" else "rejected"
    try:
        svc.comments().setModerationStatus(id=comment_id, moderationStatus=status).execute()
        quota.consume("comments.setModerationStatus")
    except Exception as e:
        quota.consume("comments.setModerationStatus")
        logger.error("comments: setModerationStatus failed: %s", e)