"""
provider_health.py — runtime availability, distinct from registry claims.

The registry's `health` field is whatever the last scheduled probe recorded.
It is a claim, refreshed on a workflow schedule, and it can say "ok" for a
provider that has been failing every request for an hour. This module tracks
what has actually been observed from real calls in this process's state file,
and it expires.

Three properties matter:

* Expiry. A state older than the TTL becomes `unknown`, not `healthy`. Stale
  evidence must not keep a provider in rotation.
* Fail-open on unknown. Missing or expired data yields `unknown`, which does
  not block routing. Blocking on absent evidence would take every provider out
  of rotation on a fresh checkout, before any call had been made.
* Recovery. One success clears the failure streak. A provider that flaps back
  should not stay marked unhealthy for the rest of the cooldown.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HEALTHY = "healthy"
DEGRADED = "degraded"
UNHEALTHY = "unhealthy"
UNKNOWN = "unknown"

# Anything in this set blocks routing. `degraded` does not: a provider that is
# rate-limiting is still worth trying after its own cooldown, and the circuit
# breaker is the right control for backing off.
BLOCKING = {UNHEALTHY}

STATE_PATH_ENV = "SHORTS_HEALTH_STATE_PATH"
TTL_ENV = "SHORTS_HEALTH_TTL_S"
DEGRADED_THRESHOLD_ENV = "SHORTS_HEALTH_DEGRADED_AFTER"
UNHEALTHY_THRESHOLD_ENV = "SHORTS_HEALTH_UNHEALTHY_AFTER"

DEFAULT_STATE_PATH = Path(".shorts_state/provider_health.json")
DEFAULT_TTL_S = 3600.0
DEFAULT_DEGRADED_AFTER = 1
DEFAULT_UNHEALTHY_AFTER = 3

_lock = threading.RLock()


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("health: %s=%r is not a number; using %s", name, raw, default)
        return default
    # Zero or negative would expire every state immediately or never, so it is
    # treated as misconfiguration rather than honoured.
    if value <= 0:
        logger.warning("health: %s=%r is not positive; using %s", name, raw, default)
        return default
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def state_path() -> Path:
    raw = os.environ.get(STATE_PATH_ENV, "").strip()
    return Path(raw) if raw else DEFAULT_STATE_PATH


def ttl_s() -> float:
    return _env_float(TTL_ENV, DEFAULT_TTL_S)


def _load() -> dict[str, Any]:
    path = state_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        # A corrupt state file must not take routing down. Start from empty,
        # which yields `unknown` for every provider.
        logger.warning("health: could not read %s (%s); treating as empty", path, exc)
        return {}


def _save(state: dict[str, Any]) -> None:
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        tmp.replace(path)
    except OSError as exc:
        logger.warning("health: could not write %s (%s)", path, exc)


def _state_for(provider: str, state: dict[str, Any] | None = None) -> dict[str, Any]:
    entry = (state if state is not None else _load()).get(provider)
    return entry if isinstance(entry, dict) else {}


def status(provider: str, now: float | None = None) -> dict[str, Any]:
    """
    Current runtime health for a provider.

    Returns a dict with `state`, `reason`, `age_s` and `consecutive_failures`.
    `state` is `unknown` when there is no observation, or the observation has
    expired.
    """
    # Wall clock, not monotonic: this state is persisted and must stay
    # meaningful across a process restart.
    now = time.time() if now is None else now
    with _lock:
        entry = _state_for(provider)
    if not entry:
        return {"state": UNKNOWN, "reason": "no runtime observation yet",
                "age_s": None, "consecutive_failures": 0}

    recorded_at = entry.get("at")
    age = None
    if isinstance(recorded_at, (int, float)):
        age = max(0.0, now - float(recorded_at))
        if age > ttl_s():
            return {"state": UNKNOWN, "reason": f"observation expired after {age:.0f}s",
                    "age_s": age,
                    "consecutive_failures": entry.get("consecutive_failures", 0)}

    streak = int(entry.get("consecutive_failures") or 0)
    unhealthy_after = _env_int(UNHEALTHY_THRESHOLD_ENV, DEFAULT_UNHEALTHY_AFTER)
    degraded_after = _env_int(DEGRADED_THRESHOLD_ENV, DEFAULT_DEGRADED_AFTER)

    if streak >= unhealthy_after:
        state = UNHEALTHY
    elif streak >= degraded_after:
        state = DEGRADED
    else:
        state = HEALTHY

    return {"state": state,
            "reason": entry.get("reason") or f"{streak} consecutive failures",
            "age_s": age, "consecutive_failures": streak}


def is_available(provider: str) -> tuple[bool, str]:
    """
    Whether runtime health permits routing to this provider.

    `unknown` is allowed: absent evidence is not evidence of failure, and
    refusing it would leave nothing routable before the first call.
    """
    info = status(provider)
    if info["state"] in BLOCKING:
        return False, f"runtime health={info['state']} ({info['reason']})"
    return True, f"runtime health={info['state']}"


def record_success(provider: str) -> None:
    """A completed call clears the failure streak."""
    with _lock:
        state = _load()
        prior = int(_state_for(provider, state).get("consecutive_failures") or 0)
        state[provider] = {"consecutive_failures": 0, "at": time.time(),
                           "reason": "last call succeeded", "last_error": None}
        _save(state)
    if prior:
        logger.info("health: %s recovered after %d failures", provider, prior)


def record_failure(provider: str, category: str = "error",
                   detail: str = "") -> dict[str, Any]:
    """
    Record a failed call and return the new status.

    The category is stored for diagnostics only; the streak length decides the
    state, so one bad response degrades rather than disabling.
    """
    with _lock:
        state = _load()
        streak = int(_state_for(provider, state).get("consecutive_failures") or 0) + 1
        state[provider] = {"consecutive_failures": streak, "at": time.time(),
                           "reason": f"{category} x{streak}",
                           "last_error": (detail or "")[:200]}
        _save(state)
    info = status(provider)
    logger.warning("health: %s %s (%s)", provider, info["state"], info["reason"])
    return info


def reset(provider: str | None = None) -> None:
    """Clear recorded state, for tests and for manual recovery."""
    with _lock:
        if provider is None:
            _save({})
            return
        state = _load()
        state.pop(provider, None)
        _save(state)
