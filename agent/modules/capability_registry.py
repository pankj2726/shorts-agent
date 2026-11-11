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
        "aliases": ["claude-3.5-sonnet", "claude-sonnet-3.5"],
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
        "pricing": {
            "input_per_million": 3.00,
            "output_per_million": 15.00,
            "currency": "USD"
        },
        "tags": ["frontier", "coding", "reasoning", "multimodal"],
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
        "aliases": ["arena-coding-router", "arena-router"],
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
        "pricing": {
            "input_per_million": 0.00,
            "output_per_million": 0.00,
            "currency": "USD"
        },
        "tags": ["router", "coding", "fast", "h100"],
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
        "aliases": ["deepseek-reasoner", "deepseek/deepseek-r1"],
        "display_name": "DeepSeek R1 (Reasoning)",
        "family": "deepseek",
        "description": "Frontier open-weight chain-of-thought reasoning ($0.00 free tier).",
        "provider_mappings": {
            "openrouter": "deepseek/deepseek-r1:free",
            "arena": "deepseek-v4p1-flash"
        },
        "default_provider": "openrouter",
        "context_window": 64000,
        "max_output_tokens": 8192,
        "cost_tier": "free",
        "pricing": {
            "input_per_million": 0.55,
            "output_per_million": 2.19,
            "currency": "USD"
        },
        "tags": ["reasoning", "open_weights", "math", "coding"],
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
        "aliases": ["gemini-2.0-flash", "google/gemini-2.0-flash"],
        "display_name": "Google Gemini 2.0 Flash",
        "family": "google",
        "description": "Ultra-fast multimodal reasoning with 1M token context ($0.00 free tier).",
        "provider_mappings": {
            "openrouter": "google/gemini-2.0-flash-exp:free",
            "vertex_ai": "gemini-2.0-flash",
            "arena": "gemini-3.7-flash"
        },
        "default_provider": "openrouter",
        "context_window": 1048576,
        "max_output_tokens": 8192,
        "cost_tier": "free",
        "pricing": {
            "input_per_million": 0.10,
            "output_per_million": 0.40,
            "currency": "USD"
        },
        "tags": ["ultra_fast", "multimodal", "extreme_context"],
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
        "aliases": ["llama-3.3-70b", "meta-llama/llama-3.3-70b"],
        "display_name": "Meta Llama 3.3 70B",
        "family": "meta",
        "description": "State-of-the-art open weights at ~500 tokens/sec via Groq LPU.",
        "provider_mappings": {
            "groq": "openai/gpt-oss-120b",
            "openrouter": "meta-llama/llama-3.3-70b-instruct:free",
            "deepinfra": "meta-llama/Meta-Llama-3.1-70B-Instruct"
        },
        "default_provider": "groq",
        "context_window": 131072,
        "max_output_tokens": 4096,
        "cost_tier": "free",
        "pricing": {
            "input_per_million": 0.59,
            "output_per_million": 0.79,
            "currency": "USD"
        },
        "tags": ["fast", "open_weights", "general"],
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
        "aliases": ["qwen-2.5-coder", "qwen/qwen-2.5-coder-32b"],
        "display_name": "Qwen 2.5 Coder 32B",
        "family": "qwen",
        "description": "Specialized coding and structured logic model ($0.00 free tier).",
        "provider_mappings": {
            "openrouter": "qwen/qwen-2.5-coder-32b-instruct:free",
            "deepinfra": "Qwen/Qwen2.5-72B-Instruct",
            "arena": "coding-router-preview"
        },
        "default_provider": "openrouter",
        "context_window": 32768,
        "max_output_tokens": 4096,
        "cost_tier": "free",
        "pricing": {
            "input_per_million": 0.20,
            "output_per_million": 0.20,
            "currency": "USD"
        },
        "tags": ["coding", "specialized", "open_weights"],
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
        "aliases": ["flux", "flux-schnell"],
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
        "pricing": {
            "input_per_million": 0.00,
            "output_per_million": 0.00,
            "currency": "USD"
        },
        "tags": ["media", "image", "stills"],
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
        "aliases": ["chatgpt-4o", "openai/gpt-4o"],
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
        "pricing": {
            "input_per_million": 2.50,
            "output_per_million": 10.00,
            "currency": "USD"
        },
        "tags": ["frontier", "multimodal", "general"],
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
        "aliases": ["openai/o3-mini"],
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
        "pricing": {
            "input_per_million": 1.10,
            "output_per_million": 4.40,
            "currency": "USD"
        },
        "tags": ["reasoning", "coding", "fast", "math"],
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
        "aliases": ["openai/o1"],
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
        "pricing": {
            "input_per_million": 15.00,
            "output_per_million": 60.00,
            "currency": "USD"
        },
        "tags": ["frontier_reasoning", "complex_math", "engineering"],
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
        "aliases": ["claude-3.7-sonnet", "claude-3-7-sonnet-20250219"],
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
        "pricing": {
            "input_per_million": 3.00,
            "output_per_million": 15.00,
            "currency": "USD"
        },
        "tags": ["frontier", "hybrid_reasoning", "coding", "thinking"],
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
    "claude-3-5-haiku": {
        "canonical_id": "claude-3-5-haiku",
        "aliases": ["claude-3.5-haiku", "claude-haiku-3.5", "claude-3-5-haiku-20241022"],
        "display_name": "Anthropic Claude 3.5 Haiku",
        "family": "anthropic",
        "description": "Ultra-fast lightweight Claude model for rapid iteration, code generation, and low-latency workflows.",
        "provider_mappings": {
            "arena": "claude-haiku-4-5-20251001",
            "openrouter": "anthropic/claude-3.5-haiku",
            "anthropic": "claude-3-5-haiku-20241022"
        },
        "default_provider": "arena",
        "context_window": 200000,
        "max_output_tokens": 8192,
        "cost_tier": "router_funded",
        "pricing": {
            "input_per_million": 0.80,
            "output_per_million": 4.00,
            "cache_read_per_million": 0.08,
            "cache_write_per_million": 1.00,
            "currency": "USD"
        },
        "tags": ["fast", "coding", "low_latency", "cost_effective", "text"],
        "capabilities": {
            "reasoning_effort": {"supported": False, "levels": []},
            "extended_thinking": False,
            "thinking_budget": False,
            "tool_calling": True,
            "structured_json": True,
            "vision": False,
            "code": True,
        }
    },
    "mistral-large-2411": {
        "canonical_id": "mistral-large-2411",
        "aliases": ["mistral-large-2", "mistral-large", "mistralai/mistral-large-2411"],
        "display_name": "Mistral Large 2 (2411)",
        "family": "mistral",
        "description": "Flagship 123B European reasoning, advanced coding, and multilingual powerhouse (release 2411).",
        "provider_mappings": {
            "deepinfra": "mistralai/Mistral-Large-Instruct-2411",
            "openrouter": "mistralai/mistral-large-2411",
            "arena": "coding-router-preview"
        },
        "default_provider": "deepinfra",
        "context_window": 128000,
        "max_output_tokens": 8192,
        "cost_tier": "router_funded",
        "pricing": {
            "input_per_million": 2.00,
            "output_per_million": 6.00,
            "currency": "USD"
        },
        "tags": ["multilingual", "coding", "reasoning", "general", "frontier"],
        "capabilities": {
            "reasoning_effort": {"supported": False, "levels": []},
            "extended_thinking": False,
            "thinking_budget": False,
            "tool_calling": True,
            "structured_json": True,
            "vision": False,
            "code": True,
        }
    },
    "deepseek-v3": {
        "canonical_id": "deepseek-v3",
        "aliases": ["deepseek-chat", "deepseek-v3-moe", "deepseek-ai/deepseek-v3"],
        "display_name": "DeepSeek V3 (671B MoE)",
        "family": "deepseek",
        "description": "Massive 671B parameter Mixture-of-Experts powerhouse with 37B active parameters and top-tier code benchmark scores.",
        "provider_mappings": {
            "arena": "deepseek-v4p1-flash",
            "openrouter": "deepseek/deepseek-chat",
            "deepinfra": "deepseek-ai/DeepSeek-V3"
        },
        "default_provider": "arena",
        "context_window": 64000,
        "max_output_tokens": 8192,
        "cost_tier": "router_funded",
        "pricing": {
            "input_per_million": 0.14,
            "output_per_million": 0.28,
            "currency": "USD"
        },
        "tags": ["moe", "ultra_cheap", "coding", "high_throughput", "open_weights"],
        "capabilities": {
            "reasoning_effort": {"supported": False, "levels": []},
            "extended_thinking": False,
            "thinking_budget": False,
            "tool_calling": True,
            "structured_json": True,
            "vision": False,
            "code": True,
        }
    },
    "llama-3.1-405b": {
        "canonical_id": "llama-3.1-405b",
        "aliases": ["llama-3-1-405b", "meta-llama-3.1-405b", "llama-405b", "meta-llama/llama-3.1-405b-instruct"],
        "display_name": "Meta Llama 3.1 405B",
        "family": "meta",
        "description": "The world's largest open-weights frontier model (405 Billion parameters) for complex reasoning, math, and code.",
        "provider_mappings": {
            "deepinfra": "meta-llama/Meta-Llama-3.1-405B-Instruct",
            "openrouter": "meta-llama/llama-3.1-405b-instruct",
            "arena": "coding-router-preview"
        },
        "default_provider": "deepinfra",
        "context_window": 131072,
        "max_output_tokens": 8192,
        "cost_tier": "router_funded",
        "pricing": {
            "input_per_million": 1.79,
            "output_per_million": 1.79,
            "currency": "USD"
        },
        "tags": ["frontier_scale", "open_weights", "complex_reasoning", "coding", "405b"],
        "capabilities": {
            "reasoning_effort": {"supported": False, "levels": []},
            "extended_thinking": False,
            "thinking_budget": False,
            "tool_calling": True,
            "structured_json": True,
            "vision": False,
            "code": True,
        }
    },
    "llama-3-1-405b": {
        "canonical_id": "llama-3-1-405b",
        "aliases": ["llama-3.1-405b", "meta-llama-3.1-405b", "llama-405b", "meta-llama/llama-3.1-405b-instruct"],
        "display_name": "Meta Llama 3.1 405B",
        "family": "meta",
        "description": "The world's largest open-weights frontier model (405 Billion parameters) for complex reasoning, math, and code.",
        "provider_mappings": {
            "deepinfra": "meta-llama/Meta-Llama-3.1-405B-Instruct",
            "openrouter": "meta-llama/llama-3.1-405b-instruct",
            "arena": "coding-router-preview"
        },
        "default_provider": "deepinfra",
        "context_window": 131072,
        "max_output_tokens": 8192,
        "cost_tier": "router_funded",
        "pricing": {
            "input_per_million": 1.79,
            "output_per_million": 1.79,
            "currency": "USD"
        },
        "tags": ["frontier_scale", "open_weights", "complex_reasoning", "coding", "405b"],
        "capabilities": {
            "reasoning_effort": {"supported": False, "levels": []},
            "extended_thinking": False,
            "thinking_budget": False,
            "tool_calling": True,
            "structured_json": True,
            "vision": False,
            "code": True,
        }
    },
    "grok-2": {
        "canonical_id": "grok-2",
        "aliases": ["grok-2-1212", "x-ai/grok-2", "grok-2-vision", "grok-2-vision-1212"],
        "display_name": "xAI Grok 2",
        "family": "xai",
        "description": "Frontier intelligence from xAI with state-of-the-art vision understanding, math, coding, and real-time world knowledge.",
        "provider_mappings": {
            "openrouter": "x-ai/grok-2-1212",
            "arena": "grok-4.7"
        },
        "default_provider": "arena",
        "context_window": 131072,
        "max_output_tokens": 8192,
        "cost_tier": "router_funded",
        "pricing": {
            "input_per_million": 2.00,
            "output_per_million": 10.00,
            "currency": "USD"
        },
        "tags": ["multimodal", "vision", "coding", "realtime", "frontier"],
        "capabilities": {
            "reasoning_effort": {"supported": False, "levels": []},
            "extended_thinking": False,
            "thinking_budget": False,
            "tool_calling": True,
            "structured_json": True,
            "vision": True,
            "code": True,
        }
    },
    "gemini-1-5-pro": {
        "canonical_id": "gemini-1-5-pro",
        "aliases": ["gemini-1.5-pro", "gemini-pro-1.5", "google/gemini-1.5-pro"],
        "display_name": "Google Gemini 1.5 Pro",
        "family": "google",
        "description": "2 Million token long-context multimodal frontier reasoning engine.",
        "provider_mappings": {
            "vertex_ai": "gemini-1.5-pro",
            "openrouter": "google/gemini-pro-1.5",
            "arena": "gemini-4-carbon"
        },
        "default_provider": "arena",
        "context_window": 2097152,
        "max_output_tokens": 8192,
        "cost_tier": "organization_funded",
        "pricing": {
            "input_per_million": 1.25,
            "output_per_million": 5.00,
            "currency": "USD"
        },
        "tags": ["extreme_context", "multimodal", "audio_video", "reasoning", "frontier"],
        "capabilities": {
            "reasoning_effort": {"supported": True, "levels": ["low", "medium", "high"]},
            "extended_thinking": True,
            "thinking_budget": True,
            "tool_calling": True,
            "structured_json": True,
            "vision": True,
            "code": True,
        }
    },
    "gemini-1.5-pro": {
        "canonical_id": "gemini-1.5-pro",
        "aliases": ["gemini-1-5-pro", "gemini-pro-1.5", "google/gemini-1.5-pro"],
        "display_name": "Google Gemini 1.5 Pro",
        "family": "google",
        "description": "2 Million token long-context multimodal frontier reasoning engine.",
        "provider_mappings": {
            "vertex_ai": "gemini-1.5-pro",
            "openrouter": "google/gemini-pro-1.5",
            "arena": "gemini-4-carbon"
        },
        "default_provider": "arena",
        "context_window": 2097152,
        "max_output_tokens": 8192,
        "cost_tier": "organization_funded",
        "pricing": {
            "input_per_million": 1.25,
            "output_per_million": 5.00,
            "currency": "USD"
        },
        "tags": ["extreme_context", "multimodal", "audio_video", "reasoning", "frontier"],
        "capabilities": {
            "reasoning_effort": {"supported": True, "levels": ["low", "medium", "high"]},
            "extended_thinking": True,
            "thinking_budget": True,
            "tool_calling": True,
            "structured_json": True,
            "vision": True,
            "code": True,
        }
    }
}


