"""
llm.py — Single LLM entry point with automatic 429 failover.

Priority chain:
  1. Gemini (google-generativeai, free AI Studio key)
  2. Groq  (groq SDK or requests, free tier)
  3. OpenRouter :free models (requests)

All callers import only `complete()` and `complete_json()`.
No secrets are ever logged.
"""
import json
import logging
import os
import time
from typing import Any


def _load_env_file() -> None:
    """Populate os.environ from the project .env. Delegates to config."""
    from agent.modules.config import load_env_file

    load_env_file()

_load_env_file()

import requests
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from agent.modules import provider_errors

logger = logging.getLogger(__name__)

# ─── Model config ─────────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-flash-lite-latest"       # free tier Flash (high reliability)
GROQ_MODEL = "openai/gpt-oss-120b"              # 120B model on Groq
GROQ_FAST_MODEL = "qwen/qwen3.8-27b"            # Fast Groq model
OPENROUTER_MODEL = "meta-llama/llama-3.1-70b-instruct:free"

_MAX_RETRIES = 3
_BACKOFF_MIN = 2
_BACKOFF_MAX = 30

# Process-local telemetry: the model id the provider actually answered with,
# as reported in its own response body. Set by each backend after a successful
# call so callers can record what was *used*, not what was requested — a
# provider may serve a request with a different model than the one asked for.
# Not thread-safe by design; the pipeline issues one request at a time.
LAST_ACTUAL_MODEL: str | None = None


def _note_actual_model(model_id: str | None) -> None:
    global LAST_ACTUAL_MODEL
    LAST_ACTUAL_MODEL = model_id or None


# Reported token usage for the most recent call, when the provider supplies it.
# Used to reconcile a budget estimate against what was actually consumed.
LAST_USAGE: dict | None = None


def _note_usage(usage: dict | None) -> None:
    global LAST_USAGE
    LAST_USAGE = usage if isinstance(usage, dict) else None


class LLMError(Exception):
    """Raised when all providers fail."""


# ─── Public API ───────────────────────────────────────────────────────────────

def complete(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    task: str = "scriptwriting",
) -> str:
    """
    Return a text completion. Tries Gemini → Groq → OpenRouter on 429/failure.
    Raises LLMError only if all three fail.

    Set LLM_ROUTER=true to route through agent.router instead, which consults
    the provider registry, per-provider health and the quota tracker. The chain
    below stays the default so the working path is never changed implicitly.

    `task` selects the routing-table entry and the per-task model
    configuration; complete_json() goes through here too, so both entry points
    share one execution path.
    """
    from agent.router import router_enabled

    if router_enabled():
        from agent.router import complete as _routed_complete

        return _routed_complete(
            prompt, system, temperature, max_tokens, task=task
        )

    errors: list[str] = []

    # 1. Gemini
    if os.environ.get("GEMINI_API_KEY"):
        try:
            return _gemini(prompt, system, temperature, max_tokens)
        except _RateLimited as e:
            logger.warning("llm: Gemini rate-limited (%s), falling back to Groq", e)
            errors.append(f"Gemini: {e}")
        except Exception as e:
            logger.warning("llm: Gemini failed (%s), falling back to Groq", e)
            errors.append(f"Gemini: {e}")

    # 2. Groq
    if os.environ.get("GROQ_API_KEY"):
        try:
            return _groq(prompt, system, temperature, max_tokens)
        except _RateLimited as e:
            logger.warning("llm: Groq rate-limited (%s), falling back to OpenRouter", e)
            errors.append(f"Groq: {e}")
        except Exception as e:
            logger.warning("llm: Groq failed (%s), falling back to OpenRouter", e)
            errors.append(f"Groq: {e}")

    # 3. OpenRouter :free
    if os.environ.get("OPENROUTER_API_KEY"):
        try:
            return _openrouter(prompt, system, temperature, max_tokens)
        except Exception as e:
            logger.error("llm: OpenRouter failed: %s", e)
            errors.append(f"OpenRouter: {e}")

    raise LLMError("All LLM providers failed: " + "; ".join(errors))


