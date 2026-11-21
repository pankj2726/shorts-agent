"""
provider_errors.py — one taxonomy for every provider failure.

Before this, a 401 from a provider was retried three times with exponential
backoff exactly like a read timeout, because tenacity was configured on
`requests.exceptions.RequestException` and raise_for_status() turns any 4xx
into that. Retrying an authentication failure cannot succeed, wastes the
backoff budget, and then falls through to the next provider — which fails the
same way for the same reason.

Everything here is classified from the status code and, where the code is
ambiguous, from narrow markers in the body. Markers are matched as substrings
rather than parsed as provider schemas, because those schemas differ per
provider and are not documented consistently enough to rely on. Where a case
cannot be distinguished, it falls back to the conservative reading: retryable
only when there is positive evidence that retrying could help.

Error messages never carry credentials or unbounded response bodies. `_redact`
scrubs anything that looks like a key and the body is truncated.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Longest slice of a provider body kept in an error message. Bodies can embed
# the request, including headers, so this is deliberately small.
MAX_DETAIL_CHARS = 200

# Anything that looks like a bearer token, API key or authorization header.
_SENSITIVE_PATTERNS = (
    # Credential shapes first. Redacting only the keyword ("Authorization:")
    # would consume the label and leave the secret itself in the message.
    re.compile(r"(?i)\b(?:sk|pk|key|token|ghp|gho|ghs|xox[baprs]|AIza)[-_]"
               r"[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"(?i)\b(bearer|api[_-]?key|authorization|token|secret|password)\b"
               r"\s*[:=]?\s*\S+"),
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
)


def _redact(text: str) -> str:
    """Strip credential-shaped material and bound the length."""
    if not text:
        return ""
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    text = " ".join(text.split())
    if len(text) > MAX_DETAIL_CHARS:
        text = text[:MAX_DETAIL_CHARS] + "..."
    return text


class ProviderError(Exception):
    """
    Base for every classified provider failure.

    `retryable` is a class attribute: whether retrying the *same* provider
    could plausibly succeed. Callers must consult it before retrying or
    falling back.
    """

    retryable = False
    category = "provider_error"

    def __init__(
        self,
        provider: str,
        message: str,
        status_code: int | None = None,
        detail: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        self.detail = _redact(detail or "")
        self.retry_after = retry_after
        super().__init__(f"{provider}: {message}")

    def as_dict(self) -> dict[str, Any]:
        """Telemetry-safe view. Contains no credential material."""
        return {
            "category": self.category,
            "provider": self.provider,
            "status_code": self.status_code,
            "retryable": self.retryable,
            "detail": self.detail,
        }


# ─── Configuration and credential failures: never retryable ──────────────────

class AuthenticationError(ProviderError):
    """401/403. The credential is missing, invalid or unauthorized."""

    retryable = False
    category = "authentication"


class BillingError(ProviderError):
    """402 or explicit insufficient-credit language. Needs a human."""

    retryable = False
    category = "billing"


class UnsupportedModelError(ProviderError):
    """The model id does not exist or is not available to this account."""

    retryable = False
    category = "unsupported_model"


class InvalidRequestError(ProviderError):
    """400/422. The request itself is malformed; retrying repeats the error."""

    retryable = False
    category = "invalid_request"


class MalformedResponseError(ProviderError):
    """A 200 whose body cannot be parsed into the expected shape."""

    retryable = False
    category = "malformed_response"


class QuotaExhaustedError(ProviderError):
    """
    A limit that resets on a schedule longer than any sane retry window.

    Kept distinct from RateLimitError: a per-minute rate limit is worth a short
    wait, a daily allowance is not, and conflating them makes the router burn
    backoff budget against a provider that will not recover today.
    """

    retryable = False
    category = "quota_exhausted"


# ─── Transient failures: retryable ───────────────────────────────────────────

class RateLimitError(ProviderError):
    """429 that is a rate limit rather than an exhausted allowance."""

    retryable = True
    category = "rate_limit"


class RequestTimeoutError(ProviderError):
    """The provider did not answer in time."""

    retryable = True
    category = "timeout"


class ConnectionFailureError(ProviderError):
    """DNS failure, refused connection, reset, or no route to the provider."""

    retryable = True
    category = "connection"


class ServerError(ProviderError):
    """5xx. The provider's problem, and usually transient."""

    retryable = True
    category = "server_error"


class CircuitOpenError(ProviderError):
    """
    The provider's breaker is open, so the call was not attempted.

    Retryable in the sense that a later attempt may succeed once the cooldown
    elapses, but not within the same request.
    """

    retryable = False
    category = "circuit_open"


