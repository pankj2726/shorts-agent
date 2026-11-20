"""
router.py — Provider and endpoint routing for the free-AI provider pool.

Implements the "Model Router" described in
`agents/agent-4-providers/AGENT.md`:

  1. Check the quota tracker for the primary provider.
  2. If quota is exhausted or health is `down`, try fallbacks in order.
  3. If every API provider fails, fall back to a local provider.
  4. Log every routing decision with a timestamp and a reason.

Design constraints (from AGENT.md "Security Rules", enforced here):
  * Only the single configured account is ever used. This module never rotates
    between accounts or credentials to evade a per-account limit.
  * Unknown quotas stay `None` — a missing number is never invented.
  * Credentials are read from the environment and never logged.

The router is opt-in for LLM traffic: set `LLM_ROUTER=true` to have
`agent.modules.llm.complete()` delegate here. Until then the existing
Gemini -> Groq -> OpenRouter chain in llm.py remains the live path.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agent.modules.config import env_bool

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "agents" / "agent-4-providers" / "registry" / "providers.json"
QUOTA_PATH = REPO_ROOT / "agent" / "state" / "quota_tracker.json"
DECISION_LOG = REPO_ROOT / "agents" / "agent-4-providers" / "router" / "decisions.jsonl"

# Providers that need a credential from the environment to be usable at all.
# A provider absent from this map is local and needs no key.
PROVIDER_KEY_ENV = {
    "google-gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "pollinations-ai": "POLLINATIONS_AI_API_KEY",
}

# Task -> ordered provider preference. Mirrors the routing table in AGENT.md.
# The final entry for each task is a local or procedural fallback where one exists.
ROUTING_TABLE: dict[str, list[str]] = {
    "scriptwriting": ["google-gemini", "groq", "openrouter"],
    "content_analysis": ["google-gemini", "openrouter"],
    "image_generation": ["pollinations-ai", "procedural-gradient-local"],
    "speech_recognition": ["whisperx", "groq"],
    "voiceover": ["edge-tts", "pyttsx3-local"],
    "music_generation": ["ace-step", "cc0-music-library"],
    "video_generation": ["wan-video", "pollinations-ai"],
    "captions": ["edge-tts", "whisperx"],
    "rendering": ["ffmpeg-local"],
}

# Providers that are local binaries/libraries rather than remote endpoints.
LOCAL_PROVIDERS = {
    "procedural-gradient-local",
    "pyttsx3-local",
    "cc0-music-library",
    "ffmpeg-local",
}

HEALTH_BLOCKING = {"down"}


class RoutingError(Exception):
    """Raised when no provider can serve a task."""


@dataclass
class RouteDecision:
    """The outcome of one routing decision, including why."""

    task: str
    provider: str
    model: Optional[str]
    reason: str
    considered: list[dict] = field(default_factory=list)
    at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def as_dict(self) -> dict:
        return {
            "task": self.task,
            "provider": self.provider,
            "model": self.model,
            "reason": self.reason,
            "considered": self.considered,
            "at": self.at,
        }


# ─── Registry ────────────────────────────────────────────────────────────────

def load_registry(path: Path = REGISTRY_PATH) -> list[dict]:
    """Load the provider registry. Returns [] when the file is missing."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("router: cannot read registry %s: %s", path, exc)
        return []
    return data if isinstance(data, list) else []


def registry_by_id(registry: Optional[list[dict]] = None) -> dict[str, dict]:
    """Index the registry by provider id."""
    reg = registry if registry is not None else load_registry()
    return {entry.get("provider", ""): entry for entry in reg if entry.get("provider")}


def has_credential(provider: str) -> bool:
    """
    True when a provider can be used credential-wise.

    Providers absent from PROVIDER_KEY_ENV need no key at all (local tools such
    as edge-tts and ffmpeg), so they are always satisfied.
    """
    env_name = PROVIDER_KEY_ENV.get(provider)
    if env_name is None:
        return True
    return bool(os.environ.get(env_name, "").strip())


