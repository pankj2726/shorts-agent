"""
notify.py — Telegram + generic webhook notifications.
RULES:
  - Never raises (a notification failure must not stop the pipeline).
  - Never includes secrets, tokens, or full API responses.
  - Masks tokens in log output.
  - Keeps callback_data under 64 bytes.
  - Stays under ~1 msg/s per chat.
"""
import os
import hmac
import hashlib
import json
import time
import logging
import requests

logger = logging.getLogger(__name__)

def _load_env_file() -> None:
    from pathlib import Path
    candidates = [
        Path(r"C:\Secrets\shorts-agent.env"),
        Path(r"C:\Secrets\.env"),
        Path(__file__).parent.parent.parent / ".env",
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

_last_send_ts: float = 0.0
_MIN_INTERVAL = 1.1  # seconds between Telegram messages


def _rate_limit() -> None:
    global _last_send_ts
    now = time.time()
    gap = _MIN_INTERVAL - (now - _last_send_ts)
    if gap > 0:
        time.sleep(gap)
    _last_send_ts = time.time()


def notify(
    event: str,
    text: str,
    level: str = "info",
    buttons: list | None = None,
    video_path: str | None = None,
) -> None:
    """
    Send a status notification to Telegram and optionally to a generic webhook.

    Args:
        event:      Short event name, e.g. "PIPELINE_ONLINE".
        text:       Human-readable detail (truncated to 3500 chars for Telegram).
        level:      info | warn | error | success | action | critical
        buttons:    List of rows, each row a list of (label, callback_data) tuples.
                    callback_data must be ≤ 64 bytes.
        video_path: Local path to an MP4 to attach (≤ 50 MB). Sent via sendVideo.
    """
    _send_telegram(event, text, level, buttons, video_path)
    _send_webhook(event, text, level)


# ─── Telegram ────────────────────────────────────────────────────────────────

def _send_telegram(
    event: str,
    text: str,
    level: str,
    buttons: list | None,
    video_path: str | None,
) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("notify: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping")
        return

    base_url = f"https://api.telegram.org/bot{token}"
    icon = {"info": "ℹ️", "warn": "⚠️", "error": "❌", "success": "✅",
            "action": "🔔", "critical": "🚨"}.get(level, "📢")
    caption = f"{icon} [{level.upper()}] {event}\n{text[:3500]}"

    # Build inline keyboard
    reply_markup = None
    if buttons:
        kb = []
        for row in buttons:
            kb_row = []
            for label, cb_data in row:
                # Enforce 64-byte limit
                cb_data = cb_data[:64]
                kb_row.append({"text": label, "callback_data": cb_data})
            kb.append(kb_row)
        reply_markup = {"inline_keyboard": kb}

    _rate_limit()
    try:
        if video_path and os.path.isfile(video_path):
            fsize = os.path.getsize(video_path)
            if fsize > 50 * 1024 * 1024:
                logger.warning("notify: video %s is > 50 MB, sending text only", video_path)
                _send_text(base_url, chat_id, caption, reply_markup)
            else:
                with open(video_path, "rb") as fh:
                    data: dict = {"chat_id": chat_id, "caption": caption[:1024],
                                  "disable_notification": False}
                    if reply_markup:
                        data["reply_markup"] = json.dumps(reply_markup)
                    resp = requests.post(
                        f"{base_url}/sendVideo",
                        data=data,
                        files={"video": fh},
                        timeout=120,
                    )
                    resp.raise_for_status()
        else:
            _send_text(base_url, chat_id, caption, reply_markup)
    except Exception as exc:
        # Log but never re-raise
        logger.error("notify: Telegram send failed: %s", exc)


def _send_text(base_url: str, chat_id: str, text: str, reply_markup: dict | None) -> None:
    body: dict = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        body["reply_markup"] = reply_markup
    resp = requests.post(f"{base_url}/sendMessage", json=body, timeout=15)
    if resp.status_code != 200:
        logger.warning("notify: Telegram response error: %s", resp.text)
    resp.raise_for_status()


def poll_updates(offset: int = 0, timeout: int = 30) -> list[dict]:
    """
    Long-poll Telegram getUpdates. Returns a list of update dicts.
    Used by the approval flow and command handler.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return []
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": offset, "timeout": timeout},
            timeout=timeout + 5,
        )
        resp.raise_for_status()
        return resp.json().get("result", [])
    except Exception as exc:
        logger.error("notify: poll_updates failed: %s", exc)
        return []


def answer_callback(callback_query_id: str, text: str = "") -> None:
    """Acknowledge an inline-keyboard button tap."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text[:200]},
            timeout=10,
        )
    except Exception as exc:
        logger.error("notify: answerCallbackQuery failed: %s", exc)


# ─── Generic webhook ─────────────────────────────────────────────────────────

def _send_webhook(event: str, text: str, level: str) -> None:
    url = os.environ.get("WEBHOOK_URL", "")
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not url:
        return
    try:
        payload = json.dumps({
            "event": event,
            "level": level,
            "text": text[:4000],
            "ts": int(time.time()),
        })
        sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        requests.post(
            url,
            data=payload,
            timeout=15,
            headers={
                "Content-Type": "application/json",
                "X-Signature": f"sha256={sig}",
            },
        )
    except Exception as exc:
        logger.error("notify: webhook send failed: %s", exc)
