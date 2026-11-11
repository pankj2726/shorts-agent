"""
session_router.py — Cross-Platform Sessions Routing & Unified Interaction Engine.

Allows managing, viewing, exporting, and interacting with AI sessions/conversations
across different platforms (Claude/Anthropic, Arena.AI, ChatGPT/OpenAI, OpenRouter,
Groq, OmniRoute, DeepInfra, Pollinations) from a single unified hub.

A session started on one platform (e.g. Claude) is automatically visible and
interactive across all linked platforms (e.g. Arena.AI, ChatGPT, OpenRouter),
with lossless per-turn schema translation and full provider provenance.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.modules import db
from agent.modules.conversation_store import (
    export_conversation_for_provider,
    get_conversation,
    get_conversation_messages,
    save_conversation,
    save_message,
)
from agent.modules.omniroute_embedded import EmbeddedOmniRoute

logger = logging.getLogger("SessionRouter")

LEGACY_UNIFIED_DB = Path(__file__).resolve().parent.parent / "state" / "unified_conversations.db"

PLATFORM_ALIASES: Dict[str, str] = {
    "claude": "claude",
    "anthropic": "claude",
    "claude.ai": "claude",
    "arena": "arena",
    "arena.ai": "arena",
    "lmarena": "arena",
    "chatgpt": "chatgpt",
    "openai": "chatgpt",
    "gpt": "chatgpt",
    "openrouter": "openrouter",
    "groq": "groq",
    "deepinfra": "deepinfra",
    "omniroute": "omniroute",
    "opencode": "opencode",
    "anyapi": "anyapi",
    "vertex_ai": "vertex_ai",
    "vertex": "vertex_ai",
    "gemini": "vertex_ai",
    "pollinations": "pollinations",
}

PLATFORM_TO_PROVIDER: Dict[str, str] = {
    "claude": "anthropic",
    "arena": "arena",
    "chatgpt": "openai",
    "openrouter": "openrouter",
    "groq": "groq",
    "deepinfra": "deepinfra",
    "omniroute": "omniroute",
    "opencode": "opencode",
    "anyapi": "anyapi",
    "vertex_ai": "vertex_ai",
    "pollinations": "pollinations",
}

PLATFORM_DEFAULT_MODELS: Dict[str, str] = {
    "claude": "claude-3-5-sonnet",
    "arena": "coding-router-preview",
    "chatgpt": "gpt-4o",
    "openrouter": "deepseek-r1",
    "groq": "llama-3.3-70b",
    "deepinfra": "llama-3-1-405b",
    "omniroute": "claude-3-5-sonnet",
    "opencode": "claude-3-5-sonnet",
    "pollinations": "flux-stills",
}

DEFAULT_SHARED_PLATFORMS: List[str] = ["claude", "arena", "chatgpt", "openrouter", "groq"]


def normalize_platform(platform_or_provider: Optional[str]) -> str:
    """Normalize any provider/account alias (e.g. openai -> chatgpt, anthropic -> claude)."""
    if not platform_or_provider:
        return "arena"
    cleaned = platform_or_provider.strip().lower()
    return PLATFORM_ALIASES.get(cleaned, cleaned)


def to_upstream_provider(platform: Optional[str]) -> str:
    """Map canonical UI platform name to EmbeddedOmniRoute upstream provider key."""
    norm = normalize_platform(platform)
    return PLATFORM_TO_PROVIDER.get(norm, norm)


def infer_platform_from_model(model: Optional[str]) -> Optional[str]:
    """Infer likely origin platform when only a model ID is supplied."""
    if not model:
        return None
    m = model.lower()
    if "coding-router" in m or "arena" in m or "fable" in m:
        return "arena"
    if "claude" in m:
        return "claude"
    if any(tok in m for tok in ("gpt", "o1", "o3", "chatgpt")):
        return "chatgpt"
    if "deepseek" in m or "qwen" in m:
        return "openrouter"
    if "llama" in m:
        return "groq"
    if "gemini" in m:
        return "vertex_ai"
    return None


def _extract_text(content: Any) -> str:
    """Normalize string or content-block list into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


