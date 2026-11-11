"""
openai_adapter.py — Direct OpenAI & ChatGPT Organization API Adapter.

Integrates organization-authorized OpenAI credentials, dynamic model discovery,
native reasoning effort translation for o1/o3-mini, and normalized completion.
Zero reliance on consumer subscriptions; strictly honors provider API authorization.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _load_env_safe() -> None:
    for c in [Path(r"C:\Secrets\shorts-agent.env"), Path(r"C:\Secrets\.env"), Path(".env")]:
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


class OpenAIAdapter:
    """Official OpenAI Chat & Models API connector."""

    BASE_URL = "https://api.openai.com/v1"

    @classmethod
    def get_api_key(cls) -> Optional[str]:
        return (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("CHATGPT_API_KEY")
        )

    @classmethod
    def get_headers(cls) -> Dict[str, str]:
        key = cls.get_api_key()
        if not key:
            raise ValueError("OPENAI_API_KEY missing from environment/vault")

        headers = {
            "Authorization": f"Bearer {key.strip()}",
            "Content-Type": "application/json",
            "User-Agent": "ShortsForge-OpenAIAdapter/1.0",
        }
        org_id = os.environ.get("OPENAI_ORG_ID")
        if org_id:
            headers["OpenAI-Organization"] = org_id.strip()

        proj_id = os.environ.get("OPENAI_PROJECT_ID")
        if proj_id:
            headers["OpenAI-Project"] = proj_id.strip()

        return headers

    @classmethod
    def discover_models(cls, timeout: int = 8) -> List[Dict[str, Any]]:
        """
        Discovers models exposed by the connected OpenAI Organization account
        via GET https://api.openai.com/v1/models.
        """
        key = cls.get_api_key()
        if not key:
            return []

        try:
            req = urllib.request.Request(
                f"{cls.BASE_URL}/models",
                headers=cls.get_headers(),
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = []
                    for item in data.get("data", []):
                        mid = item.get("id", "")
                        # Filter to chat and reasoning models
                        if any(p in mid for p in ["gpt", "o1", "o3", "chatgpt"]):
                            models.append({
                                "id": mid,
                                "name": mid,
                                "provider": "openai",
                                "created": item.get("created"),
                                "owned_by": item.get("owned_by")
                            })
                    return models
        except Exception as e:
            logger.warning(f"OpenAI model discovery failed: {e}")
            return []
        return []

    @classmethod
    def complete(
        cls,
        model: str,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        timeout: int = 45,
    ) -> Dict[str, Any]:
        """
        Executes normalized chat completion against OpenAI endpoints.
        Translates reasoning effort for o1 and o3-mini models.
        """
        t0 = time.time()
        headers = cls.get_headers()

        # Format messages
        formatted_messages = []
        if system:
            # o1 and o3-mini prefer 'developer' or 'system' role
            is_reasoning_model = any(r in model.lower() for r in ["o1", "o3"])
            role = "developer" if is_reasoning_model else "system"
            formatted_messages.append({"role": role, "content": system})

        for m in messages:
            formatted_messages.append({
                "role": m.get("role", "user"),
                "content": m.get("content", "")
            })

        payload: Dict[str, Any] = {
            "model": model,
            "messages": formatted_messages,
        }

        # Apply reasoning effort or standard generation parameters
        is_reasoning = any(r in model.lower() for r in ["o1", "o3"])
        if is_reasoning:
            # o1/o3 do not support custom temperature
            if reasoning_effort and reasoning_effort.lower() in ["low", "medium", "high"]:
                payload["reasoning_effort"] = reasoning_effort.lower()
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["temperature"] = temperature
            payload["max_tokens"] = max_tokens

        req = urllib.request.Request(
            f"{cls.BASE_URL}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                body = json.loads(resp.read().decode("utf-8"))
                latency_ms = int((time.time() - t0) * 1000)

                choices = body.get("choices", [])
                text = choices[0].get("message", {}).get("content", "") if choices else ""
                usage = body.get("usage", {})

                return {
                    "text": text,
                    "status": "success",
                    "http_code": status,
                    "model": body.get("model", model),
                    "latency_ms": latency_ms,
                    "tokens_in": usage.get("prompt_tokens", 0),
                    "tokens_out": usage.get("completion_tokens", 0),
                    "provider": "openai",
                    "reasoning_effort": payload.get("reasoning_effort"),
                }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8") if e.fp else str(e)
            return {
                "text": "",
                "status": "error",
                "http_code": e.code,
                "error": f"OpenAI HTTP {e.code}: {err_body}",
                "model": model,
                "latency_ms": int((time.time() - t0) * 1000),
                "provider": "openai"
            }
        except Exception as ex:
            return {
                "text": "",
                "status": "error",
                "http_code": 500,
                "error": str(ex),
                "model": model,
                "latency_ms": int((time.time() - t0) * 1000),
                "provider": "openai"
            }
