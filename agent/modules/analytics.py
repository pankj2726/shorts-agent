"""
analytics.py — Analyst agent: daily metrics + weekly learning report.
"""
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from agent.modules import db, llm
from agent.modules.notify import notify

logger = logging.getLogger(__name__)


def _get_analytics_service():
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleRequest
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
    )
    creds.refresh(GoogleRequest())
    return build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)


def run_daily() -> dict:
    """Fetch daily metrics for the last 7 days and send a digest."""
    notify("ANALYTICS_START", "Fetching daily metrics", level="info")
    channel_id = os.environ.get("YT_CHANNEL_ID", "")
    if not channel_id:
        logger.warning("analytics: YT_CHANNEL_ID not set")
        return {}

    end_date = date.today().isoformat()
    start_date = (date.today() - timedelta(days=7)).isoformat()

    try:
        svc = _get_analytics_service()
        resp = svc.reports().query(
            ids=f"channel=={channel_id}",
            startDate=start_date,
            endDate=end_date,
            metrics="views,averageViewDuration,averageViewPercentage,estimatedMinutesWatched,shares,subscribersGained,likes,comments",
            dimensions="day",
            sort="day",
        ).execute()
    except Exception as e:
        logger.error("analytics: daily query failed: %s", e)
        notify("ANALYTICS_ERROR", f"Daily metrics failed: {str(e)[:300]}", level="error")
        return {}

    rows = resp.get("rows", [])
    summary = {
        "period": f"{start_date} to {end_date}",
        "rows": rows,
        "total_views": sum(r[1] for r in rows) if rows else 0,
    }

    from agent.modules import quota as q
    quota_summary = q.daily_summary()

    _send_daily_digest(summary, quota_summary)
    return summary


def run_weekly() -> str:
    """Generate a weekly learning report and write it to learnings.md."""
    notify("ANALYTICS_WEEKLY_START", "Generating weekly learning report", level="info")
    channel_id = os.environ.get("YT_CHANNEL_ID", "")
    if not channel_id:
        return ""

    end_date = date.today().isoformat()
    start_date = (date.today() - timedelta(days=28)).isoformat()

    try:
        svc = _get_analytics_service()
        resp = svc.reports().query(
            ids=f"channel=={channel_id}",
            startDate=start_date,
            endDate=end_date,
            metrics="views,averageViewDuration,averageViewPercentage,shares,subscribersGained,likes,comments",
            dimensions="video",
            sort="-views",
            maxResults=20,
        ).execute()
    except Exception as e:
        logger.error("analytics: weekly query failed: %s", e)
        return ""

    rows = resp.get("rows", [])
    # Get titles from DB for context
    conn = db.get_conn()
    db_videos = {r["yt_video_id"]: r["title"] for r in
                 conn.execute("SELECT yt_video_id, title FROM videos WHERE yt_video_id IS NOT NULL").fetchall()}
    conn.close()

    video_data = []
    for row in rows:
        vid_id = row[0]
        video_data.append({
            "video_id": vid_id,
            "title": db_videos.get(vid_id, "Unknown"),
            "views": row[1],
            "avg_view_duration_s": row[2],
            "avg_view_pct": row[3],
            "shares": row[4],
            "subs_gained": row[5],
        })

    # Flag underperformers (< 40% avg viewed after 48h)
    underperformers = [v for v in video_data if v["avg_view_pct"] < 40]

    # Generate learning bullets via LLM
    prompt = f"""
You are the Analyst for a YouTube Shorts channel. Based on the last 28 days of performance data,
write exactly 5 specific, actionable rules that the Strategist and Scriptwriter must follow next week.

Performance data (sorted by views):
{json.dumps(video_data, indent=2)}

Underperforming videos (< 40% average viewed):
{json.dumps(underperformers, indent=2)}

Rules about success signals to look for:
- 3-second hold above 70% = good hook
- Average percentage viewed above 80% (replays can push it over 100%) = strong content
- Rising shares and comment rate week over week = virality signals
- Do NOT judge a Short before it is 48 hours old

Output: a numbered list of exactly 5 specific rules. Each rule must reference actual video titles or
data patterns from the data above. No generic advice.
"""
    try:
        bullets = llm.complete(prompt, temperature=0.3)
    except Exception as e:
        logger.error("analytics: weekly LLM failed: %s", e)
        bullets = "LLM unavailable this week."

    week = f"{date.today().isocalendar().year}-W{date.today().isocalendar().week:02d}"
    report = f"""# Weekly Learning Report — {week}

Generated: {datetime.now(timezone.utc).isoformat()}

## Top videos (last 28 days)
{json.dumps(video_data[:5], indent=2)}

## Underperformers to avoid
{json.dumps(underperformers, indent=2)}

## 5 rules for next week
{bullets}
"""
    # Write to learnings.md
    learnings_path = Path(__file__).parent.parent.parent / "learnings.md"
    learnings_path.write_text(report, encoding="utf-8")

    # Save bullets to DB
    conn = db.get_conn()
    for bullet in bullets.strip().split("\n"):
        if bullet.strip():
            conn.execute(
                "INSERT INTO learnings (week, bullet) VALUES (?, ?)",
                (week, bullet.strip()),
            )
    conn.commit()
    conn.close()

    notify("WEEKLY_REPORT", f"📊 Weekly report for {week}\n\n{bullets[:1000]}", level="info")
    return report


def _send_daily_digest(summary: dict, quota_summary: dict) -> None:
    total_views = summary.get("total_views", 0)
    q_used = quota_summary.get("used", "?")
    q_rem = quota_summary.get("remaining", "?")
    text = (
        f"📅 Daily Digest\n"
        f"Period: {summary.get('period', '?')}\n"
        f"Total views (7d): {total_views:,}\n"
        f"Quota used today: {q_used}u / remaining: {q_rem}u"
    )
    notify("DAILY_DIGEST", text, level="info")