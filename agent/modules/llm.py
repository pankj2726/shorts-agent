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
from pathlib import Path
from typing import Any

def _load_env_file() -> None:
    candidates = [
        Path(r"C:\Secrets\shorts-agent.env"),
        Path(r"C:\Secrets\.env"),
        Path(__file__).parent.parent.parent / ".env",
        Path(".env"),
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k and not os.environ.get(k):
                            os.environ[k] = v
            except Exception:
                pass
            break

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


class LLMError(Exception):
    """Raised when all providers fail."""


# ─── Public API ───────────────────────────────────────────────────────────────

def complete(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> str:
    """
    Return a text completion. Tries Gemini → Groq → OpenRouter on 429/failure.
    Raises LLMError only if all three fail.
    """
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


def complete_json(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> Any:
    """
    Like complete() but parses the response as JSON.
    Strips markdown code fences if present.
    Retries or fails over if invalid JSON is received.
    """
    providers = []
    if os.environ.get("GEMINI_API_KEY"):
        providers.append(("Gemini", _gemini))
    if os.environ.get("GROQ_API_KEY"):
        providers.append(("Groq", _groq))
    if os.environ.get("OPENROUTER_API_KEY"):
        providers.append(("OpenRouter", _openrouter))

    last_exc = None
    for name, provider_fn in providers:
        for attempt in range(2):
            try:
                raw = provider_fn(prompt, system, temperature, max_tokens)
                text = raw.strip()
                if text.startswith("```"):
                    lines = text.splitlines()
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    text = "\n".join(lines).strip()
                return json.loads(text)
            except Exception as exc:
                last_exc = exc
                logger.warning("llm: %s JSON attempt %d failed: %s", name, attempt + 1, exc)
                time.sleep(1)

    raise LLMError(f"All providers failed to produce valid JSON: {last_exc}")


# ─── Provider implementations ─────────────────────────────────────────────────

class _RateLimited(Exception):
    pass


def _gemini(prompt: str, system: str | None, temperature: float, max_tokens: int) -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise LLMError("GEMINI_API_KEY is empty")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={key}"
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
            if resp.status_code == 404:
                # Fallback to gemini-2.0-flash if 1.5-flash not found
                alt_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
                r2 = requests.post(alt_url, json=payload, timeout=30)
                if r2.status_code == 200:
                    return r2.json()["candidates"][0]["content"]["parts"][0]["text"]
            logger.warning("llm: Gemini error: %s", resp.text[:300])
            resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    try:
        return _call()
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            raise _RateLimited(e) from e
        raise


def _groq(prompt: str, system: str | None, temperature: float, max_tokens: int) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

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
                "model": GROQ_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        if resp.status_code == 429:
            raise _RateLimited(f"HTTP 429 from Groq")
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    try:
        return _call()
    except _RateLimited:
        raise
    except Exception as e:
        if "429" in str(e):
            raise _RateLimited(e) from e
        raise


def _openrouter(prompt: str, system: str | None, temperature: float, max_tokens: int) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

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
                "model": OPENROUTER_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    return _call()
