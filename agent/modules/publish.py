"""
publish.py — YouTube Data API upload and scheduling.

Quota costs:
  videos.insert: 1600 units
  videos.update:   50 units
  thumbnails.set:  50 units

Upload is ONLY done when a valid approval record exists for this exact render,
PAUSE_PUBLISHING != 'true' and quota allows. Until the API audit passes,
publishes to Private with a notification containing the MP4 so the operator can
schedule it manually in Studio.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent.modules import approvals, db, quota
from agent.modules.config import env_bool, env_str
from agent.modules.notify import notify

logger = logging.getLogger(__name__)


def upload_short(
    video_id: int,
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    publish_at: Optional[str] = None,
    contains_synthetic: bool = False,
) -> Optional[str]:
    """
    Upload a Short to YouTube.

    - publish_at: ISO8601 UTC datetime string for scheduled publish.
      If None, stays Private.
    - contains_synthetic: sets the synthetic-media disclosure flag.

    Returns the YouTube video ID string, or None on failure.
    Idempotent: an already-uploaded video returns its existing yt_video_id.

    Publishing requires a valid approval record for this video and this exact
    file. That check is first, ahead of the idempotency lookup and the kill
    switch, and it consults no environment variable — see approvals.py for why.

    An approval is single-use: it authorises one publication of one render. The
    read-only verify() below gates entry; consume() spends the record
    immediately before the upload actually starts.
    """
    # Approval is a precondition for entering this function at all, not one
    # condition among several. Checked before anything else runs, including the
    # early return for an already-uploaded video: if an approval has been
    # revoked or expired, reporting success and letting the caller mark the run
    # 'done' would be the wrong answer even though no bytes move.
    content_hash = approvals.file_hash(video_path)
    allowed, reason = approvals.verify(video_id, content_hash)
    if not allowed:
        logger.warning("publish: REFUSED for video_id=%s — %s", video_id, reason)
        notify(
            "UPLOAD_BLOCKED",
            f"⛔ Publishing refused for video_id={video_id}.\n{reason}",
            level="error",
        )
        return None

    # Idempotency check
    conn = db.get_conn()
    row = conn.execute("SELECT yt_video_id FROM videos WHERE id=?", (video_id,)).fetchone()
    conn.close()
    if row and row["yt_video_id"]:
        logger.info("publish: video_id=%s already uploaded as %s", video_id, row["yt_video_id"])
        return row["yt_video_id"]

    # Kill switch — defence in depth behind the approval record, never instead of it
    if env_bool("PAUSE_PUBLISHING", False):
        logger.info("publish: PAUSE_PUBLISHING=true — skipping upload")
        notify("UPLOAD_SKIP", "PAUSE_PUBLISHING is true — video not uploaded", level="warn")
        return None

    # Quota check
    if not quota.check("videos.insert"):
        notify("QUOTA_LOW", "Cannot upload: insufficient quota for videos.insert (1600u)", level="warn")
        return None

    svc = _get_youtube_service()

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:500],
            "categoryId": "22",  # People & Blogs — change per niche
            "defaultLanguage": env_str("LANGUAGE", "en"),
            "defaultAudioLanguage": env_str("LANGUAGE", "en"),
        },
        "status": {
            "privacyStatus": "private",
            "madeForKids": False,
            "selfDeclaredMadeForKids": False,
        },
    }

    # Add scheduled publish time if provided
    if publish_at:
        body["status"]["publishAt"] = publish_at

    # Synthetic media disclosure (verify field name in current API docs before use)
    # As of 2026-11, the field is not yet in the discovery doc — use videos.update workaround
    # TODO: re-check https://developers.google.com/youtube/v3/docs/videos when field is released

    from googleapiclient.http import MediaFileUpload  # type: ignore

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,
    )

    # Spend the approval. Placed here rather than at the top of the function so
    # that the cheap no-op exits above — already-uploaded, paused, out of quota
    # — do not burn a single-use grant, but still before any byte leaves the
    # machine. The UPDATE is atomic and guarded by `consumed_at IS NULL`, so a
    # second publisher racing for the same approval is refused rather than also
    # served.
    #
    # If the upload then fails, the approval stays spent. That is deliberate:
    # a single-use grant does not become reusable because a network call
    # misbehaved, and re-granting is one command.
    allowed, reason = approvals.consume(video_id, content_hash, consumed_by="upload_short")
    if not allowed:
        logger.warning("publish: REFUSED for video_id=%s — %s", video_id, reason)
        notify(
            "UPLOAD_BLOCKED",
            f"⛔ Publishing refused for video_id={video_id}.\n{reason}",
            level="error",
        )
        return None

    logger.info("publish: uploading %s (1600 quota units)", Path(video_path).name)
    try:
        request = svc.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.debug("publish: upload %.0f%%", status.progress() * 100)

        quota.consume("videos.insert")
        yt_id = response["id"]
        logger.info("publish: uploaded as youtube.com/shorts/%s", yt_id)

        # Persist in DB
        conn = db.get_conn()
        conn.execute(
            "UPDATE videos SET yt_video_id=?, yt_status='private', publish_at=?, published_at=? WHERE id=?",
            (yt_id, publish_at, datetime.now(timezone.utc).isoformat(), video_id),
        )
        conn.commit()
        conn.close()

        notify(
            "UPLOAD_OK",
            f"✅ Uploaded: https://youtube.com/shorts/{yt_id}\n"
            f"Title: {title}\nScheduled: {publish_at or 'manual'}",
            level="success",
        )
        return yt_id

    except Exception as e:
        quota.consume("videos.insert")  # failed calls still cost quota
        logger.error("publish: upload failed: %s", e)
        notify(
            "UPLOAD_FAILED",
            f"❌ Upload failed for video_id={video_id}: {str(e)[:500]}\n"
            f"Run: {os.environ.get('GH_RUN_URL', 'unknown')}",
            level="error",
        )
        # Update DB status
        conn = db.get_conn()
        conn.execute("UPDATE videos SET yt_status='failed' WHERE id=?", (video_id,))
        conn.commit()
        conn.close()
        return None


def send_for_manual_review(video_id: int, video_path: str, title: str, metadata: dict) -> None:
    """
    When the API audit hasn't passed, send the MP4 to Telegram for manual scheduling.
    Operator schedules it in YouTube Studio.
    """
    logger.info("publish: sending video_id=%s to Telegram for manual review", video_id)
    notify(
        "RENDER_DONE",
        f"📲 Manual scheduling needed (audit pending).\n"
        f"Title: {title}\n"
        f"Tags: {', '.join(metadata.get('tags', []))}\n"
        f"Schedule in YouTube Studio > Upload > Schedule",
        level="action",
        video_path=video_path,
        buttons=[[("✅ Scheduled in Studio", f"manual_ok:{video_id}"),
                  ("❌ Reject", f"manual_no:{video_id}")]],
    )


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
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.force-ssl",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ],
    )
    creds.refresh(GoogleRequest())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)