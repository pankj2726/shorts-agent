"""
anthropic_adapter.py — Direct Claude Console & Anthropic Organization API Adapter.

Integrates organization-authorized Anthropic credentials, dynamic model discovery,
native extended thinking / reasoning effort, and normalized message completion.
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

# Safe environment loader
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


class AnthropicAdapter:
    """Official Anthropic Messages & Models API connector."""

    BASE_URL = "https://api.anthropic.com/v1"
    API_VERSION = "2023-06-01"

    @classmethod
    def get_api_key(cls) -> Optional[str]:
        return (
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("CLAUDE_API_KEY")
            or os.environ.get("ANTHROPIC_OAT_TOKEN")
        )

    @classmethod
    def get_headers(cls) -> Dict[str, str]:
        key = cls.get_api_key()
        if not key:
            raise ValueError("ANTHROPIC_API_KEY missing from environment/vault")

        headers = {
            "x-api-key": key.strip(),
            "anthropic-version": cls.API_VERSION,
            "content-type": "application/json",
            "User-Agent": "ShortsForge-AnthropicAdapter/1.0",
        }
        org_id = os.environ.get("ANTHROPIC_ORG_ID")
        if org_id:
            headers["anthropic-organization-id"] = org_id.strip()

        return headers

    @classmethod
    def discover_models(cls, timeout: int = 8) -> List[Dict[str, Any]]:
        """
        Discovers models exposed by the connected Claude Console / Organization account
        via GET https://api.anthropic.com/v1/models.
        """
        key = cls.get_api_key()
        if not key:
            return []

        url = f"{cls.BASE_URL}/models"
        req = urllib.request.Request(url, headers=cls.get_headers(), method="GET")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                records = []
                for m in data.get("data", []):
                    mid = m.get("id", "")
                    records.append({
                        "canonical_id": mid,
                        "display_name": m.get("display_name", mid),
                        "provider": "anthropic",
                        "created_at": m.get("created_at"),
                        "capabilities": {
                            "extended_thinking": "sonnet" in mid or "opus" in mid,
                            "vision": True,
                            "tool_calling": True,
                            "structured_json": True,
                        }
                    })
                return records
        except Exception as e:
            logger.warning(f"Anthropic model discovery note: {e}")
            # Return baseline models if discovery probe is unconfigured or rate-limited
            return [
                {
                    "canonical_id": "claude-3-5-sonnet-20241022",
                    "display_name": "Claude 3.5 Sonnet (Latest)",
                    "provider": "anthropic",
                    "capabilities": {"extended_thinking": True, "vision": True, "tool_calling": True}
                },
                {
                    "canonical_id": "claude-3-5-haiku-20241022",
                    "display_name": "Claude 3.5 Haiku",
                    "provider": "anthropic",
                    "capabilities": {"extended_thinking": False, "vision": True, "tool_calling": True}
                }
            ]

    @classmethod
    def complete(
        cls,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        effort: Optional[str] = None,
        thinking_budget: Optional[int] = None,
        max_tokens: int = 4096,
        timeout: int = 45
    ) -> Dict[str, Any]:
        """
        Executes a completion against the Anthropic Messages API.
        """
        url = f"{cls.BASE_URL}/messages"
        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
        if system_prompt:
            payload["system"] = system_prompt

        # Extended Thinking / Reasoning configuration
        eff = (effort or "high").lower()
        if "sonnet" in model or "opus" in model or "3-7" in model:
            budget = thinking_budget or (4096 if eff in ("high", "max") else 2048)
            # When thinking is enabled, max_tokens must be greater than budget_tokens
            if payload["max_tokens"] <= budget:
                payload["max_tokens"] = budget + 2048
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget
            }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=cls.get_headers(), method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                # Extract text blocks
                text_content = ""
                thinking_content = ""
                for block in res_json.get("content", []):
                    if block.get("type") == "text":
                        text_content += block.get("text", "")
                    elif block.get("type") == "thinking":
                        thinking_content += block.get("thinking", "")

                return {
                    "provider": "anthropic",
                    "model": model,
                    "text": text_content.strip(),
                    "thinking": thinking_content.strip(),
                    "usage": res_json.get("usage", {}),
                    "status": "success"
                }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"Anthropic API error HTTP {e.code}: {err_body}")
            raise RuntimeError(f"Anthropic API error (HTTP {e.code}): {err_body}")