class BudgetExceededError(ProviderError):
    """A cost ceiling would be exceeded, so the call was not dispatched."""

    retryable = False
    category = "budget_exceeded"


# ─── Classification ──────────────────────────────────────────────────────────

# Narrow markers. A 429 means "rate limit" unless the body says the allowance
# is spent, in which case waiting seconds will not help.
_QUOTA_MARKERS = (
    "daily", "day limit", "quota", "exceeded your current quota",
    "resource_exhausted", "resourceexhausted", "insufficient_quota",
    "allowance", "spending limit",
)
_BILLING_MARKERS = (
    "insufficient credit", "insufficient credits", "insufficient balance",
    "billing", "payment required", "no credits", "credit balance",
    "you must have a paid", "upgrade your plan",
)
_MODEL_MARKERS = (
    "model_not_found", "model not found", "does not exist", "unknown model",
    "no such model", "unsupported model", "not a valid model",
)


def _has(body: str, markers: tuple[str, ...]) -> bool:
    lowered = (body or "").lower()
    return any(marker in lowered for marker in markers)


def classify_http(provider: str, status_code: int, body: str = "") -> ProviderError:
    """
    Map a non-2xx response onto the taxonomy.

    Never raises for a 2xx; callers should only invoke this on failures.
    """
    body = body or ""

    if status_code in (401, 403):
        return AuthenticationError(provider, "authentication or authorization failed",
                                   status_code, body)
    if status_code == 402:
        return BillingError(provider, "billing or credit problem", status_code, body)
    if status_code == 404:
        # A 404 on a chat/completions call is almost always the model id, not
        # the endpoint, so it is reported as an unsupported model.
        return UnsupportedModelError(provider, "model or endpoint not found",
                                     status_code, body)
    if status_code in (400, 422):
        if _has(body, _MODEL_MARKERS):
            return UnsupportedModelError(provider, "model rejected by provider",
                                         status_code, body)
        return InvalidRequestError(provider, "provider rejected the request",
                                   status_code, body)
    if status_code == 429:
        # Billing language wins over quota language, which wins over the
        # default rate-limit reading.
        if _has(body, _BILLING_MARKERS):
            return BillingError(provider, "429 with billing markers",
                                status_code, body)
        if _has(body, _QUOTA_MARKERS):
            return QuotaExhaustedError(provider, "429 with quota markers",
                                       status_code, body)
        return RateLimitError(provider, "rate limited", status_code, body)
    if 500 <= status_code <= 599:
        return ServerError(provider, "provider server error", status_code, body)

    # Anything unrecognised is treated as non-retryable. Guessing "transient"
    # here would turn an unknown permanent failure into a retry loop.
    return InvalidRequestError(provider, f"unexpected status {status_code}",
                               status_code, body)


def classify_exception(provider: str, exc: BaseException) -> ProviderError:
    """Map a transport-level exception onto the taxonomy."""
    import requests

    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, requests.exceptions.Timeout):
        return RequestTimeoutError(provider, "request timed out", detail=str(exc))
    if isinstance(exc, requests.exceptions.ConnectionError):
        return ConnectionFailureError(provider, "connection failed", detail=str(exc))
    if isinstance(exc, requests.exceptions.RequestException):
        # A RequestException carrying a response has a real status code; use it
        # rather than discarding the most specific signal available.
        resp = getattr(exc, "response", None)
        if resp is not None:
            status = getattr(resp, "status_code", None)
            if isinstance(status, int):
                return classify_http(provider, status, _safe_text(resp))
        return ConnectionFailureError(provider, "request failed", detail=str(exc))
    if isinstance(exc, (KeyError, IndexError, ValueError, TypeError)):
        return MalformedResponseError(provider, "response could not be parsed",
                                      detail=str(exc))
    return ProviderError(provider, "uncategorised failure", detail=str(exc))


def _safe_text(resp: Any) -> str:
    """Response body without ever raising or leaking."""
    try:
        return resp.text or ""
    except Exception:  # noqa: BLE001 - never let redaction fail loudly
        return ""


def is_retryable(exc: BaseException) -> bool:
    """
    Whether the *same* provider should be tried again.

    Unknown exception types are not retried: the taxonomy above covers every
    failure the backends can produce, so an unclassified exception is more
    likely a bug than a transient fault.
    """
    if isinstance(exc, ProviderError):
        return exc.retryable
    return False
