"""
budget.py — cost ceilings enforced before a paid call is dispatched.

Two design decisions worth stating, because both are easy to get wrong:

*No pricing is built in.* There is no default price table for any provider.
Prices are only what an operator puts in the cost config file. Inventing
plausible-looking per-token rates would be worse than having none, because a
wrong number silently produces a budget that looks enforced and is not.

*Enforcement is off until a ceiling is configured.* With nothing configured
there is nothing to exceed, so requests proceed. This is what keeps the change
backwards compatible. Once a ceiling exists, unknown pricing is blocked by
default rather than waved through — the conservative reading, since "we could
not price this" must not become "spend freely".

Costs are estimated from the request, then reconciled against what the provider
reports. An estimate is reserved atomically at admission, so two concurrent
requests cannot both pass a ceiling that only one of them fits under.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

COST_CONFIG_ENV = "SHORTS_COST_CONFIG_PATH"
STATE_PATH_ENV = "SHORTS_BUDGET_STATE_PATH"
UNKNOWN_POLICY_ENV = "SHORTS_BUDGET_UNKNOWN_POLICY"

MAX_PER_REQUEST_ENV = "SHORTS_BUDGET_MAX_PER_REQUEST"
MAX_PER_TASK_ENV = "SHORTS_BUDGET_MAX_PER_TASK"
MAX_PER_PERIOD_ENV = "SHORTS_BUDGET_MAX_PERIOD"
PERIOD_S_ENV = "SHORTS_BUDGET_PERIOD_S"

BLOCK = "block"
ALLOW = "allow"

DEFAULT_STATE_PATH = Path(".shorts_state/budget.json")
DEFAULT_PERIOD_S = 86400.0

# Rough chars-per-token for estimating a request before it is sent. Documented
# as an estimate: the real figure comes back in the response and is reconciled.
CHARS_PER_TOKEN = 4.0

_lock = threading.RLock()


class BudgetExceededError(Exception):
    """Raised when a ceiling would be exceeded, before any request is sent."""

    category = "budget_exceeded"

    def __init__(self, reason: str, limits: dict[str, Any] | None = None) -> None:
        self.reason = reason
        self.limits = limits or {}
        super().__init__(reason)


# ─── Configuration ───────────────────────────────────────────────────────────

def _env_float(name: str) -> float | None:
    """A ceiling, or None when unset. Zero and negative are treated as unset."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        logger.warning("budget: %s=%r is not a number; ignoring", name, raw)
        return None
    return value if value > 0 else None


def ceilings() -> dict[str, float | None]:
    return {
        "per_request": _env_float(MAX_PER_REQUEST_ENV),
        "per_task": _env_float(MAX_PER_TASK_ENV),
        "per_period": _env_float(MAX_PER_PERIOD_ENV),
    }


def period_s() -> float:
    raw = os.environ.get(PERIOD_S_ENV, "").strip()
    if not raw:
        return DEFAULT_PERIOD_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_PERIOD_S
    return value if value > 0 else DEFAULT_PERIOD_S


def unknown_policy() -> str:
    raw = os.environ.get(UNKNOWN_POLICY_ENV, "").strip().lower()
    return raw if raw in (BLOCK, ALLOW) else BLOCK


def enabled() -> bool:
    """Whether any ceiling is configured. Off means nothing to enforce."""
    return any(value is not None for value in ceilings().values())


def _cost_config() -> dict[str, Any]:
    raw = os.environ.get(COST_CONFIG_ENV, "").strip()
    if not raw:
        return {}
    path = Path(raw)
    if not path.exists():
        logger.warning("budget: cost config %s does not exist", path)
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        logger.warning("budget: could not read cost config %s (%s)", path, exc)
        return {}


