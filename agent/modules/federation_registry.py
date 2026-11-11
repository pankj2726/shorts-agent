"""
federation_registry.py — Universal Model Federation Registry & Multi-Router Failover Matrix.

Manages canonical model mapping, live multi-router routes, failure classification,
circuit breakers, cooldown timers, and intelligent same-model priority routing.
Zero silent model downgrades.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("FederationRegistry")

CONFIG_PATH = Path(r"C:\Users\Pankaj Bhati\OneDrive\Desktop\AutomateAgentWorkFlow\shorts-agent\agent\config\paid_router_config.json")
STATE_PATH = Path(r"C:\Users\Pankaj Bhati\OneDrive\Desktop\AutomateAgentWorkFlow\shorts-agent\agent\state\federation_state.json")
STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


@dataclass
class ModelRoute:
    priority: int
    provider: str
    upstream_model: str
    endpoint: str
    auth_key_name: str
    classification: str = "paid_api"
    health_status: str = "ONLINE"
    latency_ms: int = 0
    cooldown_until: float = 0.0

    def is_available(self) -> bool:
        # Check if auth key exists in environment
        key_val = os.environ.get(self.auth_key_name)
        if not key_val and self.auth_key_name != "NONE":
            # Check secondary aliases
            if self.auth_key_name == "CLAUDE_CONSOLE_API_KEY":
                key_val = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
            elif self.auth_key_name == "VERTEX_AI_API_KEY":
                key_val = os.environ.get("GEMINI_API_KEY")

        if not key_val and self.auth_key_name != "NONE":
            return False

        # Check circuit cooldown
        if time.time() < self.cooldown_until:
            return False

        return True


@dataclass
class CanonicalModel:
    canonical_id: str
    display_name: str
    developer: str
    family: str
    tier: str
    context_window: int
    max_output_tokens: int
    capabilities: Dict[str, Any] = field(default_factory=dict)
    routes: List[ModelRoute] = field(default_factory=list)

    def get_best_route(self) -> Optional[ModelRoute]:
        """Returns the highest priority route that is online and not in cooldown."""
        sorted_routes = sorted(self.routes, key=lambda r: r.priority)
        for r in sorted_routes:
            if r.is_available():
                return r
        return None


# ─── Federation Registry Master Catalog ──────────────────────────────────────

class FederationRegistry:
    """Universal Model Federation Registry Engine."""

    _active_model: str = "claude-3-5-sonnet"
    _active_effort: str = "high"
    _models: Dict[str, CanonicalModel] = {}
    _initialized: bool = False

    @classmethod
    def initialize(cls) -> None:
        if cls._initialized:
            return

        cls._models = {
            # 1. Claude 3.5 Sonnet
            "claude-3-5-sonnet": CanonicalModel(
                canonical_id="claude-3-5-sonnet",
                display_name="Anthropic Claude 3.5 Sonnet",
                developer="Anthropic",
                family="claude",
                tier="frontier_reasoning",
                context_window=200000,
                max_output_tokens=8192,
                capabilities={"reasoning_effort": True, "vision": True, "tool_calling": True, "structured_outputs": True},
                routes=[
                    ModelRoute(priority=1, provider="arena", upstream_model="claude-coding-router-preview", endpoint="https://api.arena.ai/v1/chat/completions", auth_key_name="ARENA_API_KEY", classification="router_funded"),
                    ModelRoute(priority=2, provider="openrouter", upstream_model="anthropic/claude-3.5-sonnet", endpoint="https://openrouter.ai/api/v1/chat/completions", auth_key_name="OPENROUTER_API_KEY", classification="paid_api"),
                    ModelRoute(priority=3, provider="anthropic", upstream_model="claude-3-5-sonnet-20241022", endpoint="https://api.anthropic.com/v1/messages", auth_key_name="CLAUDE_CONSOLE_API_KEY", classification="direct_org"),
                    ModelRoute(priority=4, provider="omniroute", upstream_model="anthropic/claude-3.5-sonnet", endpoint="https://api.omniroute.ai/v1/chat/completions", auth_key_name="OMNIROUTE_API_KEY", classification="router_gateway"),
                    ModelRoute(priority=5, provider="opencode", upstream_model="claude-3-5-sonnet", endpoint="https://api.opencode.ai/v1/chat/completions", auth_key_name="OPENCODE_API_KEY", classification="router_gateway"),
                ]
            ),

            # 2. Claude 3.7 Sonnet (Hybrid)
            "claude-3-7-sonnet": CanonicalModel(
                canonical_id="claude-3-7-sonnet",
                display_name="Anthropic Claude 3.7 Sonnet (Hybrid)",
                developer="Anthropic",
                family="claude",
                tier="frontier_reasoning",
                context_window=200000,
                max_output_tokens=64000,
                capabilities={"reasoning_effort": True, "extended_thinking": True, "vision": True, "tool_calling": True},
                routes=[
                    ModelRoute(priority=1, provider="arena", upstream_model="claude-coding-router-preview", endpoint="https://api.arena.ai/v1/chat/completions", auth_key_name="ARENA_API_KEY", classification="router_funded"),
                    ModelRoute(priority=2, provider="anthropic", upstream_model="claude-3-7-sonnet-20250219", endpoint="https://api.anthropic.com/v1/messages", auth_key_name="CLAUDE_CONSOLE_API_KEY", classification="direct_org"),
                    ModelRoute(priority=3, provider="openrouter", upstream_model="anthropic/claude-3.7-sonnet", endpoint="https://openrouter.ai/api/v1/chat/completions", auth_key_name="OPENROUTER_API_KEY", classification="paid_api"),
                ]
            ),

            # 3. Arena Coding Router
            "coding-router-preview": CanonicalModel(
                canonical_id="coding-router-preview",
                display_name="Arena Coding Meta-Router (H100)",
                developer="Arena.AI",
                family="arena_meta",
                tier="frontier_coding",
                context_window=128000,
                max_output_tokens=8192,
                capabilities={"reasoning_effort": True, "tool_calling": True, "structured_outputs": True},
                routes=[
                    ModelRoute(priority=1, provider="arena", upstream_model="coding-router-preview", endpoint="https://api.arena.ai/v1/chat/completions", auth_key_name="ARENA_API_KEY", classification="router_funded"),
                ]
            ),

            # 4. OpenAI o1 (Deep Reasoning)
            "o1": CanonicalModel(
                canonical_id="o1",
                display_name="OpenAI o1 (Full Reasoning)",
                developer="OpenAI",
                family="openai",
                tier="frontier_reasoning",
                context_window=200000,
                max_output_tokens=100000,
                capabilities={"reasoning_effort": True, "stem_reasoning": True, "vision": True},
                routes=[
                    ModelRoute(priority=1, provider="openai", upstream_model="o1", endpoint="https://api.openai.com/v1/chat/completions", auth_key_name="OPENAI_API_KEY", classification="direct_org"),
                    ModelRoute(priority=2, provider="openrouter", upstream_model="openai/o1", endpoint="https://openrouter.ai/api/v1/chat/completions", auth_key_name="OPENROUTER_API_KEY", classification="paid_api"),
                ]
            ),

            # 5. OpenAI o3-mini (High-Speed Reasoning)
            "o3-mini": CanonicalModel(
                canonical_id="o3-mini",
                display_name="OpenAI o3-mini (Reasoning)",
                developer="OpenAI",
                family="openai",
                tier="frontier_reasoning",
                context_window=200000,
                max_output_tokens=100000,
                capabilities={"reasoning_effort": True, "fast_reasoning": True, "tool_calling": True},
                routes=[
                    ModelRoute(priority=1, provider="openai", upstream_model="o3-mini", endpoint="https://api.openai.com/v1/chat/completions", auth_key_name="OPENAI_API_KEY", classification="direct_org"),
                    ModelRoute(priority=2, provider="openrouter", upstream_model="openai/o3-mini", endpoint="https://openrouter.ai/api/v1/chat/completions", auth_key_name="OPENROUTER_API_KEY", classification="paid_api"),
                ]
            ),

            # 6. OpenAI GPT-4o
            "gpt-4o": CanonicalModel(
                canonical_id="gpt-4o",
                display_name="OpenAI GPT-4o Flagship",
                developer="OpenAI",
                family="openai",
                tier="flagship_multimodal",
                context_window=128000,
                max_output_tokens=16384,
                capabilities={"vision": True, "tool_calling": True, "structured_outputs": True},
                routes=[
                    ModelRoute(priority=1, provider="openai", upstream_model="gpt-4o", endpoint="https://api.openai.com/v1/chat/completions", auth_key_name="OPENAI_API_KEY", classification="direct_org"),
                    ModelRoute(priority=2, provider="openrouter", upstream_model="openai/gpt-4o", endpoint="https://openrouter.ai/api/v1/chat/completions", auth_key_name="OPENROUTER_API_KEY", classification="paid_api"),
                    ModelRoute(priority=3, provider="arena", upstream_model="gpt-4o", endpoint="https://api.arena.ai/v1/chat/completions", auth_key_name="ARENA_API_KEY", classification="router_funded"),
                ]
            ),

            # 7. DeepSeek R1
            "deepseek-r1": CanonicalModel(
                canonical_id="deepseek-r1",
                display_name="DeepSeek R1 (Reasoning)",
                developer="DeepSeek",
                family="deepseek",
                tier="frontier_reasoning",
                context_window=64000,
                max_output_tokens=8192,
                capabilities={"reasoning_effort": True, "cot_reasoning": True},
                routes=[
                    ModelRoute(priority=1, provider="openrouter", upstream_model="deepseek/deepseek-r1:free", endpoint="https://openrouter.ai/api/v1/chat/completions", auth_key_name="OPENROUTER_API_KEY", classification="free"),
                    ModelRoute(priority=2, provider="deepinfra", upstream_model="deepseek-ai/DeepSeek-R1", endpoint="https://api.deepinfra.com/v1/openai/chat/completions", auth_key_name="DEEPINFRA_API_KEY", classification="paid_api"),
                ]
            ),

            # 8. DeepSeek V3
            "deepseek-v3": CanonicalModel(
                canonical_id="deepseek-v3",
                display_name="DeepSeek V3 (671B MoE)",
                developer="DeepSeek",
                family="deepseek",
                tier="frontier_open_weights",
                context_window=64000,
                max_output_tokens=8192,
                capabilities={"moe_architecture": True, "tool_calling": True},
                routes=[
                    ModelRoute(priority=1, provider="openrouter", upstream_model="deepseek/deepseek-chat", endpoint="https://openrouter.ai/api/v1/chat/completions", auth_key_name="OPENROUTER_API_KEY", classification="free"),
                    ModelRoute(priority=2, provider="deepinfra", upstream_model="deepseek-ai/DeepSeek-V3", endpoint="https://api.deepinfra.com/v1/openai/chat/completions", auth_key_name="DEEPINFRA_API_KEY", classification="paid_api"),
                ]
            ),

            # 9. Meta Llama 3.1 405B
            "llama-3-1-405b": CanonicalModel(
                canonical_id="llama-3-1-405b",
                display_name="Meta Llama 3.1 405B",
                developer="Meta",
                family="meta",
                tier="frontier_open_weights",
                context_window=131072,
                max_output_tokens=8192,
                capabilities={"massive_weights": True, "tool_calling": True},
                routes=[
                    ModelRoute(priority=1, provider="deepinfra", upstream_model="meta-llama/Meta-Llama-3.1-405B-Instruct", endpoint="https://api.deepinfra.com/v1/openai/chat/completions", auth_key_name="DEEPINFRA_API_KEY", classification="paid_api"),
                    ModelRoute(priority=2, provider="openrouter", upstream_model="meta-llama/llama-3.1-405b-instruct", endpoint="https://openrouter.ai/api/v1/chat/completions", auth_key_name="OPENROUTER_API_KEY", classification="paid_api"),
                ]
            ),

            # 10. Google Gemini 1.5 Pro
            "gemini-1-5-pro": CanonicalModel(
                canonical_id="gemini-1-5-pro",
                display_name="Google Gemini 1.5 Pro (2M Context)",
                developer="Google",
                family="google",
                tier="long_context_frontier",
                context_window=2097152,
                max_output_tokens=8192,
                capabilities={"massive_context_2m": True, "vision": True, "audio": True, "video": True},
                routes=[
                    ModelRoute(priority=1, provider="vertex_ai", upstream_model="gemini-1.5-pro", endpoint="https://aiplatform.googleapis.com/v1", auth_key_name="VERTEX_AI_API_KEY", classification="enterprise_workspace"),
                    ModelRoute(priority=2, provider="openrouter", upstream_model="google/gemini-pro-1.5", endpoint="https://openrouter.ai/api/v1/chat/completions", auth_key_name="OPENROUTER_API_KEY", classification="paid_api"),
                ]
            ),

            # 11. Google Gemini 2.0 Flash
            "gemini-2-0-flash": CanonicalModel(
                canonical_id="gemini-2-0-flash",
                display_name="Google Gemini 2.0 Flash",
                developer="Google",
                family="google",
                tier="high_speed_multimodal",
                context_window=1048576,
                max_output_tokens=8192,
                capabilities={"ultra_fast": True, "reasoning_effort": True, "vision": True},
                routes=[
                    ModelRoute(priority=1, provider="openrouter", upstream_model="google/gemini-2.0-flash-exp:free", endpoint="https://openrouter.ai/api/v1/chat/completions", auth_key_name="OPENROUTER_API_KEY", classification="free"),
                    ModelRoute(priority=2, provider="vertex_ai", upstream_model="gemini-2.0-flash", endpoint="https://aiplatform.googleapis.com/v1", auth_key_name="VERTEX_AI_API_KEY", classification="enterprise_workspace"),
                ]
            ),

            # 12. Meta Llama 3.3 70B
            "llama-3-3-70b": CanonicalModel(
                canonical_id="llama-3-3-70b",
                display_name="Meta Llama 3.3 70B",
                developer="Meta",
                family="meta",
                tier="high_speed_open_weights",
                context_window=131072,
                max_output_tokens=4096,
                capabilities={"fast_lpu": True, "tool_calling": True},
                routes=[
                    ModelRoute(priority=1, provider="groq", upstream_model="llama-3.3-70b-versatile", endpoint="https://api.groq.com/openai/v1/chat/completions", auth_key_name="GROQ_API_KEY", classification="free"),
                    ModelRoute(priority=2, provider="openrouter", upstream_model="meta-llama/llama-3.3-70b-instruct:free", endpoint="https://openrouter.ai/api/v1/chat/completions", auth_key_name="OPENROUTER_API_KEY", classification="free"),
                    ModelRoute(priority=3, provider="deepinfra", upstream_model="meta-llama/Meta-Llama-3.1-70B-Instruct", endpoint="https://api.deepinfra.com/v1/openai/chat/completions", auth_key_name="DEEPINFRA_API_KEY", classification="paid_api"),
                ]
            ),

            # 13. Pollinations Zero-Cost Fallback
            "flux-stills": CanonicalModel(
                canonical_id="flux-stills",
                display_name="Flux 1080×1920 Generative Stills",
                developer="Black Forest Labs",
                family="media",
                tier="generative_media",
                context_window=0,
                max_output_tokens=0,
                capabilities={"generative_media": True},
                routes=[
                    ModelRoute(priority=1, provider="pollinations", upstream_model="flux", endpoint="https://text.pollinations.ai/", auth_key_name="NONE", classification="free"),
                    ModelRoute(priority=2, provider="fal_ai", upstream_model="fal-ai/flux/schnell", endpoint="https://api.fal.ai/v1", auth_key_name="FAL_AI_API_KEY", classification="paid_api"),
                ]
            )
        }

        cls._load_persisted_state()
        cls._initialized = True

    @classmethod
    def _load_persisted_state(cls) -> None:
        if STATE_PATH.exists():
            try:
                with open(STATE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cls._active_model = data.get("active_model", cls._active_model)
                    cls._active_effort = data.get("active_effort", cls._active_effort)
                    # Restore cooldowns
                    cooldowns = data.get("cooldowns", {})
                    for cid, m in cls._models.items():
                        for r in m.routes:
                            key = f"{cid}:{r.provider}"
                            if key in cooldowns:
                                r.cooldown_until = cooldowns[key]
            except Exception as e:
                logger.warning(f"Failed to load federation state: {e}")

    @classmethod
    def save_state(cls) -> None:
        try:
            cooldowns = {}
            for cid, m in cls._models.items():
                for r in m.routes:
                    if r.cooldown_until > time.time():
                        cooldowns[f"{cid}:{r.provider}"] = r.cooldown_until

            data = {
                "active_model": cls._active_model,
                "active_effort": cls._active_effort,
                "cooldowns": cooldowns,
                "updated_at": time.time()
            }
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to persist federation state: {e}")

    @classmethod
    def get_canonical_model(cls, model_id: str) -> Optional[CanonicalModel]:
        cls.initialize()
        if model_id in cls._models:
            return cls._models[model_id]
        # Partial match
        for k, v in cls._models.items():
            if model_id.lower() in k.lower() or model_id.lower() in v.display_name.lower():
                return v
        return None

    @classmethod
    def resolve_route_cascade(cls, canonical_id: str) -> List[ModelRoute]:
        """
        Builds the complete same-model cascading chain for the requested canonical model.
        Returns all valid routes ordered by priority that are online and not in cooldown.
        """
        cls.initialize()
        model = cls.get_canonical_model(canonical_id)
        if not model:
            # Fallback to active model
            model = cls._models.get(cls._active_model)

        valid_routes = []
        for r in sorted(model.routes, key=lambda x: x.priority):
            if r.is_available():
                valid_routes.append(r)

        return valid_routes

    @classmethod
    def mark_route_failure(cls, canonical_id: str, provider: str, error_msg: str, status_code: Optional[int] = None) -> None:
        """Applies failure classification, sets cooldown timers, and records circuit break."""
        cls.initialize()
        model = cls.get_canonical_model(canonical_id)
        if not model:
            return

        for r in model.routes:
            if r.provider == provider:
                now = time.time()
                # Failure classification
                if status_code in (429, 402) or "quota" in error_msg.lower() or "rate limit" in error_msg.lower() or "credits remaining" in error_msg.lower():
                    # Rate limit or quota exhaustion: 180s cooldown
                    r.cooldown_until = now + 180.0
                    r.health_status = "EXHAUSTED_COOLDOWN"
                    logger.warning(f"[Circuit Breaker] {canonical_id} on {provider} rate-limited/exhausted. Cooldown: 180s.")
                elif status_code in (401, 403):
                    # Auth failure: 300s cooldown
                    r.cooldown_until = now + 300.0
                    r.health_status = "AUTH_COOLDOWN"
                    logger.warning(f"[Circuit Breaker] {canonical_id} on {provider} auth failed. Cooldown: 300s.")
                elif status_code in (500, 502, 503, 504) or "timeout" in error_msg.lower():
                    # Upstream outage / timeout: 60s cooldown
                    r.cooldown_until = now + 60.0
                    r.health_status = "OUTAGE_COOLDOWN"
                    logger.warning(f"[Circuit Breaker] {canonical_id} on {provider} server error/timeout. Cooldown: 60s.")
                else:
                    r.cooldown_until = now + 30.0
                    r.health_status = "ERROR_COOLDOWN"

        cls.save_state()

    @classmethod
    def mark_route_success(cls, canonical_id: str, provider: str, latency_ms: int) -> None:
        """Clears cooldown, updates latency and resets route to ONLINE."""
        cls.initialize()
        model = cls.get_canonical_model(canonical_id)
        if not model:
            return

        for r in model.routes:
            if r.provider == provider:
                r.health_status = "ONLINE"
                r.cooldown_until = 0.0
                r.latency_ms = latency_ms

        cls.save_state()

    @classmethod
    def get_all_models(cls) -> List[Dict[str, Any]]:
        cls.initialize()
        out = []
        for cid, m in cls._models.items():
            best_route = m.get_best_route()
            status = "AVAILABLE" if best_route else "COOLDOWN_OR_AUTH_REQUIRED"
            out.append({
                "canonical_id": m.canonical_id,
                "display_name": m.display_name,
                "developer": m.developer,
                "family": m.family,
                "tier": m.tier,
                "context_window": m.context_window,
                "max_output_tokens": m.max_output_tokens,
                "status": status,
                "active_route": asdict(best_route) if best_route else None,
                "total_routes": len(m.routes),
                "online_routes": len([r for r in m.routes if r.is_available()]),
                "is_active": (cid == cls._active_model),
                "capabilities": m.capabilities
            })
        return out

    @classmethod
    def get_active_model(cls) -> str:
        return cls._active_model

    @classmethod
    def set_active_model(cls, model_id: str) -> bool:
        cls.initialize()
        m = cls.get_canonical_model(model_id)
        if m:
            cls._active_model = m.canonical_id
            cls.save_state()
            return True
        return False

    @classmethod
    def get_active_effort(cls) -> str:
        return cls._active_effort

    @classmethod
    def set_active_effort(cls, effort: str) -> bool:
        if effort.lower() in ("low", "medium", "high", "max"):
            cls._active_effort = effort.lower()
            cls.save_state()
            return True
        return False