def _strip_code_fences(raw: str) -> str:
    """Remove a surrounding markdown code fence, if the model added one."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def complete_json(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    task: str = "scriptwriting",
) -> Any:
    """
    Like complete() but parses the response as JSON.
    Strips markdown code fences if present.
    Retries if the response is not parseable.

    `task` selects routing and per-task model configuration. Both entry points
    share one execution path, so a model selected for the task is the model
    that is actually requested.
    """
    last_exc: Exception | None = None
    for attempt in range(2):
        # Goes through complete(), so both entry points share one routing and
        # execution path. Previously this built its own provider list from
        # env-var presence, which meant a routed model selection, health
        # filtering and the quota tracker were all bypassed for JSON calls.
        raw = complete(prompt, system, temperature, max_tokens, task=task)
        try:
            return json.loads(_strip_code_fences(raw))
        except Exception as exc:  # noqa: BLE001 - parse failures are expected
            last_exc = exc
            logger.warning(
                "llm: JSON attempt %d unparseable: %s", attempt + 1, exc
            )
            time.sleep(1)

    raise LLMError(f"All providers failed to produce valid JSON: {last_exc}")


# ─── Provider implementations ─────────────────────────────────────────────────

class _RateLimited(provider_errors.RateLimitError):
    """
    Retained because existing callers and tests catch it by name.

    Now a RateLimitError, so `isinstance` checks and the retry predicate treat
    it consistently with every other classified failure.
    """

    def __init__(self, message: str = "", provider: str = "unknown") -> None:
        super().__init__(provider, message or "rate limited")


def _post(url: str, provider: str, **kwargs) -> requests.Response:
    """
    POST with transport failures classified instead of left raw.

    Without this, a Timeout or ConnectionError escaped the retry predicate
    (which now keys off the taxonomy rather than RequestException) and would
    not be retried at all.
    """
    try:
        return requests.post(url, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise provider_errors.classify_exception(provider, exc) from exc


def _json(resp: requests.Response, provider: str) -> dict:
    """Parse a JSON body, or raise MalformedResponseError rather than KeyError."""
    try:
        return resp.json()
    except Exception as exc:  # any decode failure is a malformed response
        raise provider_errors.MalformedResponseError(
            provider, "response was not valid JSON", detail=str(exc)
        ) from exc


def _extract(data: dict, provider: str, *path: str) -> str:
    """
    Pull a nested string out of a response body.

    A 200 whose shape is unexpected is a malformed response, not a crash: a
    bare KeyError here would propagate as an unclassified exception and the
    retry predicate would treat it as a bug rather than a provider fault.
    """
    node: object = data
    try:
        for part in path:
            node = node[part] if isinstance(node, dict) else node[int(part)]
        if not isinstance(node, str):
            node = str(node)
        return node
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise provider_errors.MalformedResponseError(
            provider, f"response missing {'.'.join(path)}", detail=str(exc)
        ) from exc


def _raise_for(provider: str, resp: requests.Response) -> None:
    """Classify a non-2xx response and raise the matching taxonomy error."""
    err = provider_errors.classify_http(provider, resp.status_code, resp.text)
    logger.warning("llm: %s %s (HTTP %s): %s",
                   provider, err.category, resp.status_code, err.detail)
    raise err



def _gemini(
    prompt: str,
    system: str | None,
    temperature: float,
    max_tokens: int,
    model: str | None = None,
) -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise LLMError("GEMINI_API_KEY is empty")

    PROVIDER = "google-gemini"
    # An explicit model wins; otherwise the historical default is unchanged.
    model_id = model or GEMINI_MODEL
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={key}"
    payload: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if system:
        payload["system_instruction"] = {"parts": [{"text": system}]}

    @retry(
        retry=retry_if_exception(provider_errors.is_retryable),
        stop=stop_after_attempt(_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=_BACKOFF_MIN, max=_BACKOFF_MAX),
        reraise=True,
    )
    def _call() -> str:
        resp = _post(url, PROVIDER, json=payload, timeout=30)
        # The legacy default retried against gemini-2.0-flash on a 404. Kept
        # only when no model was explicitly requested: swapping models under an
        # explicit request would make telemetry claim a model that never ran.
        if resp.status_code == 404 and model is None:
            alt = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.0-flash:generateContent?key={key}"
            )
            r2 = _post(alt, PROVIDER, json=payload, timeout=30)
            if r2.status_code == 200:
                data2 = _json(r2, PROVIDER)
                _note_actual_model(data2.get("modelVersion") or "gemini-2.0-flash")
                return _extract(data2, PROVIDER,
                                "candidates", 0, "content", "parts", 0, "text")
        if resp.status_code != 200:
            _raise_for(PROVIDER, resp)
        data = _json(resp, PROVIDER)
        _note_actual_model(data.get("modelVersion") or model_id)
        _note_usage(data.get("usageMetadata"))
        return _extract(data, PROVIDER,
                        "candidates", 0, "content", "parts", 0, "text")

    return _call()


def _groq(
    prompt: str,
    system: str | None,
    temperature: float,
    max_tokens: int,
    model: str | None = None,
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    PROVIDER = "groq"
    model_id = model or GROQ_MODEL

    @retry(
        retry=retry_if_exception(provider_errors.is_retryable),
        stop=stop_after_attempt(_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=_BACKOFF_MIN, max=_BACKOFF_MAX),
        reraise=True,
    )
    def _call() -> str:
        resp = _post(
            "https://api.groq.com/openai/v1/chat/completions", PROVIDER,
            headers={
                "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_id,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        if resp.status_code != 200:
            _raise_for(PROVIDER, resp)
        data = _json(resp, PROVIDER)
        _note_actual_model(data.get("model") or model_id)
        _note_usage(data.get("usage"))
        return _extract(data, PROVIDER, "choices", 0, "message", "content")

    return _call()


def _openrouter(
    prompt: str,
    system: str | None,
    temperature: float,
    max_tokens: int,
    model: str | None = None,
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    PROVIDER = "openrouter"
    model_id = model or OPENROUTER_MODEL

    @retry(
        retry=retry_if_exception(provider_errors.is_retryable),
        stop=stop_after_attempt(_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=_BACKOFF_MIN, max=_BACKOFF_MAX),
        reraise=True,
    )
    def _call() -> str:
        resp = _post(
            "https://openrouter.ai/api/v1/chat/completions", PROVIDER,
            headers={
                "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/shorts-agent",
                "X-Title": "Shorts Agent",
            },
            json={
                "model": model_id,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        if resp.status_code != 200:
            _raise_for(PROVIDER, resp)
        data = _json(resp, PROVIDER)
        # OpenRouter routes a request to whichever upstream serves the slug, and
        # reports the concrete model it settled on. Record that, not the slug.
        _note_actual_model(data.get("model") or model_id)
        _note_usage(data.get("usage"))
        return _extract(data, PROVIDER, "choices", 0, "message", "content")

    return _call()
