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
    DEFAULT_WORKSPACE_ID = "wrkspc_01Ne4eA89fWrsVS4Y9LzS9Cm"
    _cached_workspace_id: Optional[str] = None

    @classmethod
    def get_api_key(cls) -> Optional[str]:
        return (
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("CLAUDE_CONSOLE_API_KEY")
            or os.environ.get("CLAUDE_API_KEY")
            or os.environ.get("ANTHROPIC_OAT_TOKEN")
        )

    @classmethod
    def get_workspace_id(cls, timeout: int = 5) -> Optional[str]:
        """Resolves workspace ID from environment, auto-discovery, or default fallback."""
        # 1. Environment variable override
        ws = os.environ.get("ANTHROPIC_WORKSPACE_ID") or os.environ.get("CLAUDE_WORKSPACE_ID")
        if ws:
            return ws.strip()

        # 2. Return cached workspace ID if already resolved
        if cls._cached_workspace_id:
            return cls._cached_workspace_id

        # 3. Dynamic discovery probe against organization workspaces
        key = cls.get_api_key()
        if key:
            try:
                url = f"{cls.BASE_URL}/organizations/workspaces"
                headers = {
                    "x-api-key": key.strip(),
                    "anthropic-version": cls.API_VERSION,
                    "User-Agent": "ShortsForge-AnthropicAdapter/1.0",
                }
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        workspaces = data.get("data", [])
                        if workspaces and "id" in workspaces[0]:
                            discovered_id = workspaces[0]["id"]
                            cls._cached_workspace_id = discovered_id
                            os.environ["ANTHROPIC_WORKSPACE_ID"] = discovered_id
                            return discovered_id
            except Exception as e:
                logger.debug(f"Workspace auto-discovery skipped: {e}")

        # 4. Safe fallback to verified organization workspace
        cls._cached_workspace_id = cls.DEFAULT_WORKSPACE_ID
        os.environ["ANTHROPIC_WORKSPACE_ID"] = cls.DEFAULT_WORKSPACE_ID
        return cls.DEFAULT_WORKSPACE_ID

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

        ws_id = cls.get_workspace_id()
        if ws_id:
            headers["anthropic-workspace-id"] = ws_id.strip()

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
                        "id": mid,
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
                    "id": "claude-3-5-sonnet-20241022",
                    "canonical_id": "claude-3-5-sonnet-20241022",
                    "display_name": "Claude 3.5 Sonnet (Latest)",
                    "provider": "anthropic",
                    "capabilities": {"extended_thinking": True, "vision": True, "tool_calling": True}
                },
                {
                    "id": "claude-3-5-haiku-20241022",
                    "canonical_id": "claude-3-5-haiku-20241022",
                    "display_name": "Claude 3.5 Haiku",
                    "provider": "anthropic",
                    "capabilities": {"extended_thinking": False, "vision": True, "tool_calling": True}
                }
            ]

    @classmethod
    def complete(
        cls,
        prompt: Optional[str] = None,
        system_prompt: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        model: str = "claude-3-5-sonnet-20241022",
        effort: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        thinking_budget: Optional[int] = None,
        max_tokens: int = 4096,
        system: Optional[str] = None,
        timeout: int = 45
    ) -> Dict[str, Any]:
        """
        Executes a completion against the Anthropic Messages API.
        """
        url = f"{cls.BASE_URL}/messages"

        # Normalize prompt / messages
        if messages:
            formatted_messages = messages
        elif prompt:
            formatted_messages = [{"role": "user", "content": prompt}]
        else:
            formatted_messages = [{"role": "user", "content": "ping"}]

        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": formatted_messages
        }
        effective_system = system or system_prompt
        if effective_system:
            payload["system"] = effective_system

        # Extended Thinking / Reasoning configuration
        eff = (effort or reasoning_effort or "high").lower()
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
                raw_data = resp.read()
                if isinstance(raw_data, bytes):
                    res_json = json.loads(raw_data.decode("utf-8"))
                elif isinstance(raw_data, str):
                    res_json = json.loads(raw_data)
                elif isinstance(raw_data, dict):
                    res_json = raw_data
                else:
                    try:
                        res_json = json.loads(str(raw_data))
                    except Exception:
                        res_json = {"content": [{"type": "text", "text": str(raw_data)}]}
                # Extract text blocks
                text_content = ""
                thinking_content = ""
                for block in res_json.get("content", []):
                    if block.get("type") == "text":
                        text_content += block.get("text", "")
                    elif block.get("type") == "thinking":
                        thinking_content += block.get("thinking", "")

                # Extract rate-limit telemetry headers
                rate_limits = {k.lower(): v for k, v in resp.headers.items() if "ratelimit" in k.lower()}

                return {
                    "provider": "anthropic",
                    "model": model,
                    "text": text_content.strip(),
                    "thinking": thinking_content.strip(),
                    "usage": res_json.get("usage", {}),
                    "rate_limits": rate_limits,
                    "status": "success"
                }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"Anthropic API error HTTP {e.code}: {err_body}")
            raise RuntimeError(f"Anthropic API error (HTTP {e.code}): {err_body}")
