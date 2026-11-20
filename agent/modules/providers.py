"""
providers.py — uniform interface over the LLM backends.

Before this module, adding a provider meant editing four unrelated places and
writing a bare function with an ad-hoc signature. This defines one contract so
callers can treat every provider the same way.

Deliberate design choice: the adapters *delegate* to the existing
agent.modules.llm backend functions rather than reimplementing them. Those
functions carry the retry policy, backoff, timeouts, status-code handling and
response parsing that production depends on; copying them would duplicate that
logic and risk drifting from it. Delegation preserves it by construction.

Consequence: this module is a structural refactor. It changes no model default,
no timeout, no retry count and no fallback order.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class UnsupportedCapability(Exception):
    """Raised when a provider does not implement an optional capability."""


@dataclass(frozen=True)
class ModelInfo:
    """A model a provider can serve."""

    model_id: str
    modality: tuple[str, ...] = ()
    context_tokens: int | None = None
    notes: str = ""


@dataclass(frozen=True)
class HealthStatus:
    """
    Provider availability.

    `state` is one of:
      ok           — usable
      no_credential — configured key missing or empty
      unknown      — not probed; credential presence only
    """

    provider: str
    state: str
    reason: str = ""
    latency_ms: int | None = None
    checked: bool = False


class Provider:
    """
    Contract every LLM provider adapter implements.

    Subclasses must set `name` and `key_env`, and implement `_call()`.
    `list_models()` and `probe()` are optional and raise
    UnsupportedCapability by default, so a caller can ask any provider and get
    an explicit answer rather than a silent None.
    """

    name: str = ""
    key_env: str = ""

    def has_credential(self) -> bool:
        import os

        return bool(os.environ.get(self.key_env, "").strip())

    def _call(
        self,
        prompt: str,
        system: str | None,
        temperature: float,
        max_tokens: int,
    ) -> str:
        raise NotImplementedError

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """
        Generate text. Raises if the credential is absent, so callers get one
        clear failure mode instead of a provider-specific one.
        """
        if not self.has_credential():
            from agent.modules.llm import LLMError

            raise LLMError(f"{self.key_env} is empty")
        return self._call(prompt, system, temperature, max_tokens)

    def list_models(self) -> list[ModelInfo]:
        """
        Discover available models.

        Not implemented for any provider yet. Live discovery requires each
        provider's models endpoint, which has not been verified against real
        credentials; rather than guess at endpoints, this raises explicitly.
        The registry in agents/agent-4-providers/registry/providers.json is the
        current source of truth for declared models.
        """
        raise UnsupportedCapability(
            f"{self.name}: live model discovery is not implemented; consult the "
            "provider registry for declared models"
        )

    def probe(self) -> HealthStatus:
        """
        Check endpoint availability.

        Not implemented here: scripts/health_check.py already probes the real
        endpoints and writes the registry, and duplicating that logic would
        create two sources of truth. `health()` below reports credential state
        only, and says so.
        """
        raise UnsupportedCapability(
            f"{self.name}: endpoint probing is handled by scripts/health_check.py"
        )

    def health(self) -> HealthStatus:
        """
        Credential-level availability. Does not contact the provider.

        Callers needing real endpoint health should read the registry produced
        by the health-check workflow.
        """
        if not self.has_credential():
            return HealthStatus(self.name, "no_credential",
                                f"{self.key_env} is not set")
        return HealthStatus(self.name, "ok", "credential present", checked=False)


# ─── Concrete adapters ───────────────────────────────────────────────────────

class GeminiProvider(Provider):
    name = "google-gemini"
    key_env = "GEMINI_API_KEY"

    def _call(self, prompt, system, temperature, max_tokens):
        from agent.modules.llm import _gemini

        return _gemini(prompt, system, temperature, max_tokens)


class GroqProvider(Provider):
    name = "groq"
    key_env = "GROQ_API_KEY"

    def _call(self, prompt, system, temperature, max_tokens):
        from agent.modules.llm import _groq

        return _groq(prompt, system, temperature, max_tokens)


class OpenRouterProvider(Provider):
    name = "openrouter"
    key_env = "OPENROUTER_API_KEY"

    def _call(self, prompt, system, temperature, max_tokens):
        from agent.modules.llm import _openrouter

        return _openrouter(prompt, system, temperature, max_tokens)


# Insertion order is the default fallback order, matching the chain that
# agent.modules.llm.complete() has always used: Gemini -> Groq -> OpenRouter.
PROVIDERS: tuple[Provider, ...] = (
    GeminiProvider(),
    GroqProvider(),
    OpenRouterProvider(),
)

BY_NAME: dict[str, Provider] = {p.name: p for p in PROVIDERS}


def get(name: str) -> Provider:
    """Return the adapter for a provider id, or raise KeyError."""
    return BY_NAME[name]


def configured() -> list[Provider]:
    """Providers with a credential present, in fallback order."""
    return [p for p in PROVIDERS if p.has_credential()]
