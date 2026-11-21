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
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from agent.modules import budget, circuit_breaker, provider_errors, provider_health
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
    # wan-video only. pollinations-ai was listed here as a fallback, but its sole
    # model (flux) is text-to-image, so a video request silently resolved to a
    # still frame and reported success. Better to raise RoutingError than to
    # hand back the wrong medium.
    "video_generation": ["wan-video"],
    # Captions are produced FROM audio. agent/modules/captions.py resolves word
    # timings via Groq's whisper-large-v3 transcription endpoint, so the
    # providers here are speech-recognition ones. edge-tts was listed first,
    # which is the opposite direction — it synthesises speech from text. The VTT
    # that render.py burns in comes out of the TTS call as a by-product and is
    # already covered by the `voiceover` task.
    "captions": ["whisperx", "groq"],
    # Timed subtitle formatting (WebVTT -> styled ASS -> burned in) is pure
    # local work in captions.vtt_to_ass() and render._ffmpeg_burn_captions().
    # It needs no provider and imposes no modality.
    "subtitle_formatting": ["ffmpeg-local"],
    "rendering": ["ffmpeg-local"],
}

# Modality each task actually needs, matched against the `modality` list each
# registry model declares. Checked by supports_task_modality(). An empty tuple
# means "no constraint" — used for local tooling that declares no modality.
TASK_MODALITY: dict[str, tuple[str, ...]] = {
    "scriptwriting": ("text",),
    "content_analysis": ("text",),
    "image_generation": ("text-to-image", "image-to-image"),
    "speech_recognition": ("audio-to-text",),
    "voiceover": ("text-to-speech",),
    "music_generation": ("text-to-music",),
    "video_generation": ("text-to-video", "image-to-video"),
    # Captions come FROM audio: transcription to word-level timings.
    "captions": ("audio-to-text",),
    # Formatting existing timings into ASS/VTT is local, provider-free work.
    "subtitle_formatting": (),
    "rendering": (),
}

# Providers that are local binaries/libraries rather than remote endpoints.
LOCAL_PROVIDERS = {
    "procedural-gradient-local",
    "pyttsx3-local",
    "cc0-music-library",
    "ffmpeg-local",
}

HEALTH_BLOCKING = {"down"}

# Reasons beginning with this marker mean "allowed, but a compatibility rule
# was bypassed" rather than a plain pass. Surfaced on RouteDecision.
COMPAT_EXCEPTION_PREFIX = "compat-exception:"


class RoutingError(Exception):
    """Raised when no provider can serve a task."""


