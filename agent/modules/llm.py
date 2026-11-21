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
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

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

class _RateLimited(Exception):
    pass


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
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        stop=stop_after_attempt(_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=_BACKOFF_MIN, max=_BACKOFF_MAX),
        reraise=True,
    )
    def _call() -> str:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 429:
            raise _RateLimited(resp.text)
        if resp.status_code != 200:
            # The legacy default silently retried against gemini-2.0-flash on a
            # 404. That is kept only when no model was explicitly requested:
            # swapping models under an explicit request would make telemetry
            # claim a model that never served the call.
            if resp.status_code == 404 and model is None:
                alt_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
                r2 = requests.post(alt_url, json=payload, timeout=30)
                if r2.status_code == 200:
                    data2 = r2.json()
                    _note_actual_model(
                        data2.get("modelVersion") or "gemini-2.0-flash"
                    )
                    return data2["candidates"][0]["content"]["parts"][0]["text"]
            logger.warning("llm: Gemini error: %s", resp.text[:300])
            resp.raise_for_status()
        data = resp.json()
        _note_actual_model(data.get("modelVersion") or model_id)
        return data["candidates"][0]["content"]["parts"][0]["text"]

    try:
        return _call()
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            raise _RateLimited(e) from e
        raise


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
    model_id = model or GROQ_MODEL

    @retry(
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        stop=stop_after_attempt(_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=_BACKOFF_MIN, max=_BACKOFF_MAX),
        reraise=True,
    )
    def _call() -> str:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
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
        if resp.status_code == 429:
            raise _RateLimited(f"HTTP 429 from Groq")
        resp.raise_for_status()
        data = resp.json()
        _note_actual_model(data.get("model") or model_id)
        return data["choices"][0]["message"]["content"]

    try:
        return _call()
    except _RateLimited:
        raise
    except Exception as e:
        if "429" in str(e):
            raise _RateLimited(e) from e
        raise


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
    model_id = model or OPENROUTER_MODEL

    @retry(
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        stop=stop_after_attempt(_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=_BACKOFF_MIN, max=_BACKOFF_MAX),
        reraise=True,
    )
    def _call() -> str:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
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
        resp.raise_for_status()
        data = resp.json()
        # OpenRouter routes a request to whichever upstream serves the slug, and
        # reports the concrete model it settled on. Record that, not the slug.
        _note_actual_model(data.get("model") or model_id)
        return data["choices"][0]["message"]["content"]

    return _call()
