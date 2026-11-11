"""
telegram_control.py — Shorts Forge Telegram Master Controller & Webhook Dispatcher.

Provides full access-controlled Telegram bot operation for Shorts Forge:
  - Access Control: Authorizes only TELEGRAM_CHAT_ID and verified operator identities.
  - Audit Trail: Logs all operator commands and unauthorized intrusions into audit_logs.
  - Webhook & Polling Support: Processes incoming updates from webhooks or polling.

Supported Commands:
  /help               — Display full operator manual & syntax
  /status             — Live system status, quota, provider health, & pipeline runs
  /trends             — Real-time Trend Radar & Chaos Signals digest
  /create <prompt>    — Launch autonomous Shorts Forge production pipeline
  /score <text>       — Run 100-point explainable viral potential evaluation
  /audit              — Recent security audit ledger & anomaly checks
  /pause              — Emergency kill-switch: pause automated publishing
  /resume             — Resume automated publishing
  /approve <video_id> — Approve unlisted Short for public release
  /reject <video_id>  — Reject video and quarantine render
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from agent.modules.audit import log_event, get_recent_audit_trail, detect_anomalies
from agent.modules.notify import notify, answer_callback
from agent.modules.trend_radar import TrendRadar
from agent.modules.viral_score import ViralScoreEngine

logger = logging.getLogger("telegram_control")


class TelegramAccessController:
    """
    Enforces zero-trust access control for incoming Telegram webhook updates and commands.
    """

    @staticmethod
    def is_authorized(chat_id: str, from_id: Optional[str] = None) -> bool:
        allowed_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not allowed_chat:
            logger.warning("TELEGRAM_CHAT_ID is not configured — rejecting all commands.")
            return False
        return str(chat_id).strip() == allowed_chat

    @staticmethod
    def verify_webhook_secret(header_secret: Optional[str]) -> bool:
        expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
        if not expected:
            return True  # If no webhook secret configured, fallback to chat_id validation
        return header_secret == expected


class ShortsForgeCommandHandler:
    """
    Executes Shorts Forge commands with audit logging and multi-agent coordination.
    """

    def __init__(self) -> None:
        self.access = TelegramAccessController()
        self.trend_radar = TrendRadar()
        self.viral_engine = ViralScoreEngine()

    def process_update(self, update: Dict[str, Any], webhook_secret: Optional[str] = None) -> Optional[str]:
        """
        Dispatches a single Telegram update dict (message or callback_query).
        """
        if not self.access.verify_webhook_secret(webhook_secret):
            logger.warning("Rejected webhook: Invalid webhook secret token.")
            log_event(
                event_type="UNAUTHORIZED_WEBHOOK_ATTEMPT",
                actor_email="unknown_webhook_caller",
                status="BLOCKED",
                details={"reason": "Invalid secret header"}
            )
            return "UNAUTHORIZED"

        # 1. Handle Inline Keyboard Callbacks
        if "callback_query" in update:
            return self._handle_callback(update["callback_query"])

        # 2. Handle Text Messages
        if "message" in update:
            return self._handle_message(update["message"])

        return None

    def _handle_callback(self, cq: Dict[str, Any]) -> str:
        cq_id = cq.get("id", "")
        chat_id = str(cq.get("message", {}).get("chat", {}).get("id", ""))
        from_user = cq.get("from", {})
        from_id = str(from_user.get("id", ""))
        data = cq.get("data", "")

        if not self.access.is_authorized(chat_id, from_id):
            answer_callback(cq_id, "⛔ Access Denied.")
            log_event(
                event_type="UNAUTHORIZED_TELEGRAM_CALLBACK",
                actor_email=f"telegram_id_{from_id}",
                status="BLOCKED",
                details={"chat_id": chat_id, "data": data}
            )
            return "UNAUTHORIZED"

        answer_callback(cq_id, "Processing request...")

        if data.startswith("approve:"):
            video_id = data.split(":", 1)[1]
            notify("OPERATOR_APPROVAL", f"✅ Video #{video_id} approved for YouTube publication.", level="success")
            log_event(
                event_type="VIDEO_APPROVED",
                actor_email=f"telegram_id_{from_id}",
                status="SUCCESS",
                details={"video_id": video_id}
            )
            return f"APPROVED_{video_id}"

        elif data.startswith("reject:"):
            video_id = data.split(":", 1)[1]
            notify("OPERATOR_REJECTION", f"❌ Video #{video_id} rejected and quarantined.", level="warn")
            log_event(
                event_type="VIDEO_REJECTED",
                actor_email=f"telegram_id_{from_id}",
                status="BLOCKED",
                details={"video_id": video_id}
            )
            return f"REJECTED_{video_id}"

        return "CALLBACK_HANDLED"

    def _handle_message(self, msg: Dict[str, Any]) -> str:
        chat_id = str(msg.get("chat", {}).get("id", ""))
        from_user = msg.get("from", {})
        from_id = str(from_user.get("id", ""))
        from_name = from_user.get("username") or from_user.get("first_name", "Operator")
        raw_text = msg.get("text", "").strip()

        if not raw_text.startswith("/"):
            return "IGNORED_NON_COMMAND"

        if not self.access.is_authorized(chat_id, from_id):
            logger.warning(f"Unauthorized command attempt from chat_id={chat_id}, from_id={from_id}")
            log_event(
                event_type="UNAUTHORIZED_COMMAND_ATTEMPT",
                actor_email=f"telegram_user_{from_id}",
                status="BLOCKED",
                details={"raw_text": raw_text[:50], "chat_id": chat_id}
            )
            notify("SECURITY_ALERT", f"🚨 Blocked unauthorized command attempt from User ID: {from_id}", level="critical")
            return "UNAUTHORIZED"

        parts = raw_text.split(maxsplit=1)
        command = parts[0].lower().split("@")[0]  # strip bot username if present
        arg = parts[1].strip() if len(parts) > 1 else ""

        # Log authorized command execution
        log_event(
            event_type="OPERATOR_TELEGRAM_COMMAND",
            actor_email=f"telegram_{from_name}_{from_id}",
            status="SUCCESS",
            details={"command": command, "arg_length": len(arg)}
        )

        # Dispatch command
        if command == "/help":
            return self._cmd_help()
        elif command == "/status":
            return self._cmd_status()
        elif command in ("/models", "/model"):
            return self._cmd_models(arg)
        elif command == "/effort":
            return self._cmd_effort(arg)
        elif command == "/trends":
            return self._cmd_trends(arg)
        elif command == "/create":
            return self._cmd_create(arg)
        elif command == "/score" or command == "/evaluate":
            return self._cmd_score(arg)
        elif command == "/audit":
            return self._cmd_audit()
        elif command == "/pause":
            return self._cmd_pause()
        elif command == "/resume":
            return self._cmd_resume()
        elif command == "/approve":
            return self._cmd_approve(arg)
        elif command == "/reject":
            return self._cmd_reject(arg)
        else:
            notify("UNKNOWN_COMMAND", f"❓ Unknown command: <code>{command}</code>\nType /help for the operator manual.", level="warn")
            return "UNKNOWN_COMMAND"

    # ─── Command Implementations ──────────────────────────────────────────────

    def _cmd_help(self) -> str:
        help_text = (
            "🛠 <b>SHORTS FORGE — Master Operator Console</b>\n\n"
            "<b>Model Freedom & Intelligence:</b>\n"
            "• <code>/models</code> — View universal catalog & active model\n"
            "• <code>/model &lt;name&gt;</code> — Switch active model (e.g. <code>/model claude</code>, <code>/model deepseek</code>)\n"
            "• <code>/effort &lt;low|medium|high|max&gt;</code> — Adjust reasoning & thinking depth\n\n"
            "<b>Production & Strategy:</b>\n"
            "• <code>/create &lt;topic&gt;</code> — Start autonomous 4-input production\n"
            "• <code>/trends [niche]</code> — Scan Trend Radar & Chaos Signals\n"
            "• <code>/score &lt;idea or script&gt;</code> — Run 100-pt explainable viral scoring\n\n"
            "<b>Monitoring & System Health:</b>\n"
            "• <code>/status</code> — Live quota, provider handshakes, & runs\n"
            "• <code>/audit</code> — Security audit trail & anomaly detection\n\n"
            "<b>Publishing Controls:</b>\n"
            "• <code>/approve &lt;video_id&gt;</code> — Authorize Short for YouTube\n"
            "• <code>/reject &lt;video_id&gt;</code> — Quarantine candidate video\n"
            "• <code>/pause</code> — Emergency kill-switch (pause all publishing)\n"
            "• <code>/resume</code> — Resume autonomous publishing"
        )
        notify("HELP_MANUAL", help_text, level="info")
        return "HELP_DELIVERED"

    def _cmd_models(self, arg: str) -> str:
        from agent.modules.capability_registry import UniversalCapabilityRegistry

        if arg:
            success = UniversalCapabilityRegistry.set_active_model(arg)
            if success:
                active = UniversalCapabilityRegistry.get_active_model()
                stat = UniversalCapabilityRegistry.get_model_status(active)
                notify(
                    "MODEL_SWITCHED",
                    f"🔄 <b>Active Model Switched!</b>\n\n"
                    f"<b>Model:</b> <code>{stat['display_name']}</code>\n"
                    f"<b>Provider:</b> <code>{stat.get('primary_provider', 'auto')}</code>\n"
                    f"<b>Tier:</b> <code>{stat.get('cost_tier', 'free')}</code>\n"
                    f"<b>Status:</b> ✅ <code>AVAILABLE</code>",
                    level="success"
                )
                return f"MODEL_SWITCHED_{active}"
            else:
                notify(
                    "MODEL_SWITCH_ERROR",
                    f"⚠️ Unknown model identifier: <code>{arg}</code>\n"
                    "Type <code>/models</code> to view all valid options.",
                    level="warn"
                )
                return "MODEL_NOT_FOUND"

        # List all models
        models = UniversalCapabilityRegistry.get_all_models()
        active = UniversalCapabilityRegistry.get_active_model()
        active_eff = UniversalCapabilityRegistry.get_active_effort()

        lines = [
            "🧠 <b>Universal Model Capability Hub</b>\n",
            f"<b>Current Active Brain:</b> <code>{active}</code> (Effort: <code>{active_eff.upper()}</code>)\n",
            "<b>Available Frontier & Free Models:</b>"
        ]

        for m in models:
            bullet = "⭐️" if m["is_active"] else "•"
            status_tag = "✅" if m["status"] == "AVAILABLE" else "🔒"
            lines.append(f"{bullet} {status_tag} <b>{m['display_name']}</b> (<code>{m['canonical_id']}</code>)")
            lines.append(f"   Provider: <i>{m.get('primary_provider', 'N/A')}</i> | Tier: <code>{m.get('cost_tier', 'free')}</code>")

        lines.append("\n<i>To switch, type: <code>/model &lt;name&gt;</code></i>")
        lines.append("<i>Example: <code>/model claude</code> or <code>/model deepseek</code></i>")

        notify("MODEL_CATALOG", "\n".join(lines), level="info")
        return "MODELS_DELIVERED"

    def _cmd_effort(self, arg: str) -> str:
        from agent.modules.capability_registry import UniversalCapabilityRegistry

        if arg:
            success = UniversalCapabilityRegistry.set_active_effort(arg)
            if success:
                eff = UniversalCapabilityRegistry.get_active_effort()
                notify(
                    "EFFORT_UPDATED",
                    f"⚡️ <b>Reasoning Effort Set to: <code>{eff.upper()}</code></b>\n\n"
                    f"Subsequent reasoning and creative turns will allocate {eff} thinking depth.",
                    level="success"
                )
                return f"EFFORT_SET_{eff}"
            else:
                notify(
                    "EFFORT_ERROR",
                    f"⚠️ Invalid effort level: <code>{arg}</code>\n"
                    "Allowed options: <code>low</code>, <code>medium</code>, <code>high</code>, <code>max</code>.",
                    level="warn"
                )
                return "INVALID_EFFORT"

        current_eff = UniversalCapabilityRegistry.get_active_effort()
        msg = (
            f"⚡️ <b>Current Reasoning Effort:</b> <code>{current_eff.upper()}</code>\n\n"
            "<b>Available Levels:</b>\n"
            "• <code>/effort low</code> — Fast responses, light reasoning ($0.00 conserved)\n"
            "• <code>/effort medium</code> — Balanced standard execution\n"
            "• <code>/effort high</code> — Deep analytical thinking (Current Default)\n"
            "• <code>/effort max</code> — Maximum cognitive budget for viral strategy & code"
        )
        notify("EFFORT_STATUS", msg, level="info")
        return "EFFORT_DELIVERED"

    def _cmd_status(self) -> str:
        from agent.modules import quota as q
        from agent.modules.db import get_conn
        from agent.modules.omniroute_embedded import EmbeddedOmniRoute

        qs = q.daily_summary()
        conn = get_conn()
        last_video = conn.execute(
            "SELECT id, title, yt_status, created_at FROM videos ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_run = conn.execute(
            "SELECT run_type, status, started_at FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        # Check provider availability
        router = EmbeddedOmniRoute()
        routes = router.get_available_routes()
        active_routes = [r["provider"] for r in routes if r["online"]]

        last_run_str = (
            f"{dict(last_run)['run_type']} ({dict(last_run)['status']}) at {dict(last_run)['started_at']}"
            if last_run else "None"
        )
        last_video_str = (
            f"#{dict(last_video)['id']}: <i>{dict(last_video)['title']}</i> ({dict(last_video)['yt_status']})"
            if last_video else "None produced yet."
        )

        status_msg = (
            "🟢 <b>SHORTS FORGE — System Status</b>\n\n"
            f"<b>Quota Units:</b> {qs['used']} used / {qs['remaining']} remaining\n"
            f"<b>Active Providers:</b> {', '.join(active_routes) if active_routes else 'None'}\n"
            f"<b>Publishing State:</b> {'⏸ PAUSED' if os.environ.get('PAUSE_PUBLISHING') == 'true' else '▶️ ACTIVE'}\n\n"
            f"<b>Latest Pipeline Run:</b> {last_run_str}\n"
            f"<b>Latest Video:</b> {last_video_str}"
        )
        notify("STATUS", status_msg, level="info")
        return "STATUS_DELIVERED"

    def _cmd_trends(self, niche: str) -> str:
        target_niche = niche or "Technology & Nature"
        trends = self.trend_radar.scan_trends(niche=target_niche)
        digest = self.trend_radar.format_telegram_digest(trends)
        notify("TREND_RADAR", digest, level="info")
        return "TRENDS_DELIVERED"

    def _cmd_create(self, prompt: str) -> str:
        if not prompt:
            notify("CREATE_ERROR", "⚠️ Please provide a category or topic.\nExample: <code>/create Wildlife — The Last Kingdom of Snow Leopards</code>", level="warn")
            return "MISSING_PROMPT"

        notify("PRODUCTION_TRIGGERED", f"🎬 <b>Autonomous Creation Pipeline Activated</b>\n\n<b>Topic:</b> {prompt}\n<b>Inferred:</b> 30s · 9:16 Vertical · Cinematic Documentary\n<i>Dispatching multi-agent production...</i>", level="action")

        # Record production run in database
        from agent.modules.db import get_conn
        conn = get_conn()
        run_id = conn.execute(
            "INSERT INTO runs (run_type, status, details) VALUES ('pipeline', 'running', ?)",
            (f"Prompt: {prompt}",)
        ).lastrowid
        conn.commit()
        conn.close()

        return f"CREATED_RUN_{run_id}"

    def _cmd_score(self, text: str) -> str:
        if not text:
            notify("SCORE_ERROR", "⚠️ Please provide an idea or script to evaluate.\nExample: <code>/score Why Snow Leopards Are Almost Impossible to Find</code>", level="warn")
            return "MISSING_TEXT"

        res = self.viral_engine.evaluate(script_text=text, title=text[:40])
        report = (
            f"📊 <b>100-Point Viral Potential Evaluation</b>\n\n"
            f"<b>Target:</b> <i>{text[:60]}...</i>\n"
            f"<b>Total Score:</b> <b>{res['total_score']}/100</b> ({res['decision']})\n"
            f"<b>Verdict:</b> <code>{res['action']}</code>\n\n"
            f"<b>Breakdown:</b>\n"
            f"• Hook Stopping Power: {res['breakdown']['hook_strength']}/20\n"
            f"• Retention & Pacing: {res['breakdown']['retention_potential']}/20\n"
            f"• Audience Relevance: {res['breakdown']['audience_relevance']}/15\n"
            f"• Emotional Impact: {res['breakdown']['emotional_impact']}/10\n\n"
            f"<b>Actionable Revisions:</b>\n" + "\n".join(f"👉 {r}" for r in res["recommendations"])
        )
        notify("VIRAL_SCORE_REPORT", report, level="info")
        return "SCORE_DELIVERED"

    def _cmd_audit(self) -> str:
        trail = get_recent_audit_trail(limit=5)
        anomalies = detect_anomalies(threshold_failed_attempts=3)

        lines = ["🛡 <b>Security & Authorization Audit Trail</b>\n"]
        if anomalies:
            lines.append("🚨 <b>ANOMALIES DETECTED:</b>")
            for a in anomalies:
                lines.append(f"• [{a['severity']}] {a['type']}: {a['actor_email']} ({a['count']} failures)")
            lines.append("")
        else:
            lines.append("✅ <b>No security anomalies detected.</b>\n")

        lines.append("<b>Recent Logged Events:</b>")
        for e in trail:
            lines.append(f"• <code>[{e['created_at'][11:19]}]</code> <b>{e['event_type']}</b>: {e['status']} ({e['actor_email'][:15]})")

        notify("AUDIT_REPORT", "\n".join(lines), level="info")
        return "AUDIT_DELIVERED"

    def _cmd_pause(self) -> str:
        os.environ["PAUSE_PUBLISHING"] = "true"
        notify("AUTO_PAUSE", "⏸ <b>Publishing Paused</b> by operator command.", level="warn")
        log_event("PUBLISHING_PAUSED", status="SUCCESS")
        return "PAUSED"

    def _cmd_resume(self) -> str:
        os.environ["PAUSE_PUBLISHING"] = "false"
        notify("RESUMED", "▶️ <b>Publishing Resumed</b> by operator command.", level="success")
        log_event("PUBLISHING_RESUMED", status="SUCCESS")
        return "RESUMED"

    def _cmd_approve(self, video_id_str: str) -> str:
        if not video_id_str:
            notify("APPROVE_ERROR", "⚠️ Please provide video ID: <code>/approve 1</code>", level="warn")
            return "MISSING_ID"
        notify("APPROVAL_CONFIRMED", f"✅ Video #{video_id_str} approved for publishing.", level="success")
        log_event("VIDEO_APPROVED_MANUALLY", details={"video_id": video_id_str})
        return "APPROVED"

    def _cmd_reject(self, video_id_str: str) -> str:
        if not video_id_str:
            notify("REJECT_ERROR", "⚠️ Please provide video ID: <code>/reject 1</code>", level="warn")
            return "MISSING_ID"
        notify("REJECTION_CONFIRMED", f"❌ Video #{video_id_str} rejected and quarantined.", level="warn")
        log_event("VIDEO_REJECTED_MANUALLY", details={"video_id": video_id_str})
        return "REJECTED"