@dataclass
class RouteDecision:
    """The outcome of one routing decision, including why."""

    task: str
    provider: str
    model: str | None
    reason: str
    considered: list[dict] = field(default_factory=list)
    # Compatibility rules that were bypassed to reach this decision, e.g.
    # ["compat-exception:local-tooling"]. Empty means every check applied.
    compat_exceptions: list[str] = field(default_factory=list)
    at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    def as_dict(self) -> dict:
        return {
            "task": self.task,
            "provider": self.provider,
            "model": self.model,
            "reason": self.reason,
            "considered": self.considered,
            "compat_exceptions": self.compat_exceptions,
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


def registry_by_id(registry: list[dict] | None = None) -> dict[str, dict]:
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


def is_eligible(provider: str, entry: dict | None = None) -> tuple[bool, str]:
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


def provider_modalities(entry: dict | None) -> set[str]:
    """Every modality declared across a provider's models."""
    if not entry:
        return set()
    declared: set[str] = set()
    for model in entry.get("models") or []:
        if isinstance(model, dict):
            declared.update(model.get("modality") or [])
    return declared


def supports_task_modality(
    task: str,
    provider: str,
    entry: dict | None = None,
    policy: str | None = None,
) -> tuple[bool, str]:
    """
    Return (supports, reason) for whether `provider` can serve `task`.

    Compatibility policy (ROUTER_MODALITY_POLICY):

    * ``strict`` (default) — a provider that is present in the registry must
      declare a modality that matches the task. An entry with no declared
      modality is rejected rather than waved through, so an undocumented model
      can never silently serve production traffic.
    * ``legacy`` — the previous fail-open behaviour, where an absent modality
      declaration is treated as "unknown, allow". Opt in explicitly with
      ``ROUTER_MODALITY_POLICY=legacy`` when running against an older registry.

    Two exceptions survive in both modes, because both are deliberate rather
    than unknown:

    * local tooling in LOCAL_PROVIDERS has no registry entry at all;
    * tasks whose TASK_MODALITY entry is empty impose no constraint.

    Every exception is returned as a ``compat-exception:`` reason so callers and
    logs can see that a rule was bypassed and why.
    """
    active = policy or modality_policy()

    # Documented exception, checked first: local tools are not registry entries
    # by design, so no registry-based rule can apply to them. Reporting this
    # ahead of "no constraint" keeps the more specific exception visible.
    if provider in LOCAL_PROVIDERS:
        return True, "compat-exception:local-tooling"

    required = TASK_MODALITY.get(task)
    if not required:
        return True, "compat-exception:no-modality-constraint"

    if entry is None:
        # is_eligible() already rejects non-local providers missing from the
        # registry; reaching here means the caller is probing in isolation.
        return False, "not in registry"

    declared = provider_modalities(entry)
    if not declared:
        if active == "legacy":
            logger.warning(
                "router: %s declares no modality; allowed only because "
                "ROUTER_MODALITY_POLICY=legacy",
                provider,
            )
            return True, "compat-exception:undeclared-modality-legacy"
        return False, (
            f"no declared modality ({active} policy requires registered "
            "providers to declare capabilities)"
        )

    if declared & set(required):
        return True, ""

    return False, (
        f"modality mismatch: declares {sorted(declared)}, "
        f"{task} needs one of {list(required)}"
    )


def modality_policy() -> str:
    """Active compatibility policy: 'strict' (default) or 'legacy'."""
    value = os.environ.get("ROUTER_MODALITY_POLICY", "").strip().lower()
    if value == "legacy":
        return "legacy"
    if value and value != "strict":
        logger.warning(
            "router: unknown ROUTER_MODALITY_POLICY=%r; using strict", value
        )
    return "strict"


def validate_registry(
    registry: list[dict] | None = None,
    policy: str | None = None,
) -> list[dict]:
    """
    Audit the registry for entries that would fail modality validation.

    Returns one finding per problem so operators can see compatibility
    exceptions instead of discovering them mid-pipeline. Findings are dicts of
    {provider, issue, detail}.
    """
    active = policy or modality_policy()
    findings: list[dict] = []
    reg = registry if registry is not None else load_registry()
    known = {e.get("provider") for e in reg if e.get("provider")}

    def add(provider, issue, detail, severity):
        findings.append({
            "provider": provider, "issue": issue,
            "detail": detail, "severity": severity,
        })

    for entry in reg:
        provider = entry.get("provider", "")
        models = entry.get("models") or []
        # A provider that has not been integrated yet is allowed to be
        # incomplete — it cannot be selected either way. Anything integrated is
        # held to the full standard, because it can receive production traffic.
        integrated = entry.get("integration_status") == "integrated"
        severity = "error" if integrated else "info"

        if not models:
            add(provider, "no_models",
                "registered with an empty models list", severity)
        declared = provider_modalities(entry)
        if not declared:
            add(provider, "no_modality_declared",
                f"would be rejected for every task under {active} policy; "
                "declare a modality per model", severity)
        for model in models:
            if not isinstance(model, dict):
                continue
            if not model.get("model_id"):
                add(provider, "missing_model_id",
                    "model entry has no model_id", "error")
            elif integrated and not (model.get("modality") or []):
                add(provider, "model_without_modality",
                    f"{model.get('model_id')} declares no modality", "error")

    # Providers referenced by the routing table but absent from the registry.
    for task, providers in ROUTING_TABLE.items():
        for provider in providers:
            if provider in LOCAL_PROVIDERS:
                continue
            if provider not in known:
                add(provider, "not_in_registry",
                    f"referenced by task {task!r} but not registered", "error")
    return findings


# ─── Quota tracker ───────────────────────────────────────────────────────────

def _today() -> str:
    return date.today().isoformat()


def load_quota(path: Path | None = None) -> dict[str, dict]:
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


def save_quota(tracker: dict[str, dict], path: Path | None = None) -> None:
    """Persist the quota tracker, creating parent directories as needed."""
    path = path or QUOTA_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(tracker, fh, indent=2, sort_keys=True)
    except OSError as exc:
        logger.warning("router: cannot write quota tracker %s: %s", path, exc)


def quota_entry(provider: str, tracker: dict[str, dict] | None = None) -> dict:
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


def quota_exhausted(provider: str, tracker: dict[str, dict] | None = None) -> tuple[bool, str]:
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
    model: str | None = None,
    tokens: int = 0,
    error: str | None = None,
    path: Path | None = None,
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

def _pick_model(entry: dict | None, task: str | None = None) -> str | None:
    """
    Model id to report for a provider, preferring one that matches the task.

    A provider can serve several tasks with different models (groq does text via
    openai/gpt-oss-120b and transcription via whisper-large-v3), so reporting
    models[0] unconditionally would mislabel half the decisions. Falls back to
    the first model when nothing matches, preserving the old behaviour.
    """
    if not entry:
        return None
    models = entry.get("models") or []
    if not models:
        return None
    required = set(TASK_MODALITY.get(task or "", ()) or ())
    if required:
        for model in models:
            if isinstance(model, dict) and set(model.get("modality") or []) & required:
                return model.get("model_id")
    first = models[0]
    return first.get("model_id") if isinstance(first, dict) else None


def route(task: str, registry: list[dict] | None = None) -> RouteDecision:
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
    exceptions: list[str] = []

    for provider in candidates:
        entry = reg.get(provider)
        ok, why = is_eligible(provider, entry)
        if not ok:
            considered.append({"provider": provider, "skipped": why})
            continue
        ok, why = supports_task_modality(task, provider, entry)
        if not ok:
            considered.append({"provider": provider, "skipped": why})
            continue
        if why.startswith(COMPAT_EXCEPTION_PREFIX):
            # The decision is allowed, but a compatibility rule was bypassed.
            # Surface it rather than letting it pass invisibly.
            exceptions.append(f"{provider}:{why}")
            logger.info("router: %s allowed via %s", provider, why)
        exhausted, quota_note = quota_exhausted(provider, tracker)
        if exhausted:
            considered.append({"provider": provider, "skipped": quota_note})
            continue
        considered.append({"provider": provider, "selected": True, quota_note: quota_note})
        decision = RouteDecision(
            task=task,
            provider=provider,
            model=_pick_model(entry, task),
            reason=f"{provider} first eligible ({quota_note})",
            considered=considered,
            compat_exceptions=exceptions,
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


def _log_decision(decision: RouteDecision, path: Path | None = None) -> None:
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

# Adapters implementing the uniform provider contract. Routing dispatches
# through these; _LLM_BACKENDS is retained only for callers that still resolve
# a backend by attribute name.
def _adapters():
    from agent.modules import providers

    return providers.BY_NAME


def _is_usable_completion(result: Any) -> tuple[bool, str]:
    """
    Validate a backend's reply before it is accepted as a completion.

    Providers do return 200s with empty bodies, and a truncated stream can yield
    None. Accepting those silently would let an empty script travel all the way
    to the renderer with nothing in the logs to explain it.
    """
    if not isinstance(result, str):
        return False, f"expected str, got {type(result).__name__}"
    if not result.strip():
        return False, "empty response"
    return True, ""


def router_enabled() -> bool:
    """True when LLM traffic should be routed through this module."""
    return env_bool("LLM_ROUTER", False)


def _env_key(task: str) -> str:
    """Task name as it appears in an LLM_*_ variable."""
    return "".join(c if c.isalnum() else "_" for c in task.upper())


def resolve_provider_model(task: str) -> tuple[str | None, str | None]:
    """
    Env-driven provider and model selection for a task.

    Task-specific variables win over the generic defaults:
      LLM_PROVIDER_<TASK>, LLM_MODEL_<TASK>,
      LLM_DEFAULT_PROVIDER, LLM_DEFAULT_MODEL

    Returns (None, None) when nothing is configured, leaving the routing table
    and registry to decide — the historical behaviour.
    """
    key = _env_key(task)
    provider = (
        os.environ.get(f"LLM_PROVIDER_{key}", "").strip()
        or os.environ.get("LLM_DEFAULT_PROVIDER", "").strip()
        or None
    )
    model = (
        os.environ.get(f"LLM_MODEL_{key}", "").strip()
        or os.environ.get("LLM_DEFAULT_MODEL", "").strip()
        or None
    )
    return provider, model


def model_is_pinned(task: str) -> bool:
    """True when LLM_MODEL_<TASK> explicitly pins this task's model."""
    return bool(os.environ.get(f"LLM_MODEL_{_env_key(task)}", "").strip())


_LAST_EXECUTION: dict[str, Any] | None = None


def last_execution() -> dict[str, Any] | None:
    """
    What the most recent routed completion actually did.

    `provider_used`/`model_used` describe the request that succeeded, which is
    not necessarily what was asked for. `model_used` comes from the provider's
    own response, so a provider that quietly substituted a model is visible.
    """
    return _LAST_EXECUTION


def complete(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    task: str = "scriptwriting",
) -> str:
    """
    Route a completion through the registry, falling back in table order.

    The selected model is now passed to the provider instead of being dropped:
    previously route() computed a model and the backend re-read its own module
    constant, so the selection never reached the request.

    Records every attempt against the quota tracker so later calls can see
    what a provider has already consumed today.
    """
    from agent.modules import llm  # local import: llm may import this module

    decision = route(task)
    reg = registry_by_id()
    env_provider, env_model = resolve_provider_model(task)

    requested_provider = env_provider or decision.provider
    if env_model:
        requested_model = env_model
    elif env_provider and env_provider != decision.provider:
        # A provider was overridden but no model named: take that provider's own
        # registry default. Using decision.model would carry over a model
        # belonging to whichever provider the table picked first.
        requested_model = _pick_model(reg.get(env_provider), task)
    else:
        requested_model = decision.model
    pinned = model_is_pinned(task)
    adapters = _adapters()

    # Resolve a pinned model against a provider before any request goes out.
    if pinned:
        if env_provider:
            # The provider was named too, so it has to declare the model.
            adapter = adapters.get(env_provider)
            if adapter is None or not adapter.supports_model(requested_model):
                declared = (
                    [m.model_id for m in adapter.declared_models()]
                    if adapter is not None else []
                )
                raise RoutingError(
                    f"LLM_MODEL_{_env_key(task)}={requested_model!r} is not "
                    f"declared for {env_provider}; declared models are {declared}"
                )
            requested_provider = env_provider
        else:
            # Only the model was named: find a provider in this task's routing
            # table that actually declares it. Rejecting here would make model
            # selection unusable without also naming a provider.
            match = [
                p for p in ROUTING_TABLE.get(task, [])
                if p in _LLM_BACKENDS
                and adapters.get(p) is not None
                and adapters[p].supports_model(requested_model)
            ]
            if not match:
                raise RoutingError(
                    f"LLM_MODEL_{_env_key(task)}={requested_model!r} is not "
                    f"declared by any provider routed for {task!r}: "
                    f"{ROUTING_TABLE.get(task, [])}"
                )
            requested_provider = match[0]
            logger.info(
                "router: %s implies provider %s for task %s",
                requested_model, requested_provider, task,
            )

    errors: list[str] = []
    fallbacks: list[str] = []

    # Try the selected provider first, then the remaining eligible ones.
    order = [requested_provider] + [
        p for p in ROUTING_TABLE.get(task, []) if p not in (requested_provider,)
    ]

    for provider in order:
        if provider not in _LLM_BACKENDS:
            continue
        adapter = adapters.get(provider)
        if adapter is None:
            errors.append(f"{provider}: no adapter")
            continue

        # Which model to ask this provider for. A generic default model is a
        # preference, not a pin: a provider that does not declare it falls back
        # to its own registry default, and that substitution is recorded.
        model = requested_model
        if model and not adapter.supports_model(model):
            fallback_model = _pick_model(reg.get(provider), task)
            fallbacks.append(
                f"{provider}: {model} not declared, used {fallback_model}"
            )
            logger.warning(
                "router: %s does not declare %s; falling back to %s",
                provider, model, fallback_model,
            )
            model = fallback_model
        if provider != requested_provider:
            fallbacks.append(f"{provider}: used after {requested_provider}")

        # Both scopes are consulted: a model-level breaker keeps one bad model
        # id from taking the whole provider out of rotation, while the
        # provider-level one covers an outage that affects everything on it.
        scopes = [provider] + ([f"{provider}/{model}"] if model else [])
        breakers = [circuit_breaker.get(scope) for scope in scopes]

        # Runtime health. Unknown is allowed: absent evidence is not evidence
        # of failure, and refusing it would leave nothing routable at startup.
        healthy, health_reason = provider_health.is_available(provider)
        if not healthy:
            errors.append(f"{provider}: {health_reason}")
            fallbacks.append(f"{provider}: skipped, {health_reason}")
            logger.warning("router: skipping %s (%s)", provider, health_reason)
            continue

        admitted, breaker_reason = True, ""
        for breaker in breakers:
            admitted, breaker_reason = breaker.allow()
            if not admitted:
                break
        if not admitted:
            errors.append(f"{provider}: breaker {breaker_reason}")
            fallbacks.append(f"{provider}: skipped, breaker {breaker_reason}")
            logger.warning("router: skipping %s (breaker %s)", provider, breaker_reason)
            continue

        # Budget is reserved before dispatch, so the ceiling is checked against
        # a cost that is already held rather than one that might be.
        try:
            reservation = budget.reserve(provider, model, task, prompt, max_tokens)
        except budget.BudgetExceededError as exc:
            errors.append(f"{provider}: {exc.reason}")
            fallbacks.append(f"{provider}: skipped, {exc.reason}")
            logger.warning("router: skipping %s (budget: %s)", provider, exc.reason)
            continue

        try:
            result = adapter._call(prompt, system, temperature, max_tokens, model)
        except Exception as exc:  # noqa: BLE001 - provider errors are expected
            err = provider_errors.classify_exception(provider, exc)
            errors.append(f"{provider}: {err.category}")
            fallbacks.append(f"{provider}: {err.category}")
            # Category first for stable machine reading, then the exception
            # class, so nothing that parsed the old format loses information.
            record_request(
                provider,
                error=f"{err.category} ({type(exc).__name__}): {err.detail}"[:200],
            )
            provider_health.record_failure(provider, err.category, err.detail)
            for breaker in breakers:
                breaker.record_failure(err.category)
            budget.release(reservation)
            logger.warning(
                "router: %s failed [%s%s], trying next", provider, err.category,
                "" if err.retryable else ", not retryable",
            )
            continue

        # A 200 with an empty body is still a failure. Without this check an
        # empty or non-string reply was returned as a valid completion, so an
        # empty script could reach the renderer with nothing logged.
        usable, why = _is_usable_completion(result)
        if not usable:
            errors.append(f"{provider}: {why}")
            fallbacks.append(f"{provider}: {why}")
            record_request(provider, error=why[:200])
            provider_health.record_failure(provider, "malformed_response", why)
            for breaker in breakers:
                breaker.record_failure("malformed_response")
            budget.release(reservation)
            logger.warning(
                "router: %s returned an unusable response (%s), trying next",
                provider, why,
            )
            continue

        usage = adapter.last_usage()
        actual_cost = budget.cost_from_usage(provider, model, usage)
        budget.settle(reservation, actual=actual_cost, usage=usage or {},
                      outcome="ok")
        provider_health.record_success(provider)
        for breaker in breakers:
            breaker.record_success()

        # Record the model the provider actually answered with, not the one we
        # asked for. Falls back to the request when the provider does not
        # report one, so the field is never silently empty.
        model_used = adapter.last_model() or model
        global _LAST_EXECUTION
        _LAST_EXECUTION = {
            "task": task,
            "requested_provider": requested_provider,
            "requested_model": requested_model,
            "provider_used": provider,
            "model_requested": model,
            "model_used": model_used,
            "model_substituted": bool(model_used != requested_model),
            "fallbacks": fallbacks,
            "cost_estimated": reservation.estimated,
            "cost_actual": reservation.actual,
            "usage": usage or {},
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        logger.info(
            "router: %s served %s with %s (requested %s/%s)%s",
            task, provider, model_used, requested_provider, requested_model,
            f"; fallbacks: {fallbacks}" if fallbacks else "",
        )

        record_request(provider, model=model_used)
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