def pricing_for(provider: str, model: str | None) -> dict[str, float] | None:
    """
    Configured per-1k-token prices, or None when the operator has not set any.

    Looks for an exact `provider/model` entry first, then a provider-level
    `*` default. Never synthesises a rate.
    """
    config = _cost_config()
    provider_cfg = config.get(provider)
    if not isinstance(provider_cfg, dict):
        return None
    entry = None
    if model and isinstance(provider_cfg.get(model), dict):
        entry = provider_cfg[model]
    elif isinstance(provider_cfg.get("*"), dict):
        entry = provider_cfg["*"]
    if not entry:
        return None
    try:
        return {
            "per_1k_input": float(entry.get("per_1k_input", 0.0)),
            "per_1k_output": float(entry.get("per_1k_output", 0.0)),
        }
    except (TypeError, ValueError):
        logger.warning("budget: malformed price entry for %s/%s", provider, model)
        return None


def estimate_tokens(prompt: str, max_tokens: int) -> tuple[int, int]:
    """(estimated input tokens, output tokens) for a request not yet sent."""
    return max(1, int(len(prompt or "") / CHARS_PER_TOKEN)), max(1, int(max_tokens))


def estimate_cost(provider: str, model: str | None, prompt: str,
                  max_tokens: int) -> float | None:
    """Estimated cost, or None when this provider/model has no configured price."""
    price = pricing_for(provider, model)
    if price is None:
        return None
    in_tok, out_tok = estimate_tokens(prompt, max_tokens)
    return (in_tok / 1000.0) * price["per_1k_input"] + \
           (out_tok / 1000.0) * price["per_1k_output"]


def cost_from_usage(provider: str, model: str | None,
                    usage: dict[str, Any] | None) -> float | None:
    """
    Actual cost from provider-reported token counts.

    Returns None when either the price or the usage figures are unavailable, so
    the caller leaves the estimate standing rather than recording a zero that
    would understate real spend.
    """
    if not usage:
        return None
    price = pricing_for(provider, model)
    if price is None:
        return None
    in_tok = usage.get("prompt_tokens", usage.get("promptTokenCount"))
    out_tok = usage.get("completion_tokens", usage.get("candidatesTokenCount"))
    try:
        in_tok = float(in_tok) if in_tok is not None else None
        out_tok = float(out_tok) if out_tok is not None else None
    except (TypeError, ValueError):
        return None
    if in_tok is None and out_tok is None:
        return None
    return ((in_tok or 0.0) / 1000.0) * price["per_1k_input"] + \
           ((out_tok or 0.0) / 1000.0) * price["per_1k_output"]


# ─── Persisted spend ─────────────────────────────────────────────────────────

def _state_path() -> Path:
    raw = os.environ.get(STATE_PATH_ENV, "").strip()
    return Path(raw) if raw else DEFAULT_STATE_PATH


def _load(now: float) -> dict[str, Any]:
    path = _state_path()
    default: dict[str, Any] = {"period_start": now, "spent": 0.0, "per_task": {}}
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("budget: could not read %s (%s); starting fresh", path, exc)
        return default
    if not isinstance(data, dict):
        return default
    start = data.get("period_start")
    if not isinstance(start, (int, float)) or (now - float(start)) > period_s():
        # The window has rolled over, so prior spend no longer counts.
        logger.info("budget: period rolled over; resetting aggregate spend")
        return default
    data.setdefault("spent", 0.0)
    data.setdefault("per_task", {})
    return data


def _save(state: dict[str, Any]) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        tmp.replace(path)
    except OSError as exc:
        logger.warning("budget: could not write %s (%s)", path, exc)


# ─── Reservations ────────────────────────────────────────────────────────────

@dataclass
class Reservation:
    """An estimated cost held against the ceilings until the call settles."""

    token: str
    provider: str
    model: str | None
    task: str
    estimated: float
    settled: bool = False
    actual: float | None = None
    outcome: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