def is_eligible(provider: str, entry: Optional[dict] = None) -> tuple[bool, str]:
    """
    Return (eligible, reason) for a provider.

    A provider is skipped when it is marked `down`, when it needs a credential
    that is not configured, or when its integration has not been started.
    """
    if provider in LOCAL_PROVIDERS:
        return True, "local provider"
    if entry is None:
        return False, "not in registry"
    if entry.get("health") in HEALTH_BLOCKING:
        return False, f"health={entry.get('health')}"
    if entry.get("integration_status") == "not_started":
        return False, "integration_status=not_started"
    if not has_credential(provider):
        return False, "no credential configured"
    return True, "eligible"


# ─── Quota tracker ───────────────────────────────────────────────────────────

def _today() -> str:
    return date.today().isoformat()


def load_quota(path: Optional[Path] = None) -> dict[str, dict]:
    """Load the per-provider quota tracker, resetting stale daily counters."""
    path = path or QUOTA_PATH
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    today = _today()
    for entry in data.values():
        if entry.get("last_reset_date") != today:
            entry["requests_today"] = 0
            entry["tokens_today"] = 0
            entry["last_reset_date"] = today
    return data


def save_quota(tracker: dict[str, dict], path: Optional[Path] = None) -> None:
    """Persist the quota tracker, creating parent directories as needed."""
    path = path or QUOTA_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(tracker, fh, indent=2, sort_keys=True)
    except OSError as exc:
        logger.warning("router: cannot write quota tracker %s: %s", path, exc)


def quota_entry(provider: str, tracker: Optional[dict[str, dict]] = None) -> dict:
    """Return the tracker entry for a provider, creating it if absent."""
    own = tracker is None
    tracker = load_quota() if own else tracker
    entry = tracker.setdefault(
        provider,
        {
            "provider": provider,
            "model": None,
            "requests_today": 0,
            "requests_limit_daily": None,
            "tokens_today": 0,
            "tokens_limit_daily": None,
            "last_reset_date": _today(),
            "last_error": None,
            "health": "unknown",
        },
    )
    if own:
        save_quota(tracker)
    return entry


def quota_exhausted(provider: str, tracker: Optional[dict[str, dict]] = None) -> tuple[bool, str]:
    """
    True when the provider's known daily request limit has been reached.

    An unknown limit (`None`) never counts as exhausted — per AGENT.md, unknown
    quotas stay null and are never invented.
    """
    entry = quota_entry(provider, tracker)
    limit = entry.get("requests_limit_daily")
    used = entry.get("requests_today") or 0
    if limit is None:
        return False, "limit unknown"
    if used >= int(limit):
        return True, f"quota {used}/{limit}"
    return False, f"quota {used}/{limit}"


def record_request(
    provider: str,
    model: Optional[str] = None,
    tokens: int = 0,
    error: Optional[str] = None,
    path: Optional[Path] = None,
) -> None:
    """Record one request against a provider's daily counters."""
    tracker = load_quota(path)
    entry = quota_entry(provider, tracker)
    entry["requests_today"] = int(entry.get("requests_today") or 0) + 1
    entry["tokens_today"] = int(entry.get("tokens_today") or 0) + int(tokens or 0)
    if model:
        entry["model"] = model
    entry["last_error"] = error
    save_quota(tracker, path)


# ─── Routing ─────────────────────────────────────────────────────────────────

def _pick_model(entry: Optional[dict]) -> Optional[str]:
    """First model id declared for a provider, or None."""
    if not entry:
        return None
    models = entry.get("models") or []
    if not models:
        return None
    first = models[0]
    return first.get("model_id") if isinstance(first, dict) else None


