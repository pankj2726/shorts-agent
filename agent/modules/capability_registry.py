"""
capability_registry.py — Universal Model & Capability Registry for Shorts Forge.

Decouples model availability from consumer app subscription restrictions.
Exposes every model and parameter legitimately supplied by authorized providers:
Arena.AI, OpenRouter, Groq, Google Vertex AI, DeepInfra, fal.ai, and Pollinations.
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

# ─── Universal Canonical Model Catalog ─────────────────────────────────────────

CANONICAL_MODELS: Dict[str, Dict[str, Any]] = {
    "claude-3-5-sonnet": {
        "canonical_id": "claude-3-5-sonnet",
        "display_name": "Claude 3.5 Sonnet",
        "family": "anthropic",
        "description": "Frontier intelligence, code synthesis, and deep reasoning.",
        "provider_mappings": {
            "arena": "claude-coding-router-preview",
            "openrouter": "anthropic/claude-3.5-sonnet",
            "anthropic": "claude-3-5-sonnet-20241022"
        },
        "default_provider": "arena",
        "context_window": 200000,
        "max_output_tokens": 8192,
        "cost_tier": "router_funded",
        "capabilities": {
            "reasoning_effort": {
                "supported": True,
                "levels": ["low", "medium", "high", "max"]
            },
            "extended_thinking": True,
            "thinking_budget": True,
            "tool_calling": True,
            "structured_json": True,
            "vision": True,
        }
    },
    "coding-router-preview": {
        "canonical_id": "coding-router-preview",
        "display_name": "Arena Coding Router",
        "family": "arena_meta",
        "description": "Intelligent dynamic meta-router selecting optimal coding models.",
        "provider_mappings": {
            "arena": "coding-router-preview"
        },
        "default_provider": "arena",
        "context_window": 128000,
        "max_output_tokens": 8192,
        "cost_tier": "router_funded",
        "capabilities": {
            "reasoning_effort": {
                "supported": True,
                "levels": ["low", "medium", "high"]
            },
            "extended_thinking": True,
            "thinking_budget": False,
            "tool_calling": True,
            "structured_json": True,
            "vision": True,
        }
    },
    "deepseek-r1": {
        "canonical_id": "deepseek-r1",
        "display_name": "DeepSeek R1 (Reasoning)",
        "family": "deepseek",
        "description": "Frontier open-weight chain-of-thought reasoning ($0.00 free tier).",
        "provider_mappings": {
            "openrouter": "deepseek/deepseek-r1:free"
        },
        "default_provider": "openrouter",
        "context_window": 64000,
        "max_output_tokens": 8192,
        "cost_tier": "free",
        "capabilities": {
            "reasoning_effort": {
                "supported": True,
                "levels": ["auto"]
            },
            "extended_thinking": True,
            "thinking_budget": False,
            "tool_calling": False,
            "structured_json": True,
            "vision": False,
        }
    },
    "gemini-2-0-flash": {
        "canonical_id": "gemini-2-0-flash",
        "display_name": "Google Gemini 2.0 Flash",
        "family": "google",
        "description": "Ultra-fast multimodal reasoning with 1M token context ($0.00 free tier).",
        "provider_mappings": {
            "openrouter": "google/gemini-2.0-flash-exp:free",
            "vertex_ai": "gemini-2.0-flash"
        },
        "default_provider": "openrouter",
        "context_window": 1048576,
        "max_output_tokens": 8192,
        "cost_tier": "free",
        "capabilities": {
            "reasoning_effort": {
                "supported": True,
                "levels": ["low", "medium", "high"]
            },
            "extended_thinking": True,
            "thinking_budget": True,
            "tool_calling": True,
            "structured_json": True,
            "vision": True,
        }
    },
    "llama-3-3-70b": {
        "canonical_id": "llama-3-3-70b",
        "display_name": "Meta Llama 3.3 70B",
        "family": "meta",
        "description": "State-of-the-art open weights at ~500 tokens/sec via Groq LPU.",
        "provider_mappings": {
            "groq": "llama-3.3-70b-versatile",
            "openrouter": "meta-llama/llama-3.3-70b-instruct:free",
            "deepinfra": "meta-llama/Meta-Llama-3.1-70B-Instruct"
        },
        "default_provider": "groq",
        "context_window": 131072,
        "max_output_tokens": 4096,
        "cost_tier": "free",
        "capabilities": {
            "reasoning_effort": {
                "supported": False,
                "levels": []
            },
            "extended_thinking": False,
            "thinking_budget": False,
            "tool_calling": True,
            "structured_json": True,
            "vision": False,
        }
    },
    "qwen-2-5-coder": {
        "canonical_id": "qwen-2-5-coder",
        "display_name": "Qwen 2.5 Coder 32B",
        "family": "qwen",
        "description": "Specialized coding and structured logic model ($0.00 free tier).",
        "provider_mappings": {
            "openrouter": "qwen/qwen-2.5-coder-32b-instruct:free",
            "deepinfra": "Qwen/Qwen2.5-72B-Instruct"
        },
        "default_provider": "openrouter",
        "context_window": 32768,
        "max_output_tokens": 4096,
        "cost_tier": "free",
        "capabilities": {
            "reasoning_effort": {
                "supported": False,
                "levels": []
            },
            "extended_thinking": False,
            "thinking_budget": False,
            "tool_calling": True,
            "structured_json": True,
            "vision": False,
        }
    },
    "flux-stills": {
        "canonical_id": "flux-stills",
        "display_name": "Flux 1080×1920 Generative Stills",
        "family": "black_forest_labs",
        "description": "Cinematic vertical 9:16 scene synthesis with FFmpeg camera motion.",
        "provider_mappings": {
            "pollinations": "flux",
            "fal_ai": "fal-ai/flux/schnell"
        },
        "default_provider": "pollinations",
        "context_window": 0,
        "max_output_tokens": 0,
        "cost_tier": "free",
        "capabilities": {
            "reasoning_effort": {
                "supported": False,
                "levels": []
            },
            "extended_thinking": False,
            "thinking_budget": False,
            "tool_calling": False,
            "structured_json": False,
            "vision": False,
            "generative_media": True,
        }
    },
    "gpt-4o": {
        "canonical_id": "gpt-4o",
        "display_name": "OpenAI GPT-4o",
        "family": "openai",
        "description": "Flagship high-intelligence multimodal model for text, vision, and coding.",
        "provider_mappings": {
            "openai": "gpt-4o",
            "openrouter": "openai/gpt-4o",
            "arena": "gpt-4o"
        },
        "default_provider": "openai",
        "context_window": 128000,
        "max_output_tokens": 16384,
        "cost_tier": "organization_funded",
        "capabilities": {
            "reasoning_effort": {
                "supported": False,
                "levels": []
            },
            "extended_thinking": False,
            "thinking_budget": False,
            "tool_calling": True,
            "structured_json": True,
            "vision": True,
        }
    },
    "o3-mini": {
        "canonical_id": "o3-mini",
        "display_name": "OpenAI o3-mini (Reasoning)",
        "family": "openai",
        "description": "Next-gen fast reasoning model with native low/medium/high effort control.",
        "provider_mappings": {
            "openai": "o3-mini",
            "openrouter": "openai/o3-mini"
        },
        "default_provider": "openai",
        "context_window": 200000,
        "max_output_tokens": 100000,
        "cost_tier": "organization_funded",
        "capabilities": {
            "reasoning_effort": {
                "supported": True,
                "levels": ["low", "medium", "high"]
            },
            "extended_thinking": True,
            "thinking_budget": False,
            "tool_calling": True,
            "structured_json": True,
            "vision": False,
        }
    },
    "o1": {
        "canonical_id": "o1",
        "display_name": "OpenAI o1 (Full Reasoning)",
        "family": "openai",
        "description": "Frontier high-effort reasoning model for complex engineering and math.",
        "provider_mappings": {
            "openai": "o1",
            "openrouter": "openai/o1"
        },
        "default_provider": "openai",
        "context_window": 200000,
        "max_output_tokens": 100000,
        "cost_tier": "organization_funded",
        "capabilities": {
            "reasoning_effort": {
                "supported": True,
                "levels": ["low", "medium", "high"]
            },
            "extended_thinking": True,
            "thinking_budget": False,
            "tool_calling": True,
            "structured_json": True,
            "vision": True,
        }
    },
    "claude-3-7-sonnet": {
        "canonical_id": "claude-3-7-sonnet",
        "display_name": "Anthropic Claude 3.7 Sonnet (Hybrid)",
        "family": "anthropic",
        "description": "Frontier hybrid reasoning Claude model with variable thinking budget.",
        "provider_mappings": {
            "anthropic": "claude-3-7-sonnet-20250219",
            "arena": "claude-coding-router-preview",
            "openrouter": "anthropic/claude-3.7-sonnet"
        },
        "default_provider": "arena",
        "context_window": 200000,
        "max_output_tokens": 64000,
        "cost_tier": "router_funded",
        "capabilities": {
            "reasoning_effort": {
                "supported": True,
                "levels": ["low", "medium", "high", "max"]
            },
            "extended_thinking": True,
            "thinking_budget": True,
            "tool_calling": True,
            "structured_json": True,
            "vision": True,
        }
    }
}


# ─── Universal Capability Registry Engine ──────────────────────────────────────

class UniversalCapabilityRegistry:
    """Manages model capabilities, parameter translation, and live availability."""

    _active_model: str = "claude-3-5-sonnet"
    _active_effort: str = "high"

    @classmethod
    def get_active_model(cls) -> str:
        return cls._active_model

    @classmethod
    def set_active_model(cls, model_id: str) -> bool:
        if model_id in CANONICAL_MODELS:
            cls._active_model = model_id
            return True
        # Try matching by display name or partial id
        for k, v in CANONICAL_MODELS.items():
            if model_id.lower() in k.lower() or model_id.lower() in v["display_name"].lower():
                cls._active_model = k
                return True
        return False

    @classmethod
    def get_active_effort(cls) -> str:
        return cls._active_effort

    @classmethod
    def set_active_effort(cls, effort: str) -> bool:
        val = effort.lower().strip()
        if val in ("low", "medium", "high", "max", "auto"):
            cls._active_effort = val
            return True
        return False

    @classmethod
    def get_model_status(cls, canonical_id: str) -> Dict[str, Any]:
        """Resolves whether a model is AVAILABLE, RESTRICTED, or UNSUPPORTED."""
        spec = CANONICAL_MODELS.get(canonical_id)
        if not spec:
            return {"status": "UNSUPPORTED", "message": "Unknown canonical model"}

        provider_mappings = spec.get("provider_mappings", {})
        available_providers = []

        for prov, pmodel in provider_mappings.items():
            if prov == "pollinations":
                available_providers.append(prov)
            elif prov == "arena" and os.environ.get("ARENA_API_KEY"):
                available_providers.append(prov)
            elif prov == "openrouter" and os.environ.get("OPENROUTER_API_KEY"):
                available_providers.append(prov)
            elif prov == "groq" and os.environ.get("GROQ_API_KEY"):
                available_providers.append(prov)
            elif prov == "deepinfra" and os.environ.get("DEEPINFRA_API_KEY"):
                available_providers.append(prov)
            elif prov == "fal_ai" and (os.environ.get("FAL_KEY") or os.environ.get("FAL_AI_API_KEY")):
                available_providers.append(prov)
            elif prov == "openai" and (os.environ.get("OPENAI_API_KEY") or os.environ.get("CHATGPT_API_KEY")):
                available_providers.append(prov)
            elif prov == "vertex_ai" and (os.environ.get("VERTEX_API_KEY") or os.environ.get("GEMINI_API_KEY")):
                available_providers.append(prov)

        if available_providers:
            return {
                "canonical_id": canonical_id,
                "display_name": spec["display_name"],
                "status": "AVAILABLE",
                "available_providers": available_providers,
                "primary_provider": available_providers[0],
                "cost_tier": spec["cost_tier"],
                "capabilities": spec["capabilities"],
            }
        else:
            return {
                "canonical_id": canonical_id,
                "display_name": spec["display_name"],
                "status": "RESTRICTED",
                "message": f"Requires credentials for one of: {list(provider_mappings.keys())}",
                "cost_tier": spec["cost_tier"],
                "capabilities": spec["capabilities"],
            }

    @classmethod
    def get_all_models(cls) -> List[Dict[str, Any]]:
        """Returns catalog with live execution status."""
        out = []
        for cid in CANONICAL_MODELS:
            info = cls.get_model_status(cid)
            info["is_active"] = (cid == cls._active_model)
            out.append(info)
        return out

    @classmethod
    def translate_parameters(
        cls,
        canonical_id: str,
        target_provider: str,
        effort: Optional[str] = None,
        thinking_budget: Optional[int] = None
    ) -> Dict[str, Any]:
        """Translates normalized parameters into provider-native payload attributes."""
        spec = CANONICAL_MODELS.get(canonical_id, {})
        caps = spec.get("capabilities", {})
        eff = effort or cls._active_effort
        params: Dict[str, Any] = {}

        # Resolve provider-specific model name
        provider_model = spec.get("provider_mappings", {}).get(target_provider)
        if not provider_model:
            provider_model = canonical_id
        params["model"] = provider_model

        # Handle Reasoning Effort
        effort_support = caps.get("reasoning_effort", {}).get("supported", False)
        if effort_support:
            if target_provider == "arena":
                # Arena supports reasoning_effort string
                params["reasoning_effort"] = eff if eff != "auto" else "high"
            elif target_provider == "openai":
                # OpenAI o1/o3-mini supports reasoning_effort string (low, medium, high)
                params["reasoning_effort"] = eff if eff in ("low", "medium", "high") else "medium"
            elif target_provider == "anthropic":
                # Native Anthropic uses thinking budget tokens
                budget = thinking_budget or (4096 if eff in ("high", "max") else 2048)
                params["thinking"] = {"type": "enabled", "budget_tokens": budget}
            elif target_provider == "openrouter":
                if "gemini" in provider_model:
                    params["thinking"] = {"type": "enabled", "budget_tokens": 4096}
                # DeepSeek R1 handles CoT automatically; omit reasoning_effort string

        return params
