"""
circuit_breaker.py — stop calling a provider that is already failing.

Health tracking answers "is this provider working"; the breaker answers "should
we try it again right now". Without one, a provider that has started failing
every request keeps absorbing calls, each burning its full retry budget and
backoff, which is how a single provider outage turns into a pipeline timeout.

State is per-scope and lives in memory. It is deliberately not persisted: a
breaker records a transient condition measured in seconds, and restoring an
open breaker from a previous boot would deny traffic to a provider that has
long since recovered. Health state, which is persisted, covers the longer view.

Concurrency: every transition happens under a single lock, and half-open
probes are handed out as a counted allowance. Two threads arriving while the
breaker is half-open cannot both probe, which is the difference between a
controlled recovery and a thundering herd against a provider that just failed.
"""
from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"

THRESHOLD_ENV = "SHORTS_CB_FAILURE_THRESHOLD"
COOLDOWN_ENV = "SHORTS_CB_COOLDOWN_S"
PROBES_ENV = "SHORTS_CB_HALF_OPEN_PROBES"

DEFAULT_THRESHOLD = 5
DEFAULT_COOLDOWN_S = 60.0
DEFAULT_PROBES = 1

# Categories that mean the provider cannot serve this account, so counting them
# toward a threshold is pointless — the next call fails identically. These open
# the breaker immediately instead of waiting for `threshold` repeats.
IMMEDIATE_OPEN = {"authentication", "billing", "quota_exhausted", "unsupported_model"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


class CircuitBreaker:
    """A single breaker for one scope, e.g. a provider or provider/model pair."""

    def __init__(
        self,
        scope: str,
        threshold: int | None = None,
        cooldown_s: float | None = None,
        half_open_probes: int | None = None,
    ) -> None:
        self.scope = scope
        self.threshold = threshold if threshold else _env_int(THRESHOLD_ENV, DEFAULT_THRESHOLD)
        self.cooldown_s = (
            cooldown_s if cooldown_s else _env_float(COOLDOWN_ENV, DEFAULT_COOLDOWN_S)
        )
        self.probes = (
            half_open_probes if half_open_probes
            else _env_int(PROBES_ENV, DEFAULT_PROBES)
        )
        self._lock = threading.RLock()
        self._state = CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._probes_in_flight = 0
        self._last_category: str | None = None

    # ─── Inspection ──────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        with self._lock:
            self._maybe_half_open()
            return self._state

    @property
    def failures(self) -> int:
        with self._lock:
            return self._failures

    def _maybe_half_open(self) -> None:
        """Move OPEN -> HALF_OPEN once the cooldown has elapsed. Caller holds the lock."""
        if self._state != OPEN or self._opened_at is None:
            return
        if time.monotonic() - self._opened_at >= self.cooldown_s:
            self._state = HALF_OPEN
            self._probes_in_flight = 0
            logger.info("breaker[%s]: cooldown elapsed, half-open", self.scope)

    # ─── Admission ───────────────────────────────────────────────────────────

    def allow(self) -> tuple[bool, str]:
        """
        Whether a call may proceed.

        Half-open admits a bounded number of probes; everyone else is refused
        until one of those probes reports back.
        """
        with self._lock:
            self._maybe_half_open()
            if self._state == CLOSED:
                return True, "closed"
            if self._state == OPEN:
                remaining = self.cooldown_s
                if self._opened_at is not None:
                    remaining = max(
                        0.0, self.cooldown_s - (time.monotonic() - self._opened_at)
                    )
                return False, f"open, {remaining:.0f}s of cooldown remaining"
            if self._probes_in_flight < self.probes:
                self._probes_in_flight += 1
                return True, "half-open probe"
            return False, f"half-open, all {self.probes} probes in flight"

    # ─── Outcomes ────────────────────────────────────────────────────────────

    def record_success(self) -> str:
        with self._lock:
            prior = self._state
            self._state = CLOSED
            self._failures = 0
            self._opened_at = None
            self._probes_in_flight = 0
            self._last_category = None
        if prior != CLOSED:
            logger.info("breaker[%s]: %s -> closed after a success", self.scope, prior)
        return CLOSED

    def record_failure(self, category: str = "error") -> str:
        with self._lock:
            self._last_category = category
            immediate = category in IMMEDIATE_OPEN
            self._failures += 1

            if self._state == HALF_OPEN:
                # A probe failed: reopen for a full cooldown rather than
                # admitting more traffic to a provider that is still down.
                self._open(f"half-open probe failed ({category})")
                return self._state
            if immediate:
                self._open(f"{category} is not retryable")
                return self._state
            if self._failures >= self.threshold:
                self._open(f"{self._failures} consecutive failures")
            return self._state

    def _open(self, reason: str) -> None:
        """Caller holds the lock."""
        self._state = OPEN
        self._opened_at = time.monotonic()
        self._probes_in_flight = 0
        logger.warning("breaker[%s]: OPEN — %s", self.scope, reason)

    def reset(self) -> None:
        with self._lock:
            self._state = CLOSED
            self._failures = 0
            self._opened_at = None
            self._probes_in_flight = 0
            self._last_category = None

    def as_dict(self) -> dict:
        with self._lock:
            return {
                "scope": self.scope,
                "state": self.state,
                "failures": self._failures,
                "threshold": self.threshold,
                "cooldown_s": self.cooldown_s,
                "last_category": self._last_category,
            }


# ─── Registry ────────────────────────────────────────────────────────────────

_registry: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get(scope: str) -> CircuitBreaker:
    """
    The breaker for a scope, creating it on first use.

    Scopes are conventionally `provider` or `provider/model`. A model-level
    scope lets one bad model id fail over without taking the whole provider out
    of rotation.
    """
    with _registry_lock:
        breaker = _registry.get(scope)
        if breaker is None:
            breaker = CircuitBreaker(scope)
            _registry[scope] = breaker
        return breaker


def reset(scope: str | None = None) -> None:
    """Reset one breaker, or drop all of them. For tests and manual recovery."""
    with _registry_lock:
        if scope is None:
            _registry.clear()
            return
        breaker = _registry.get(scope)
    if breaker is not None:
        breaker.reset()


def snapshot() -> list[dict]:
    """Every breaker's current state, for diagnostics and the health endpoint."""
    with _registry_lock:
        return [breaker.as_dict() for breaker in _registry.values()]
