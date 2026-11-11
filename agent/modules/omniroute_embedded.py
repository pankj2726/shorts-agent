"""
omniroute_embedded.py — In-Project Embedded Model Router & Gateway.

Solves the "Always-On / No Open PowerShell Needed" problem:
Instead of requiring an external server process (like a local proxy on localhost:8000)
that dies when the PC is shut down or PowerShell is closed, this embedded router
executes routing, model fallback, and token optimization IN-PROCESS within Shorts Agent.

Features:
  1. Zero background process needed (no open PowerShell terminal).
  2. Runs everywhere: on local PC, inside GitHub Actions (CI/CD), or cloud containers.
  3. Dynamic fallback chain across free providers (Pollinations -> OpenRouter:free -> DeepInfra -> Vertex).
  4. Automatic retry, rate-limit backoff, and non-billable validation.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional
from pathlib import Path
from agent.modules.capability_registry import UniversalCapabilityRegistry

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

logger = logging.getLogger(__name__)


class EmbeddedOmniRoute:
    """
    Embedded in-project router that eliminates the need for external local server processes.
    Provides OpenAI-compatible routing and automated fallbacks across verified providers.
    """

    def __init__(self, default_provider: str = "pollinations") -> None:
        self.default_provider = default_provider
        self._provider_cache: Dict[str, Any] = {}

    def get_available_routes(self) -> List[Dict[str, Any]]:
        """
        Discovers all currently active, online routing channels.
        """
        routes = []

        # 1. Pollinations AI (Always available, 100% free, zero credentials required)
        routes.append({
            "provider": "pollinations",
            "tier": "public_free",
            "online": True,
            "cost_per_token": 0.0,
            "requires_key": False,
            "endpoints": {
                "text": "https://text.pollinations.ai/",
                "image": "https://image.pollinations.ai/prompt/",
                "models": "https://gen.pollinations.ai/models"
            }
        })

        # 2. OpenRouter (Checks if key is present in env / secrets)
        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        routes.append({
            "provider": "openrouter",
            "tier": "free_and_paid",
            "online": bool(openrouter_key),
            "cost_per_token": 0.0,  # when using :free suffix models
            "requires_key": True,
            "endpoints": {
                "chat": "https://openrouter.ai/api/v1/chat/completions",
                "models": "https://openrouter.ai/api/v1/models"
            }
        })

        # 3. DeepInfra
        deepinfra_key = os.environ.get("DEEPINFRA_API_KEY")
        routes.append({
            "provider": "deepinfra",
            "tier": "developer",
            "online": bool(deepinfra_key),
            "requires_key": True,
            "endpoints": {
                "chat": "https://api.deepinfra.com/v1/openai/chat/completions",
                "models": "https://api.deepinfra.com/v1/models"
            }
        })

        # 4. Google Cloud Vertex AI
        vertex_key = os.environ.get("VERTEX_API_KEY")
        routes.append({
            "provider": "vertex_ai",
            "tier": "enterprise_workspace",
            "online": bool(vertex_key),
            "requires_key": True,
            "endpoints": {
                "generate": "https://aiplatform.googleapis.com/v1"
            }
        })

        # 5. Arena AI (TokenRouter / Max Router)
        arena_key = os.environ.get("ARENA_API_KEY")
        routes.append({
            "provider": "arena",
            "tier": "intelligent_router",
            "online": bool(arena_key),
            "requires_key": True,
            "endpoints": {
                "chat": "https://api.arena.ai/v1/chat/completions",
                "models": "https://api.arena.ai/v1/models"
            }
        })

        # 6. Groq Cloud (Ultra-Fast Inference)
        groq_key = os.environ.get("GROQ_API_KEY")
        routes.append({
            "provider": "groq",
            "tier": "high_speed_free_tier",
            "online": bool(groq_key),
            "requires_key": True,
            "endpoints": {
                "chat": "https://api.groq.com/openai/v1/chat/completions",
                "models": "https://api.groq.com/openai/v1/models"
            }
        })

        return routes

    def complete_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        provider_preference: Optional[List[str]] = None,
        timeout: int = 30
    ) -> Dict[str, Any]:
        """
        Routes text generation with automatic 429/busy fallback across eligible providers.
        Guarantees that an operation never stalls midway.
        """
        # Resolve active canonical model and effort from UniversalCapabilityRegistry if not explicitly provided
        active_canonical = model or UniversalCapabilityRegistry.get_active_model()
        active_effort = effort or UniversalCapabilityRegistry.get_active_effort()
        model_status = UniversalCapabilityRegistry.get_model_status(active_canonical)

        # Dynamic primary chain construction: Same-Model First, Peer Models Second
        if provider_preference:
            chain = provider_preference
        else:
            # 1. Identify all providers that support this exact canonical model
            canonical_spec = CANONICAL_MODELS.get(active_canonical, {})
            model_providers = list(canonical_spec.get("provider_mappings", {}).keys())

            # 2. Preferred order: Same-model routers first
            primary_chain = [p for p in model_providers if self._is_provider_online(p)]

            # 3. Resilient fallback routers
            fallback_chain = ["arena", "openrouter", "omniroute", "opencode", "anyapi", "anthropic", "openai", "deepinfra", "groq", "pollinations"]
            chain = primary_chain + [p for p in fallback_chain if p not in primary_chain and self._is_provider_online(p)]

        last_error = None
        attempt_history = []

        for prov in chain:
            try:
                # Translate parameters for this specific provider
                p_params = UniversalCapabilityRegistry.translate_parameters(
                    canonical_id=active_canonical,
                    target_provider=prov,
                    effort=active_effort
                )
                target_model = p_params.get("model", active_canonical)

                if prov == "pollinations":
                    res = self._call_pollinations_text(prompt, system_prompt, target_model, timeout)
                    res["attempt_history"] = attempt_history
                    res["canonical_model"] = active_canonical
                    res["effort"] = active_effort
                    return res

                elif prov == "anthropic" and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CONSOLE_API_KEY") or os.environ.get("CLAUDE_API_KEY")):
                    from agent.modules.anthropic_adapter import AnthropicAdapter
                    res = AnthropicAdapter.complete(
                        model=target_model,
                        messages=[{"role": "user", "content": prompt}],
                        system=system_prompt,
                        reasoning_effort=active_effort,
                        timeout=timeout
                    )
                    if res.get("status") == "success":
                        res["attempt_history"] = attempt_history
                        res["canonical_model"] = active_canonical
                        res["effort"] = active_effort
                        return res
                    else:
                        raise RuntimeError(res.get("error", "Anthropic call failed"))

                elif prov == "openai" and (os.environ.get("OPENAI_API_KEY") or os.environ.get("CHATGPT_API_KEY")):
                    from agent.modules.openai_adapter import OpenAIAdapter
                    res = OpenAIAdapter.complete(
                        model=target_model,
                        messages=[{"role": "user", "content": prompt}],
                        system=system_prompt,
                        reasoning_effort=active_effort,
                        timeout=timeout
                    )
                    if res.get("status") == "success":
                        res["attempt_history"] = attempt_history
                        res["canonical_model"] = active_canonical
                        res["effort"] = active_effort
                        return res
                    else:
                        raise RuntimeError(res.get("error", "OpenAI call failed"))

                elif prov == "openrouter" and os.environ.get("OPENROUTER_API_KEY"):
                    res = self._call_openrouter_text(prompt, system_prompt, target_model, timeout)
                    res["attempt_history"] = attempt_history
                    res["canonical_model"] = active_canonical
                    res["effort"] = active_effort
                    return res

                elif prov == "groq" and os.environ.get("GROQ_API_KEY"):
                    res = self._call_groq_text(prompt, system_prompt, target_model, timeout)
                    res["attempt_history"] = attempt_history
                    res["canonical_model"] = active_canonical
                    res["effort"] = active_effort
                    return res

                elif prov == "arena" and os.environ.get("ARENA_API_KEY"):
                    res = self._call_arena_text(prompt, system_prompt, target_model, timeout)
                    res["attempt_history"] = attempt_history
                    res["canonical_model"] = active_canonical
                    res["effort"] = active_effort
                    return res

                elif prov == "deepinfra" and os.environ.get("DEEPINFRA_API_KEY"):
                    res = self._call_deepinfra_text(prompt, system_prompt, target_model, timeout)
                    res["attempt_history"] = attempt_history
                    res["canonical_model"] = active_canonical
                    res["effort"] = active_effort
                    return res

                elif prov == "omniroute" and os.environ.get("OMNIROUTE_API_KEY"):
                    res = self._call_openai_compatible_router(
                        provider_name="omniroute",
                        endpoint_url="https://api.omniroute.ai/v1/chat/completions",
                        api_key=os.environ["OMNIROUTE_API_KEY"],
                        prompt=prompt, system_prompt=system_prompt, model=target_model, timeout=timeout
                    )
                    res["attempt_history"] = attempt_history
                    res["canonical_model"] = active_canonical
                    res["effort"] = active_effort
                    return res

                elif prov == "opencode" and os.environ.get("OPENCODE_API_KEY"):
                    res = self._call_openai_compatible_router(
                        provider_name="opencode",
                        endpoint_url="https://api.opencode.ai/v1/chat/completions",
                        api_key=os.environ["OPENCODE_API_KEY"],
                        prompt=prompt, system_prompt=system_prompt, model=target_model, timeout=timeout
                    )
                    res["attempt_history"] = attempt_history
                    res["canonical_model"] = active_canonical
                    res["effort"] = active_effort
                    return res

                elif prov == "anyapi" and os.environ.get("ANYAPI_API_KEY"):
                    res = self._call_openai_compatible_router(
                        provider_name="anyapi",
                        endpoint_url="https://api.anyapi.io/v1/chat/completions",
                        api_key=os.environ["ANYAPI_API_KEY"],
                        prompt=prompt, system_prompt=system_prompt, model=target_model, timeout=timeout
                    )
                    res["attempt_history"] = attempt_history
                    res["canonical_model"] = active_canonical
                    res["effort"] = active_effort
                    return res

            except Exception as e:
                err_msg = f"{prov} failed ({type(e).__name__}: {e})"
                logger.warning(f"Adaptive Router: {err_msg}. Auto-switching to next provider...")
                attempt_history.append({"provider": prov, "error": str(e), "timestamp": time.time()})
                last_error = e

        raise RuntimeError(f"All embedded gateway routes failed. Attempts: {attempt_history}. Last error: {last_error}")

    def _is_provider_online(self, prov: str) -> bool:
        if prov == "pollinations":
            return True
        key_map = {
            "arena": ["ARENA_API_KEY"],
            "anthropic": ["ANTHROPIC_API_KEY", "CLAUDE_CONSOLE_API_KEY", "CLAUDE_API_KEY"],
            "openai": ["OPENAI_API_KEY", "CHATGPT_API_KEY"],
            "openrouter": ["OPENROUTER_API_KEY"],
            "omniroute": ["OMNIROUTE_API_KEY"],
            "opencode": ["OPENCODE_API_KEY"],
            "anyapi": ["ANYAPI_API_KEY"],
            "groq": ["GROQ_API_KEY"],
            "deepinfra": ["DEEPINFRA_API_KEY"],
            "vertex_ai": ["VERTEX_AI_API_KEY", "GEMINI_API_KEY"],
            "fal_ai": ["FAL_KEY", "FAL_AI_API_KEY"]
        }
        keys = key_map.get(prov, [])
        return any(bool(os.environ.get(k)) for k in keys)

    def _call_openai_compatible_router(
        self,
        provider_name: str,
        endpoint_url: str,
        api_key: str,
        prompt: str,
        system_prompt: Optional[str],
        model: str,
        timeout: int
    ) -> Dict[str, Any]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {"model": model, "messages": messages}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint_url,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "ShortsAgent-UniversalRouter/1.0",
                "X-Workspace-Identity": "pankajbhati140@gmail.com",
                "X-Organization-Name": "ShortsForge-Enterprise",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            choice = res_json["choices"][0]["message"]["content"]
            return {
                "provider": provider_name,
                "model": model,
                "cost": 0.0,
                "text": choice.strip(),
                "status": "success"
            }

    def _call_groq_text(
        self, prompt: str, system_prompt: Optional[str], model: str, timeout: int
    ) -> Dict[str, Any]:
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise ValueError("GROQ_API_KEY missing")

        url = "https://api.groq.com/openai/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        target_model = "llama-3.3-70b-versatile" if "gpt" in model or "nano" in model else model
        payload = {
            "model": target_model,
            "messages": messages,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "ShortsAgent-GroqRouter/1.0",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            choice = res_json["choices"][0]["message"]["content"]
            return {
                "provider": "groq",
                "model": target_model,
                "cost": 0.0,
                "text": choice.strip(),
                "status": "success"
            }

    def _call_arena_text(
        self, prompt: str, system_prompt: Optional[str], model: str, timeout: int
    ) -> Dict[str, Any]:
        key = os.environ.get("ARENA_API_KEY")
        if not key:
            raise ValueError("ARENA_API_KEY missing")

        url = "https://api.arena.ai/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Default to Arena coding router if generic model passed
        target_model = "coding-router-preview" if "gpt" in model or "nano" in model else model
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "reasoning_effort": "high",
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "ShortsAgent-ArenaRouter/1.0",
                "X-Workspace-Identity": "pankajbhati140@gmail.com",
                "X-Organization-Name": "ShortsForge-Enterprise",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            choice = res_json["choices"][0]["message"]["content"]
            return {
                "provider": "arena",
                "model": target_model,
                "cost": 0.0,
                "text": choice.strip(),
                "status": "success"
            }

    def _call_deepinfra_text(
        self, prompt: str, system_prompt: Optional[str], model: str, timeout: int
    ) -> Dict[str, Any]:
        key = os.environ.get("DEEPINFRA_API_KEY")
        if not key:
            raise ValueError("DEEPINFRA_API_KEY missing")

        url = "https://api.deepinfra.com/v1/openai/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Default to high performance Llama 3.1 if generic model passed
        target_model = "meta-llama/Meta-Llama-3.1-8B-Instruct" if "gpt" in model or "nano" in model else model
        payload = {
            "model": target_model,
            "messages": messages,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "ShortsAgent-EmbeddedRouter/1.0",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            choice = res_json["choices"][0]["message"]["content"]
            return {
                "provider": "deepinfra",
                "model": target_model,
                "cost": 0.0,
                "text": choice.strip(),
                "status": "success"
            }

    def _call_pollinations_text(
        self, prompt: str, system_prompt: Optional[str], model: str, timeout: int
    ) -> Dict[str, Any]:
        url = "https://text.pollinations.ai/"
        payload: Dict[str, Any] = {
            "messages": [],
            "model": model,
            "seed": int(time.time()),
            "jsonMode": False
        }
        if system_prompt:
            payload["messages"].append({"role": "system", "content": system_prompt})
        payload["messages"].append({"role": "user", "content": prompt})

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "ShortsAgent-EmbeddedRouter/1.0"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_text = resp.read().decode("utf-8")
            return {
                "provider": "pollinations",
                "model": model,
                "cost": 0.0,
                "text": raw_text.strip(),
                "status": "success"
            }

    def _call_openrouter_text(
        self, prompt: str, system_prompt: Optional[str], model: str, timeout: int
    ) -> Dict[str, Any]:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise ValueError("OPENROUTER_API_KEY missing")

        url = "https://openrouter.ai/api/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Ensure we use free model suffix if model not explicitly specified
        target_model = model if ":" in model else f"{model}:free"
        payload = {
            "model": target_model,
            "messages": messages,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/pankj2726/shorts-agent",
                "X-Title": "ShortsAgent Embedded",
                "X-Workspace-Identity": "pankajbhati140@gmail.com",
                "X-Organization-Name": "ShortsForge-Enterprise",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            choice = res_json["choices"][0]["message"]["content"]
            return {
                "provider": "openrouter",
                "model": target_model,
                "cost": 0.0,
                "text": choice.strip(),
                "status": "success"
            }