def import_legacy_unified_db(legacy_path: Path = LEGACY_UNIFIED_DB) -> int:
    """
    Idempotently migrate rows from legacy agent/state/unified_conversations.db
    into the unified SQLite store if they are not already present.
    """
    if not legacy_path.exists():
        return 0

    imported = 0
    try:
        lconn = sqlite3.connect(str(legacy_path))
        lconn.row_factory = sqlite3.Row
        cur = lconn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        if not cur.fetchone():
            lconn.close()
            return 0

        rows = lconn.execute(
            "SELECT session_id, model, provider, role, content, effort, latency_ms FROM messages ORDER BY id ASC"
        ).fetchall()
        lconn.close()

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            sid = r["session_id"] or "default_session"
            grouped.setdefault(sid, []).append(dict(r))

        for sid, items in grouped.items():
            existing = db.get_conversation(sid)
            if existing and existing.get("message_count", 0) > 0:
                continue
            for item in items:
                role = item.get("role") or "user"
                if role not in db.VALID_ROLES:
                    role = "user"
                save_message(
                    conversation_id=sid,
                    role=role,
                    content=item.get("content") or "",
                    provider=item.get("provider"),
                    model=item.get("model"),
                    metadata={
                        "effort": item.get("effort"),
                        "latency_ms": item.get("latency_ms", 0),
                        "migrated_from": "unified_conversations.db",
                    },
                )
                imported += 1
    except Exception as exc:
        logger.warning(f"Legacy unified DB check skipped: {exc}")
    return imported