def route(task: str, registry: Optional[list[dict]] = None) -> RouteDecision:
    """
    Choose a provider for `task`.

    Walks the routing table in order, skipping providers that are down,
    unconfigured, not yet integrated, or out of known quota. Raises
    RoutingError when nothing can serve the task.
    """
    candidates = ROUTING_TABLE.get(task)
    if not candidates:
        raise RoutingError(f"no routing table entry for task {task!r}")

    reg = registry_by_id(registry if registry is not None else load_registry())
    tracker = load_quota()
    considered: list[dict] = []

    for provider in candidates:
        entry = reg.get(provider)
        ok, why = is_eligible(provider, entry)
        if not ok:
            considered.append({"provider": provider, "skipped": why})
            continue
        exhausted, quota_note = quota_exhausted(provider, tracker)
        if exhausted:
            considered.append({"provider": provider, "skipped": quota_note})
            continue
        considered.append({"provider": provider, "selected": True, quota_note: quota_note})
        decision = RouteDecision(
            task=task,
            provider=provider,
            model=_pick_model(entry),
            reason=f"{provider} first eligible ({quota_note})",
            considered=considered,
        )
        _log_decision(decision)
        return decision

    decision = RouteDecision(
        task=task,
        provider="",
        model=None,
        reason="no eligible provider",
        considered=considered,
    )
    _log_decision(decision)
    raise RoutingError(f"no eligible provider for task {task!r}: {considered}")


def _log_decision(decision: RouteDecision, path: Optional[Path] = None) -> None:
    """Append one decision to the JSONL routing log (best effort)."""
    path = path or DECISION_LOG
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(decision.as_dict(), ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("router: cannot append decision log %s: %s", path, exc)
    logger.info(
        "router: task=%s -> provider=%s model=%s (%s)",
        decision.task,
        decision.provider or "<none>",
        decision.model or "-",
        decision.reason,
    )


# ─── LLM entry point ─────────────────────────────────────────────────────────

# Provider id -> the llm.py callable that serves it.
_LLM_BACKENDS = {
    "google-gemini": "_gemini",
    "groq": "_groq",
    "openrouter": "_openrouter",
}


def router_enabled() -> bool:
    """True when LLM traffic should be routed through this module."""
    return env_bool("LLM_ROUTER", False)


def complete(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    task: str = "scriptwriting",
) -> str:
    """
    Route a completion through the registry, falling back in table order.

    Records every attempt against the quota tracker so later calls can see
    what a provider has already consumed today.
    """
    from agent.modules import llm  # local import: llm may import this module

    decision = route(task)
    errors: list[str] = []

    # Try the selected provider first, then the remaining eligible ones.
    order = [decision.provider] + [
        p for p in ROUTING_TABLE.get(task, []) if p not in (decision.provider,)
    ]

    for provider in order:
        backend_name = _LLM_BACKENDS.get(provider)
        if backend_name is None:
            continue
        backend = getattr(llm, backend_name, None)
        if backend is None:
            errors.append(f"{provider}: no backend")
            continue
        try:
            result = backend(prompt, system, temperature, max_tokens)
        except Exception as exc:  # noqa: BLE001 - provider errors are expected
            errors.append(f"{provider}: {type(exc).__name__}")
            record_request(provider, error=f"{type(exc).__name__}: {exc}"[:200])
            logger.warning("router: %s failed (%s), trying next", provider, exc)
            continue
        record_request(provider, model=_pick_model(registry_by_id().get(provider)))
        return result

    raise llm.LLMError("router: all eligible providers failed: " + "; ".join(errors))


def summary() -> dict[str, Any]:
    """Snapshot of routing state, for reports and health checks."""
    reg = registry_by_id()
    tracker = load_quota()
    out: dict[str, Any] = {"generated_at": _today(), "providers": []}
    for provider, entry in reg.items():
        ok, why = is_eligible(provider, entry)
        exhausted, quota_note = quota_exhausted(provider, tracker)
        out["providers"].append(
            {
                "provider": provider,
                "health": entry.get("health"),
                "integration_status": entry.get("integration_status"),
                "eligible": ok and not exhausted,
                "reason": quota_note if ok else why,
                "credential_configured": has_credential(provider),
                "requests_today": (tracker.get(provider) or {}).get("requests_today", 0),
                "requests_limit_daily": (tracker.get(provider) or {}).get("requests_limit_daily"),
            }
        )
    return out
