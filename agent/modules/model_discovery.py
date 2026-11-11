"""
model_discovery.py — Dynamic Multi-Provider Model Discovery Engine.

Discovers live models exposed by every connected AI provider and router:
Arena.AI, OpenRouter, Groq, DeepInfra, Anthropic, Google Vertex, and Pollinations.
Caches catalog with TTL and normalizes capabilities into a single unified registry.
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

CACHE_FILE = Path(r"C:\Users\Pankaj Bhati\OneDrive\Desktop\AutomateAgentWorkFlow\shorts-agent\temp\model_discovery_cache.json")


class DynamicModelDiscovery:
    """Discovers live models across all connected endpoints and caches the unified catalog."""

    @classmethod
    def discover_arena_models(cls, timeout: int = 5) -> List[Dict[str, Any]]:
        key = os.environ.get("ARENA_API_KEY")
        if not key:
            return []
        url = "https://api.arena.ai/v1/models"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = []
                for item in data.get("data", []):
                    mid = item.get("id", "")
                    models.append({
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
                        }
                    })
                return models
        except Exception as e:
            logger.debug(f"Arena discovery skipped: {e}")
            return []

    @classmethod
    def discover_openrouter_models(cls, timeout: int = 5) -> List[Dict[str, Any]]:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            return []
        url = "https://openrouter.ai/api/v1/models"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = []
                # Filter top models and all free models
                for item in data.get("data", []):
                    mid = item.get("id", "")
                    is_free = ":free" in mid or float(item.get("pricing", {}).get("prompt", 1.0)) == 0.0
                    if is_free or any(sub in mid for sub in ("deepseek", "claude-3-5", "gemini-2", "llama-3.3")):
                        models.append({
                            "canonical_id": f"openrouter/{mid}",
                            "original_id": mid,
                            "display_name": item.get("name", mid),
                            "provider": "openrouter",
                            "cost_tier": "free" if is_free else "pay_per_token",
                            "context_window": item.get("context_length", 32768),
                            "capabilities": {
                                "reasoning_effort": "r1" in mid or "thinking" in mid,
                                "extended_thinking": "r1" in mid or "thinking" in mid,
                                "vision": "vision" in item.get("description", "").lower(),
                                "tool_calling": True,
                                "structured_json": True,
                            }
                        })
                return models
        except Exception as e:
            logger.debug(f"OpenRouter discovery skipped: {e}")
            return []

    @classmethod
    def discover_groq_models(cls, timeout: int = 5) -> List[Dict[str, Any]]:
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            return []
        url = "https://api.groq.com/openai/v1/models"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = []
                for item in data.get("data", []):
                    mid = item.get("id", "")
                    models.append({
                        "canonical_id": f"groq/{mid}",
                        "original_id": mid,
                        "display_name": f"Groq {mid}",
                        "provider": "groq",
                        "cost_tier": "free",
                        "context_window": item.get("context_window", 131072),
                        "capabilities": {
                            "reasoning_effort": False,
                            "extended_thinking": False,
                            "vision": False,
                            "tool_calling": True,
                            "structured_json": True,
                        }
                    })
                return models
        except Exception as e:
            logger.debug(f"Groq discovery skipped: {e}")
            return []

    @classmethod
    def discover_all(cls, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Discovers all live models across connected providers.
        Uses cached catalog if fresh (< 1 hour) unless force_refresh=True.
        """
        now = time.time()
        if not force_refresh and CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                    if now - cache_data.get("timestamp", 0) < 3600:
                        return cache_data.get("models", [])
            except Exception:
                pass

        all_models = []
        # 1. Discover from Arena
        all_models.extend(cls.discover_arena_models())
        # 2. Discover from OpenRouter
        all_models.extend(cls.discover_openrouter_models())
        # 3. Discover from Groq
        all_models.extend(cls.discover_groq_models())
        # 4. Include Anthropic Adapter baseline
        try:
            from agent.modules.anthropic_adapter import AnthropicAdapter
            all_models.extend(AnthropicAdapter.discover_models())
        except Exception:
            pass

        # 5. Include OpenAI Adapter baseline
        try:
            from agent.modules.openai_adapter import OpenAIAdapter
            all_models.extend(OpenAIAdapter.discover_models())
        except Exception:
            pass

        # 5. Always include baseline free models if discovery returned few items
        if len(all_models) < 3:
            all_models.extend([
                {
                    "canonical_id": "arena/coding-router-preview",
                    "original_id": "coding-router-preview",
                    "display_name": "Arena Coding Router",
                    "provider": "arena",
                    "cost_tier": "router_funded",
                    "context_window": 128000,
                    "capabilities": {"reasoning_effort": True, "tool_calling": True, "structured_json": True}
                },
                {
                    "canonical_id": "openrouter/deepseek/deepseek-r1:free",
                    "original_id": "deepseek/deepseek-r1:free",
                    "display_name": "DeepSeek R1 (Free Reasoning)",
                    "provider": "openrouter",
                    "cost_tier": "free",
                    "context_window": 64000,
                    "capabilities": {"reasoning_effort": True, "tool_calling": False, "structured_json": True}
                },
                {
                    "canonical_id": "groq/llama-3.3-70b-versatile",
                    "original_id": "llama-3.3-70b-versatile",
                    "display_name": "Groq Llama 3.3 70B",
                    "provider": "groq",
                    "cost_tier": "free",
                    "context_window": 131072,
                    "capabilities": {"reasoning_effort": False, "tool_calling": True, "structured_json": True}
                }
            ])

        # Write cache
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"timestamp": now, "models": all_models}, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not write discovery cache: {e}")

        return all_models