class SessionRouter:
    """Unified Cross-Platform Session Manager and Interactive Router."""

    def __init__(self, router: Optional[EmbeddedOmniRoute] = None) -> None:
        db.init_db()
        self.router = router or EmbeddedOmniRoute()

    def _compute_session_summary(self, conv: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich a raw conversation record with cross-platform visibility & routing metadata."""
        cid = conv["id"]
        msgs = db.get_messages_by_conversation(cid, order="ASC")
        meta = dict(conv.get("metadata") or {})

        routed_platforms: List[str] = []
        models_used: List[str] = []

        def _add_platform(p: Optional[str]) -> None:
            if not p or p == "client":
                return
            norm = normalize_platform(p)
            if norm not in routed_platforms:
                routed_platforms.append(norm)

        for p in meta.get("routed_platforms", []):
            _add_platform(p)

        _add_platform(conv.get("default_provider"))
        if conv.get("default_model"):
            models_used.append(conv["default_model"])
            _add_platform(infer_platform_from_model(conv["default_model"]))

        for m in msgs:
            _add_platform(m.get("provider"))
            mdl = m.get("model")
            if mdl and mdl not in models_used:
                models_used.append(mdl)
            if m.get("provider") in (None, "client") and mdl:
                _add_platform(infer_platform_from_model(mdl))

        if not routed_platforms:
            routed_platforms = [normalize_platform(conv.get("default_provider") or "arena")]

        # Cross-platform visibility: by default, sessions are shared across all connected accounts
        # so Claude sessions appear in Arena.AI, ChatGPT, etc., unless explicitly scoped.
        visible_on: List[str] = list(meta.get("visible_on_platforms") or DEFAULT_SHARED_PLATFORMS)
        for rp in routed_platforms:
            if rp not in visible_on:
                visible_on.append(rp)

        origin_platform = normalize_platform(meta.get("origin_platform") or routed_platforms[0])
        active_platform = normalize_platform(conv.get("default_provider") or routed_platforms[-1])
        active_model = conv.get("default_model") or (models_used[-1] if models_used else PLATFORM_DEFAULT_MODELS.get(active_platform, "claude-3-5-sonnet"))

        last_msg = msgs[-1] if msgs else None
        last_preview = (last_msg.get("content", "")[:120] if last_msg else "")

        return {
            "session_id": cid,
            "conversation_id": cid,
            "title": conv.get("title") or "New Conversation",
            "origin_platform": origin_platform,
            "active_platform": active_platform,
            "active_provider": conv.get("default_provider") or to_upstream_provider(active_platform),
            "active_model": active_model,
            "routed_platforms": routed_platforms,
            "visible_on_platforms": visible_on,
            "models_used": models_used,
            "message_count": len(msgs),
            "last_message_preview": last_preview,
            "created_at": conv.get("created_at"),
            "updated_at": conv.get("updated_at"),
            "metadata": meta,
        }

    def list_sessions(
        self,
        platform: Optional[str] = None,
        origin_only: bool = False,
        query: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        List sessions across platforms.
        When `platform` is given (e.g. 'arena', 'chatgpt', 'claude'), returns every
        session visible on that platform (which includes cross-platform synced sessions
        unless `origin_only=True`).
        """
        raw_convs = db.list_conversations(limit=max(limit * 3, 150), offset=0)
        sessions = [self._compute_session_summary(c) for c in raw_convs]

        if platform:
            target = normalize_platform(platform)
            if origin_only:
                sessions = [
                    s for s in sessions
                    if s["origin_platform"] == target or target in s["routed_platforms"]
                ]
            else:
                sessions = [
                    s for s in sessions
                    if target in s["visible_on_platforms"] or target in s["routed_platforms"]
                ]

        if query:
            q = query.strip().lower()
            sessions = [
                s for s in sessions
                if q in s["title"].lower()
                or q in s["session_id"].lower()
                or q in s["last_message_preview"].lower()
                or any(q in m.lower() for m in s["models_used"])
            ]

        return sessions[offset : offset + limit]

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Fetch full cross-platform session details plus chronological messages."""
        conv = get_conversation(session_id)
        if not conv:
            return None
        summary = self._compute_session_summary(conv)
        raw_msgs = db.get_messages_by_conversation(session_id, order="ASC")
        formatted_msgs: List[Dict[str, Any]] = []
        for m in raw_msgs:
            prov = m.get("provider") or summary["active_provider"]
            plat = normalize_platform(prov if prov != "client" else (infer_platform_from_model(m.get("model")) or summary["origin_platform"]))
            formatted_msgs.append({
                "id": m.get("id"),
                "session_id": session_id,
                "conversation_id": session_id,
                "role": m.get("role"),
                "content": m.get("content", ""),
                "provider": prov,
                "platform": plat,
                "model": m.get("model"),
                "thinking_content": m.get("thinking_content"),
                "created_at": m.get("created_at"),
                "metadata": m.get("metadata") or {},
            })
        summary["messages"] = formatted_msgs
        return summary

    def create_session(
        self,
        session_id: Optional[str] = None,
        title: Optional[str] = None,
        platform: str = "claude",
        model: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        visible_on_platforms: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create or import a unified cross-platform session."""
        sid = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        norm_plat = normalize_platform(platform)
        upstream_prov = to_upstream_provider(norm_plat)
        resolved_model = model or PLATFORM_DEFAULT_MODELS.get(norm_plat, "claude-3-5-sonnet")

        shared_on = list(visible_on_platforms or DEFAULT_SHARED_PLATFORMS)
        if norm_plat not in shared_on:
            shared_on.insert(0, norm_plat)

        meta = dict(metadata or {})
        meta.setdefault("origin_platform", norm_plat)
        routed = list(meta.get("routed_platforms") or [])
        if norm_plat not in routed:
            routed.append(norm_plat)
        meta["routed_platforms"] = routed
        meta["visible_on_platforms"] = shared_on

        save_conversation(
            conversation_id=sid,
            title=title,
            default_provider=upstream_prov,
            default_model=resolved_model,
            metadata=meta,
        )

        if messages:
            for msg in messages:
                role = msg.get("role", "user")
                if role not in db.VALID_ROLES:
                    role = "user"
                msg_prov = msg.get("provider") or upstream_prov
                msg_model = msg.get("model") or resolved_model
                save_message(
                    conversation_id=sid,
                    role=role,
                    content=_extract_text(msg.get("content", "")),
                    provider=msg_prov,
                    model=msg_model,
                    thinking_content=msg.get("thinking_content"),
                    metadata=msg.get("metadata") or {},
                )

        return self.get_session(sid)  # type: ignore[return-value]

    def link_platform(self, session_id: str, platform: str) -> Dict[str, Any]:
        """Ensure a session is visible and linked on an additional platform account."""
        conv = db.get_conversation(session_id)
        if not conv:
            raise KeyError(f"Session '{session_id}' not found")
        norm = normalize_platform(platform)
        meta = dict(conv.get("metadata") or {})
        routed = list(meta.get("routed_platforms") or [])
        visible = list(meta.get("visible_on_platforms") or DEFAULT_SHARED_PLATFORMS)
        if norm not in routed:
            routed.append(norm)
        if norm not in visible:
            visible.append(norm)
        meta["routed_platforms"] = routed
        meta["visible_on_platforms"] = visible
        db.update_conversation(session_id, metadata=meta)
        return self.get_session(session_id)  # type: ignore[return-value]

    def route_session(
        self,
        session_id: str,
        target_platform: str,
        target_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Switch/route an existing session to a target platform/model (e.g. route a Claude
        session onto Arena.AI or ChatGPT) and return the updated session + exported payload.
        """
        conv = db.get_conversation(session_id)
        if not conv:
            raise KeyError(f"Session '{session_id}' not found")

        norm_plat = normalize_platform(target_platform)
        upstream_prov = to_upstream_provider(norm_plat)
        resolved_model = target_model or PLATFORM_DEFAULT_MODELS.get(norm_plat, "claude-3-5-sonnet")

        meta = dict(conv.get("metadata") or {})
        meta.setdefault("origin_platform", normalize_platform(conv.get("default_provider") or norm_plat))
        routed = list(meta.get("routed_platforms") or [])
        if norm_plat not in routed:
            routed.append(norm_plat)
        visible = list(meta.get("visible_on_platforms") or DEFAULT_SHARED_PLATFORMS)
        if norm_plat not in visible:
            visible.append(norm_plat)
        meta["routed_platforms"] = routed
        meta["visible_on_platforms"] = visible

        db.update_conversation(
            session_id,
            default_provider=upstream_prov,
            default_model=resolved_model,
            metadata=meta,
        )

        exported = self.export_session(session_id, target_platform=norm_plat, target_model=resolved_model)
        session = self.get_session(session_id)
        return {"session": session, "routed_to": norm_plat, "model": resolved_model, "export_payload": exported}

    def export_session(
        self,
        session_id: str,
        target_platform: str = "chatgpt",
        target_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Export a session in the target platform's native format (ChatGPT, Claude, Arena, Markdown)."""
        conv = db.get_conversation(session_id)
        if not conv:
            raise KeyError(f"Session '{session_id}' not found")

        norm = normalize_platform(target_platform)
        if norm == "markdown":
            msgs = db.get_messages_by_conversation(session_id, order="ASC")
            lines = [f"# {conv.get('title') or session_id}\n"]
            for m in msgs:
                plat_tag = m.get("provider") or "unknown"
                mdl_tag = m.get("model") or ""
                lines.append(f"**{m['role'].upper()}** _({plat_tag} / {mdl_tag})_:\n{m['content']}\n")
            return {"format": "markdown", "content": "\n".join(lines)}

        upstream = to_upstream_provider(norm)
        model = target_model or PLATFORM_DEFAULT_MODELS.get(norm, "claude-3-5-sonnet")
        if norm == "arena":
            arena_turns = get_conversation_messages(session_id, target_format="arena")
            return {"platform": "arena", "model": model, "turns": arena_turns}

        payload = export_conversation_for_provider(session_id, provider=upstream, model=model)
        payload["platform"] = norm
        return payload

    def interact(
        self,
        session_id: str,
        prompt: str,
        target_platform: Optional[str] = None,
        target_model: Optional[str] = None,
        effort: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a prompt within any session routed through any chosen platform/model.
        Preserves full cross-platform history and appends both user & assistant turns.
        """
        conv = db.get_conversation(session_id)
        if not conv:
            seed_plat = normalize_platform(target_platform or infer_platform_from_model(target_model) or "arena")
            seed_model = target_model or PLATFORM_DEFAULT_MODELS.get(seed_plat, "claude-3-5-sonnet")
            self.create_session(session_id=session_id, platform=seed_plat, model=seed_model)
            conv = db.get_conversation(session_id)

        assert conv is not None
        active_plat = normalize_platform(target_platform or conv.get("default_provider") or infer_platform_from_model(target_model) or "arena")
        upstream_prov = to_upstream_provider(active_plat)
        resolved_model = target_model or conv.get("default_model") or PLATFORM_DEFAULT_MODELS.get(active_plat, "claude-3-5-sonnet")

        if system_prompt:
            existing_msgs = db.get_messages_by_conversation(session_id, order="ASC")
            has_sys = any(m.get("role") in ("system", "developer") and m.get("content") == system_prompt for m in existing_msgs)
            if not has_sys:
                save_message(session_id, "system", system_prompt, provider=upstream_prov, model=resolved_model)

        save_message(session_id, "user", prompt, provider="client", model=resolved_model)

        exported = export_conversation_for_provider(session_id, provider=upstream_prov, model=resolved_model)
        exported_msgs = exported.get("messages", [])
        sys_text = exported.get("system", system_prompt)

        dialogue_turns: List[str] = []
        for m in exported_msgs:
            r = m.get("role", "user")
            c = _extract_text(m.get("content", ""))
            if r in ("system", "developer") and not sys_text:
                sys_text = c
            elif r in ("user", "assistant"):
                dialogue_turns.append(f"{r.capitalize()}: {c}")

        if len(dialogue_turns) > 1:
            full_prompt = "\n\n".join(dialogue_turns)
        elif dialogue_turns:
            full_prompt = dialogue_turns[0].split(": ", 1)[-1]
        else:
            full_prompt = prompt

        provider_pref = [upstream_prov] + [
            p for p in ("arena", "anthropic", "openai", "openrouter", "groq", "deepinfra", "omniroute", "pollinations")
            if p != upstream_prov
        ]

        res = self.router.complete_text(
            prompt=full_prompt,
            system_prompt=sys_text,
            model=resolved_model,
            effort=effort,
            provider_preference=provider_pref,
        )

        reply_text = res.get("text", "")
        used_provider = res.get("provider", upstream_prov)
        used_model = res.get("canonical_model", resolved_model)

        save_message(
            session_id,
            "assistant",
            reply_text,
            provider=used_provider,
            model=used_model,
            metadata={"effort": effort, "target_platform": active_plat},
        )

        # Update metadata so both requested & serving platforms show this session
        meta = dict((db.get_conversation(session_id) or {}).get("metadata") or {})
        routed = list(meta.get("routed_platforms") or [])
        for p in (active_plat, normalize_platform(used_provider)):
            if p not in routed:
                routed.append(p)
        visible = list(meta.get("visible_on_platforms") or DEFAULT_SHARED_PLATFORMS)
        for p in routed:
            if p not in visible:
                visible.append(p)
        meta["routed_platforms"] = routed
        meta["visible_on_platforms"] = visible
        db.update_conversation(
            session_id,
            default_provider=used_provider,
            default_model=used_model,
            metadata=meta,
        )

        return {
            "session_id": session_id,
            "platform": active_plat,
            "provider": used_provider,
            "model": used_model,
            "reply": reply_text,
            "session": self.get_session(session_id),
        }

    def rename_session(self, session_id: str, title: str) -> bool:
        return db.update_conversation(session_id, title=title)

    def delete_session(self, session_id: str) -> bool:
        return db.delete_conversation(session_id)
