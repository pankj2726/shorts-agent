"""
research.py — Trend research and competitor analysis.

Quota notes:
  - videos.list (chart=mostPopular): 1 unit/call
  - playlistItems.list: 1 unit/call  (use this, NOT search.list which costs 100!)
  - Batch up to 50 video IDs per videos.list call
"""
import logging
import os
import time
from typing import Any

import requests

from agent.modules import quota, db
from agent.modules.notify import notify

logger = logging.getLogger(__name__)


def get_youtube_service():
    """Return an authenticated YouTube Data API service (OAuth)."""
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleRequest

    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.force-ssl",
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/yt-analytics.readonly"],
    )
    creds.refresh(GoogleRequest())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def get_public_youtube():
    """Return a public (API key) YouTube service for read-only calls."""
    from googleapiclient.discovery import build
    return build("youtube", "v3", developerKey=os.environ["YT_API_KEY"], cache_discovery=False)


def fetch_trending(category_id: str = "0", region: str = "IN", max_results: int = 20) -> list[dict]:
    """
    Fetch trending videos using videos.list (chart=mostPopular).
    Cost: 1 unit per call.
    """
    if not quota.check("videos.list"):
        return []
    svc = get_public_youtube()
    try:
        resp = svc.videos().list(
            part="snippet,statistics",
            chart="mostPopular",
            regionCode=region,
            videoCategoryId=category_id,
            maxResults=max_results,
        ).execute()
        quota.consume("videos.list")
        items = resp.get("items", [])
        return [
            {
                "video_id": v["id"],
                "title": v["snippet"]["title"],
                "channel": v["snippet"]["channelTitle"],
                "views": int(v["statistics"].get("viewCount", 0)),
                "published": v["snippet"]["publishedAt"],
            }
            for v in items
        ]
    except Exception as e:
        quota.consume("videos.list")  # failed calls still cost quota
        logger.error("research: fetch_trending failed: %s", e)
        return []


def fetch_competitor_uploads(channel_id: str, max_results: int = 20) -> list[dict]:
    """
    Fetch recent uploads from a competitor channel using playlistItems.list.
    Cost: 1 unit per call (vs 100 for search.list — always use this).
    """
    # First get the uploads playlist ID
    if not quota.check("channels.list"):
        return []
    svc = get_public_youtube()
    try:
        ch_resp = svc.channels().list(part="contentDetails", id=channel_id).execute()
        quota.consume("channels.list")
        items = ch_resp.get("items", [])
        if not items:
            return []
        uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except Exception as e:
        quota.consume("channels.list")
        logger.error("research: channels.list failed for %s: %s", channel_id, e)
        return []

    if not quota.check("playlistItems.list"):
        return []
    try:
        pl_resp = svc.playlistItems().list(
            part="snippet",
            playlistId=uploads_playlist,
            maxResults=max_results,
        ).execute()
        quota.consume("playlistItems.list")
        items = pl_resp.get("items", [])
        video_ids = [i["snippet"]["resourceId"]["videoId"] for i in items]
    except Exception as e:
        quota.consume("playlistItems.list")
        logger.error("research: playlistItems.list failed: %s", e)
        return []

    # Batch video details (up to 50 IDs per call)
    return _batch_video_details(svc, video_ids)


def _batch_video_details(svc: Any, video_ids: list[str]) -> list[dict]:
    """Fetch video details in batches of 50. Cost: 1 unit per batch call."""
    results = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        if not quota.check("videos.list"):
            break
        try:
            resp = svc.videos().list(
                part="snippet,statistics",
                id=",".join(batch),
            ).execute()
            quota.consume("videos.list")
            for v in resp.get("items", []):
                results.append({
                    "video_id": v["id"],
                    "title": v["snippet"]["title"],
                    "channel": v["snippet"]["channelTitle"],
                    "views": int(v["statistics"].get("viewCount", 0)),
                    "duration": v.get("contentDetails", {}).get("duration", ""),
                    "published": v["snippet"]["publishedAt"],
                })
        except Exception as e:
            quota.consume("videos.list")
            logger.error("research: batch video details failed: %s", e)
    return results


def fetch_pytrends(keyword: str, timeframe: str = "now 7-d") -> list[str]:
    """
    Fetch related rising queries from Google Trends.
    Uses pytrends (no API key needed).
    """
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=330)  # IST offset
        pt.build_payload([keyword], cat=0, timeframe=timeframe, geo="IN", gprop="youtube")
        related = pt.related_queries()
        rising = related.get(keyword, {}).get("rising", None)
        if rising is not None and not rising.empty:
            return rising["query"].tolist()[:10]
    except Exception as e:
        logger.warning("research: pytrends failed: %s", e)
    return []


def run_research() -> dict:
    """
    Full research run: trending + competitor analysis + trends.
    Returns a dict with keys: trending, competitor_topics, trend_queries.
    """
    notify("RESEARCH_START", "Fetching trends and competitor data", level="info")
    niche = os.environ.get("CHANNEL_NICHE", "productivity")

    trending = fetch_trending()
    trend_queries = fetch_pytrends(niche)

    # Store in DB for strategy module to use
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO runs (run_type, status, details) VALUES ('research', 'done', ?)",
        (f"trending:{len(trending)} trend_queries:{len(trend_queries)}",)
    )
    conn.commit()
    conn.close()

    result = {
        "trending": trending[:10],
        "trend_queries": trend_queries,
    }
    notify("RESEARCH_DONE", f"Found {len(trending)} trending, {len(trend_queries)} trend queries", level="info")
    return result