# ─── Universal Capability Registry Engine ──────────────────────────────────────

class UniversalCapabilityRegistry:
    """Manages model capabilities, parameter translation, and live availability."""

    _active_model: str = "claude-3-5-sonnet"
    _active_effort: str = "high"

    @classmethod
    def resolve_canonical_id(cls, model_id: str) -> Optional[str]:
        """Resolves model id across aliases, dot/hyphen variations, and canonical identifiers."""
        if not model_id:
            return None
        # 1. Direct exact match
        if model_id in CANONICAL_MODELS:
            return model_id
        # 2. Dot / hyphen variation (e.g., llama-3.1-405b vs llama-3-1-405b)
        hyphen_variant = model_id.replace(".", "-")
        if hyphen_variant in CANONICAL_MODELS:
            return hyphen_variant
        dot_variant = model_id.replace("-", ".")
        if dot_variant in CANONICAL_MODELS:
            return dot_variant
        # 3. Check aliases and display names
        mid_lower = model_id.lower().strip()
        for k, v in CANONICAL_MODELS.items():
            if mid_lower == k.lower():
                return k
            aliases = v.get("aliases", [])
            if any(mid_lower == a.lower() for a in aliases):
                return k
            if mid_lower == v.get("display_name", "").lower():
                return k
        return None

    @classmethod
    def get_active_model(cls) -> str:
        return cls._active_model

    @classmethod
    def set_active_model(cls, model_id: str) -> bool:
        resolved = cls.resolve_canonical_id(model_id)
        if resolved:
            cls._active_model = resolved
            return True
        # Try matching by display name or partial id
        for k, v in CANONICAL_MODELS.items():
            if model_id.lower() in k.lower() or model_id.lower() in v.get("display_name", "").lower():
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
        resolved_id = cls.resolve_canonical_id(canonical_id) or canonical_id
        spec = CANONICAL_MODELS.get(resolved_id)
        if not spec:
            return {"status": "UNSUPPORTED", "message": "Unknown canonical model"}

        provider_mappings = spec.get("provider_mappings", {})
        available_providers = []

        for prov, pmodel in provider_mappings.items():
            if prov == "pollinations":
                available_providers.append(prov)
            elif prov == "arena" and (os.environ.get("ARENA_API_KEY") or os.environ.get("ARENA_KEY")):
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
            elif prov == "anthropic" and (
                os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("CLAUDE_CONSOLE_API_KEY")
                or os.environ.get("CLAUDE_API_KEY")
            ):
                available_providers.append(prov)
            elif prov == "vertex_ai" and (os.environ.get("VERTEX_API_KEY") or os.environ.get("GEMINI_API_KEY")):
                available_providers.append(prov)
            elif prov == "omniroute" and os.environ.get("OMNIROUTE_API_KEY"):
                available_providers.append(prov)
            elif prov == "opencode" and os.environ.get("OPENCODE_API_KEY"):
                available_providers.append(prov)
            elif prov == "anyapi" and os.environ.get("ANYAPI_API_KEY"):
                available_providers.append(prov)
            elif prov == "agnes" and os.environ.get("AGNES_API_KEY"):
                available_providers.append(prov)
            elif prov == "agentrouter" and os.environ.get("AGENTROUTER_API_KEY"):
                available_providers.append(prov)

        if available_providers:
            return {
                "canonical_id": canonical_id,
                "display_name": spec["display_name"],
                "status": "AVAILABLE",
                "available_providers": available_providers,
                "primary_provider": available_providers[0],
                "cost_tier": spec["cost_tier"],
                "pricing": spec.get("pricing", {}),
                "tags": spec.get("tags", []),
                "capabilities": spec["capabilities"],
            }
        else:
            return {
                "canonical_id": canonical_id,
                "display_name": spec["display_name"],
                "status": "RESTRICTED",
                "message": f"Requires credentials for one of: {list(provider_mappings.keys())}",
                "cost_tier": spec["cost_tier"],
                "pricing": spec.get("pricing", {}),
                "tags": spec.get("tags", []),
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
    def list_unlocked_models(cls) -> List[Dict[str, Any]]:
        """Alias for get_all_models() returning unlocked models."""
        return cls.get_all_models()

    @classmethod
    def translate_parameters(
        cls,
        canonical_id: str,
        target_provider: str,
        effort: Optional[str] = None,
        thinking_budget: Optional[int] = None
    ) -> Dict[str, Any]:
        """Translates normalized parameters into provider-native payload attributes."""
        resolved_id = cls.resolve_canonical_id(canonical_id) or canonical_id
        spec = CANONICAL_MODELS.get(resolved_id, {})
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
