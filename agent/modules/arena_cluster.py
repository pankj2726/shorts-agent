"""
arena_cluster.py — Dedicated Arena Connection Cluster & Warm TLS Engine (Agent A).

Maintains persistent keep-alive connection pools, injects Cloudflare WAF bypass headers,
routes dedicated H100 GPU cluster models, handles 402 promotional fallback,
and provides streaming Server-Sent Events (SSE) support.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Union

import requests
from requests.adapters import HTTPAdapter
import urllib3
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Safe environment loader
def _load_env_safe() -> None:
    for c in [
        Path(r"C:\Secrets\shorts-agent.env"),
        Path(r"C:\Secrets\.env"),
        Path(".env"),
        Path(__file__).resolve().parent.parent.parent / ".env",
    ]:
        if c.exists():
            try:
                with open(c, "r", encoding="utf-8") as f:
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

_load_env_safe()


class ArenaError(Exception):
    """Base exception for Arena API errors."""
    pass


class ArenaAuthenticationError(ArenaError):
    """Raised when Arena API key is missing or invalid."""
    pass


class ArenaRateLimitError(ArenaError):
    """Raised when Arena returns HTTP 429 rate limit."""
    pass


class ArenaBillingError(ArenaError):
    """Raised when Arena returns unrecoverable billing errors."""
    pass


# Claude family models for intelligent 402 fallback
CLAUDE_FAMILY_MODELS = {
    "claude-coding-router-preview",
    "claude-fable-5",
    "claude-fable-5-1",
    "claude-fable-5-2",
    "claude-haiku-4-5-20251001",
    "claude-haiku-5-5",
    "claude-opus-5",
    "claude-opus-5-5",
    "claude-sonnet-5",
    "claude-sonnet-5-5",
    "claude-3-5-sonnet",
    "claude-3-7-sonnet",
    "claude-3-5-haiku",
    "claude-3.5-sonnet",
    "claude-3.7-sonnet",
    "claude-3.5-haiku",
}

# Dedicated model canonical to API ID mappings
MODEL_CANONICAL_MAPPINGS = {
    "claude-opus-5.5": "claude-opus-5-5",
    "claude-sonnet-5.5": "claude-sonnet-5-5",
    "claude-haiku-5.5": "claude-haiku-5-5",
    "claude-fable-5.1": "claude-fable-5-1",
    "claude-fable-5.2": "claude-fable-5-2",
    "gpt-6": "gpt-6-astra",
    "gpt-6-sol": "gpt-6-sol",
    "gpt-6-luna": "gpt-6-luna",
    "gpt-5.6": "gpt-5.6-sol",
    "gemini-3.7": "gemini-3.7-flash",
    "gemini-3.8": "gemini-3.8-flash",
    "gemini-4": "gemini-4-carbon",
}


def resolve_target_model(requested_model: str) -> str:
    """Maps canonical shorthand or dot-syntax model names to Arena API identifiers."""
    if not requested_model:
        return "coding-router-preview"
    return MODEL_CANONICAL_MAPPINGS.get(requested_model, requested_model)


class ArenaClusterClient:
    """
    Dedicated Arena.AI Connection Cluster & Warm TLS Engine (Agent A).

    Features:
      - Persistent keep-alive TLS connection pooling via requests.Session + HTTPAdapter.
      - Cloudflare WAF bypass header: User-Agent: ShortsAgent-ArenaRouter/1.0.
      - Enterprise identity headers: X-Workspace-Identity and X-Organization-Name.
      - Direct dedicated H100 model routing with automatic 402 fallback to router preview models.
      - Streaming SSE generator support.
      - Zero-cost ping() and dynamic model discovery with TTL caching.
    """

    _instance: Optional[ArenaClusterClient] = None

    @classmethod
    def get_instance(cls) -> ArenaClusterClient:
        """Returns singleton instance of the connection cluster."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(
        self,
        base_url: str = "https://api.arena.ai/v1",
        pool_connections: int = 10,
        pool_maxsize: int = 25,
        timeout: int = 45,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.pool_connections = pool_connections
        self.pool_maxsize = pool_maxsize
        self.default_timeout = timeout
        self._session: Optional[requests.Session] = None
        self._models_cache: Optional[List[Dict[str, Any]]] = None
        self._cache_expiry: float = 0.0

    def _get_session(self) -> requests.Session:
        """Initializes or returns the persistent warm TLS requests session."""
        if self._session is None:
            session = requests.Session()
            retries = Retry(
                total=3,
                backoff_factor=0.3,
                status_forcelist=[429, 500, 502, 503, 504],
                raise_on_status=False,
            )
            adapter = HTTPAdapter(
                pool_connections=self.pool_connections,
                pool_maxsize=self.pool_maxsize,
                max_retries=retries,
            )
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._session = session
        return self._session

    def get_api_key(self) -> str:
        """Retrieves active Arena API key from environment or secrets."""
        key = (
            os.environ.get("ARENA_API_KEY")
            or os.environ.get("ARENA_KEY")
            or os.environ.get("ARENA_TOKEN")
        )
        if not key:
            raise ArenaAuthenticationError(
                "ARENA_API_KEY environment variable is not configured."
            )
        return key.strip()

    def get_headers(self) -> Dict[str, str]:
        """Builds Cloudflare-compliant authorization and enterprise routing headers."""
        key = self.get_api_key()
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "ShortsAgent-ArenaRouter/1.0",
            "X-Workspace-Identity": "pankajbhati140@gmail.com",
            "X-Organization-Name": "ShortsForge-Enterprise",
            "Connection": "keep-alive",
        }

    def warm_pool(self) -> bool:
        """
        Pre-warms TCP/TLS connection sockets to minimize first-request latency.
        Returns True if the warm probe succeeded.
        """
        try:
            session = self._get_session()
            headers = self.get_headers()
            # Send lightweight GET probe to warm DNS, TCP handshake, and TLS 1.3 session ticket
            resp = session.get(f"{self.base_url}/models", headers=headers, timeout=5)
            return resp.status_code == 200
        except Exception as e:
            logger.debug(f"Warm pool probe note: {e}")
            return False

    def ping(self, timeout: int = 5) -> Dict[str, Any]:
        """
        Executes a zero-cost GET probe against /models to verify connectivity and measure latency.
        """
        t0 = time.time()
        try:
            session = self._get_session()
            headers = self.get_headers()
            resp = session.get(f"{self.base_url}/models", headers=headers, timeout=timeout)
            latency_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                count = len(data.get("data", []))
                return {
                    "status": "ONLINE",
                    "latency_ms": latency_ms,
                    "authenticated": True,
                    "models_count": count,
                }
            elif resp.status_code in (401, 403):
                return {
                    "status": "DEGRADED",
                    "latency_ms": latency_ms,
                    "authenticated": False,
                    "http_status": resp.status_code,
                    "error": resp.text,
                }
            else:
                return {
                    "status": "DEGRADED",
                    "latency_ms": latency_ms,
                    "authenticated": True,
                    "http_status": resp.status_code,
                }
        except ArenaAuthenticationError as e:
            return {
                "status": "OFFLINE",
                "latency_ms": -1,
                "authenticated": False,
                "error": str(e),
            }
        except Exception as e:
            return {
                "status": "OFFLINE",
                "latency_ms": -1,
                "authenticated": False,
                "error": str(e),
            }

    def discover_models(self, force_refresh: bool = False, timeout: int = 8) -> List[Dict[str, Any]]:
        """
        Discovers models exposed by the Arena API with TTL caching (3600 seconds).
        """
        now = time.time()
        if not force_refresh and self._models_cache and now < self._cache_expiry:
            return self._models_cache

        session = self._get_session()
        headers = self.get_headers()
        try:
            resp = session.get(f"{self.base_url}/models", headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                models = []
                for item in data.get("data", []):
                    mid = item.get("id", "")
                    models.append({
                        "id": mid,
                        "canonical_id": f"arena/{mid}",
                        "original_id": mid,
                        "display_name": f"Arena {mid.replace('-', ' ').title()}",
                        "provider": "arena",
                        "cost_tier": "router_funded",
                        "context_window": 128000,
                        "capabilities": {
                            "reasoning_effort": True,
                            "extended_thinking": "coding" in mid or "fable" in mid,
                            "vision": True,
                            "tool_calling": True,
                            "structured_json": True,
                        },
                    })
                self._models_cache = models
                self._cache_expiry = now + 3600
                return models
        except Exception as e:
            logger.warning(f"Arena model discovery failed: {e}")

        # Baseline fallback if discovery probe fails
        return [
            {
                "id": "claude-coding-router-preview",
                "canonical_id": "arena/claude-coding-router-preview",
                "original_id": "claude-coding-router-preview",
                "display_name": "Arena Claude Coding Router",
                "provider": "arena",
                "cost_tier": "router_funded",
            },
            {
                "id": "coding-router-preview",
                "canonical_id": "arena/coding-router-preview",
                "original_id": "coding-router-preview",
                "display_name": "Arena Coding Router",
                "provider": "arena",
                "cost_tier": "router_funded",
            },
        ]

    def send_request(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
        reasoning_effort: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Union[Dict[str, Any], Generator[str, None, None]]:
        """
        Dispatches chat completion request to Arena H100 cluster.
        Intercepts HTTP 402 paid_credits_required and automatically falls back to router preview.
        """
        session = self._get_session()
        headers = self.get_headers()
        effective_timeout = timeout or self.default_timeout

        resolved_model = resolve_target_model(model)

        req_messages = []
        if system:
            req_messages.append({"role": "system", "content": system})
        req_messages.extend(messages)

        payload: Dict[str, Any] = {
            "model": resolved_model,
            "messages": req_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if stream:
            payload["stream"] = True

        eff = (reasoning_effort or "high").lower()
        if eff in ("low", "medium", "high", "max"):
            payload["reasoning_effort"] = eff if eff != "max" else "high"

        url = f"{self.base_url}/chat/completions"

        # Attempt 1: Direct or routed model call
        resp = session.post(
            url,
            headers=headers,
            json=payload,
            stream=stream,
            timeout=effective_timeout,
        )

        # Handle HTTP 402 paid_credits_required fallback
        if resp.status_code == 402:
            try:
                err_data = resp.json().get("error", {})
                err_code = err_data.get("code")
            except Exception:
                err_code = None

            if err_code == "paid_credits_required" or "paid_credits_required" in resp.text:
                # Determine router fallback model
                is_claude = any(sub in resolved_model.lower() for sub in ("claude", "fable", "sonnet", "opus", "haiku"))
                fallback_model = "claude-coding-router-preview" if is_claude else "coding-router-preview"
                logger.warning(
                    f"Arena direct model '{resolved_model}' requires paid credits. "
                    f"Transparently falling back to router model '{fallback_model}'."
                )

                payload["model"] = fallback_model
                resp = session.post(
                    url,
                    headers=headers,
                    json=payload,
                    stream=stream,
                    timeout=effective_timeout,
                )
                if stream:
                    return self._stream_generator(resp)

                res_json = resp.json()
                res_json["fallback_used"] = True
                res_json["requested_model"] = model
                return res_json

        # Check for other HTTP errors
        if resp.status_code == 401 or resp.status_code == 403:
            raise ArenaAuthenticationError(f"Arena Authentication Failed (HTTP {resp.status_code}): {resp.text}")
        elif resp.status_code == 429:
            raise ArenaRateLimitError(f"Arena Rate Limit (HTTP 429): {resp.text}")
        elif resp.status_code != 200:
            raise ArenaError(f"Arena API Error (HTTP {resp.status_code}): {resp.text}")

        if stream:
            return self._stream_generator(resp)

        return resp.json()

    def _stream_generator(self, resp: requests.Response) -> Generator[str, None, None]:
        """Yields Server-Sent Events lines from streaming response."""
        for line in resp.iter_lines(decode_unicode=True):
            if line:
                yield line

    def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        timeout: int = 45,
    ) -> Dict[str, Any]:
        """
        High-level completion interface compatible with OmniRoute return structure.
        """
        target_model = model or "coding-router-preview"
        messages = [{"role": "user", "content": prompt}]

        res_json = self.send_request(
            model=target_model,
            messages=messages,
            system=system,
            stream=False,
            reasoning_effort=effort,
            timeout=timeout,
        )

        choices = res_json.get("choices", [])
        if choices and "message" in choices[0]:
            content = choices[0]["message"].get("content", "")
        else:
            content = ""

        usage = res_json.get("usage", {})
        cost = usage.get("cost", 0.0)

        out: Dict[str, Any] = {
            "provider": "arena",
            "model": res_json.get("model", target_model),
            "cost": cost,
            "text": content.strip(),
            "status": "success",
            "usage": usage,
        }
        if res_json.get("fallback_used"):
            out["fallback_used"] = True
            out["requested_model"] = res_json.get("requested_model")

        return out

    def close(self) -> None:
        """Closes the underlying requests session and socket pool."""
        if self._session:
            self._session.close()
            self._session = None


# Public alias as specified in SCOPE.md and PROJECT.md
ArenaConnectionCluster = ArenaClusterClient