def reserve(provider: str, model: str | None, task: str, prompt: str,
            max_tokens: int) -> Reservation:
    """
    Hold an estimated cost against every applicable ceiling.

    Raises BudgetExceededError before any request is sent. The estimate is
    added to the running totals under the lock, so concurrent reservations
    compete for the same remaining budget instead of each seeing the full
    amount.
    """
    token = uuid.uuid4().hex
    limits = ceilings()

    if not enabled():
        return Reservation(token, provider, model, task, 0.0, outcome="unenforced")

    estimated = estimate_cost(provider, model, prompt, max_tokens)
    if estimated is None:
        if unknown_policy() == BLOCK:
            raise BudgetExceededError(
                f"no configured price for {provider}/{model} and "
                f"{UNKNOWN_POLICY_ENV}={BLOCK}; refusing to dispatch an "
                "unpriceable request",
                limits,
            )
        logger.warning(
            "budget: no price for %s/%s; allowing because %s=%s",
            provider, model, UNKNOWN_POLICY_ENV, ALLOW,
        )
        estimated = 0.0

    if limits["per_request"] is not None and estimated > limits["per_request"]:
        raise BudgetExceededError(
            f"estimated {estimated:.6f} exceeds per-request ceiling "
            f"{limits['per_request']}", limits,
        )

    now = time.time()
    with _lock:
        state = _load(now)
        per_task = state.setdefault("per_task", {})
        task_spent = float(per_task.get(task, 0.0))
        period_spent = float(state.get("spent", 0.0))

        if limits["per_task"] is not None and task_spent + estimated > limits["per_task"]:
            raise BudgetExceededError(
                f"task {task!r} would reach {task_spent + estimated:.6f}, over its "
                f"ceiling {limits['per_task']}", limits,
            )
        if limits["per_period"] is not None and period_spent + estimated > limits["per_period"]:
            raise BudgetExceededError(
                f"period spend would reach {period_spent + estimated:.6f}, over its "
                f"ceiling {limits['per_period']}", limits,
            )

        # Reserve now, reconcile later. Holding the estimate is what stops two
        # concurrent requests from both fitting under the same remainder.
        per_task[task] = task_spent + estimated
        state["spent"] = period_spent + estimated
        _save(state)

    logger.info("budget: reserved %.6f for %s/%s (%s)", estimated, provider, model, task)
    return Reservation(token, provider, model, task, estimated)


def settle(reservation: Reservation, actual: float | None = None,
           usage: dict[str, Any] | None = None, outcome: str = "ok") -> Reservation:
    """
    Reconcile an estimate against what actually happened.

    `actual` wins when the caller can supply it; otherwise the estimate stands.
    A failed or cancelled call refunds the estimate, so a provider outage does
    not silently consume the budget.
    """
    with _lock:
        now = time.time()
        state = _load(now)
        per_task = state.setdefault("per_task", {})

        final = reservation.estimated if actual is None else max(0.0, float(actual))
        if outcome not in ("ok",):
            final = 0.0

        delta = final - reservation.estimated
        state["spent"] = max(0.0, float(state.get("spent", 0.0)) + delta)
        per_task[reservation.task] = max(
            0.0, float(per_task.get(reservation.task, 0.0)) + delta
        )
        _save(state)

    reservation.settled = True
    reservation.actual = final
    reservation.outcome = outcome
    reservation.usage = usage or {}
    if abs((actual or 0.0) - reservation.estimated) > 1e-9 and actual is not None:
        logger.info(
            "budget: reconciled %s/%s estimate %.6f -> actual %.6f",
            reservation.provider, reservation.model, reservation.estimated, final,
        )
    return reservation


def release(reservation: Reservation) -> None:
    """Refund a reservation that was never dispatched."""
    if reservation.settled:
        return
    settle(reservation, actual=0.0, outcome="released")


def usage_summary() -> dict[str, Any]:
    """Current period spend, for the usage endpoint and diagnostics."""
    with _lock:
        state = _load(time.time())
    limits = ceilings()
    return {
        "enabled": enabled(),
        "spent": round(float(state.get("spent", 0.0)), 6),
        "per_task": {k: round(float(v), 6) for k, v in state.get("per_task", {}).items()},
        "ceilings": limits,
        "period_s": period_s(),
        "unknown_policy": unknown_policy(),
    }


def reset() -> None:
    """Clear recorded spend. For tests and for starting a new period."""
    with _lock:
        _save({"period_start": time.time(), "spent": 0.0, "per_task": {}})
