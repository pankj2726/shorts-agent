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

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# ─── Model config ─────────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.0-flash-exp"          # free tier Flash
GROQ_MODEL = "llama-3.1-70b-versatile"         # 1k req/day free
GROQ_FAST_MODEL = "llama-3.1-8b-instant"       # 14.4k req/day free
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
    max_tokens: int = 1024,
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
    max_tokens: int = 2048,
) -> Any:
    """
    Like complete() but parses the response as JSON.
    Strips markdown code fences if present.
    """
    raw = complete(prompt, system=system, temperature=temperature, max_tokens=max_tokens)
    text = raw.strip()
    # Strip ```json ... ``` fences
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first and last fence line
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM returned non-JSON: {exc}\nRaw: {text[:500]}") from exc


# ─── Provider implementations ─────────────────────────────────────────────────

class _RateLimited(Exception):
    pass


def _gemini(prompt: str, system: str | None, temperature: float, max_tokens: int) -> str:
    import google.generativeai as genai  # type: ignore

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(
        GEMINI_MODEL,
        system_instruction=system or "",
    )
    cfg = genai.GenerationConfig(temperature=temperature, max_output_tokens=max_tokens)

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=_BACKOFF_MIN, max=_BACKOFF_MAX),
        reraise=True,
    )
    def _call() -> str:
        response = model.generate_content(prompt, generation_config=cfg)
        return response.text

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
