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

    def _invoke(
        self,
        prompt: str,
        system: str | None,
        temperature: float,
        max_tokens: int,
        model: str | None = None,
    ) -> str:
        raise NotImplementedError

    def _call(
        self,
        prompt: str,
        system: str | None,
        temperature: float,
        max_tokens: int,
        model: str | None = None,
    ) -> str:
        """
        Reset telemetry, then invoke the backend.

        The provider-reported model lives in module-level state, so it must be
        cleared before every call. Without this, a provider that does not
        report a model would inherit the previous provider's, and the recorded
        model would describe a call that never happened.
        """
        from agent.modules import llm

        llm.LAST_ACTUAL_MODEL = None
        llm.LAST_USAGE = None
        return self._invoke(prompt, system, temperature, max_tokens, model)

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> str:
        """
        Generate text. Raises if the credential is absent, so callers get one
        clear failure mode instead of a provider-specific one.

        `model` selects the model the request asks for. Omitting it leaves each
        backend on its historical default, so existing behaviour is unchanged.
        """
        if not self.has_credential():
            from agent.modules.llm import LLMError

            raise LLMError(f"{self.key_env} is empty")
        return self._call(prompt, system, temperature, max_tokens, model)

    def declared_models(self) -> list[ModelInfo]:
        """
        Models this provider declares in the provider registry.

        This is distinct from list_models(): the registry is a curated,
        checked-in document, while list_models() would query the provider's
        live endpoint. Only the former is available offline.
        """
        from agent.router import registry_by_id

        entry = registry_by_id().get(self.name) or {}
        out: list[ModelInfo] = []
        for item in entry.get("models") or []:
            if not isinstance(item, dict) or not item.get("model_id"):
                continue
            out.append(
                ModelInfo(
                    model_id=item["model_id"],
                    modality=tuple(item.get("modality") or ()),
                    context_tokens=item.get("context_tokens"),
                    notes=item.get("notes") or "",
                )
            )
        return out

    def supports_model(self, model_id: str) -> bool:
        """Whether the registry declares `model_id` for this provider."""
        return any(m.model_id == model_id for m in self.declared_models())

    def last_usage(self) -> dict | None:
        """Token usage the provider reported for its most recent call."""
        from agent.modules import llm

        return llm.LAST_USAGE

    def last_model(self) -> str | None:
        """
        Model id the provider reported for its most recent successful call.

        Read from agent.modules.llm telemetry. Returns None if no call has been
        made in this process.
        """
        from agent.modules import llm

        return llm.LAST_ACTUAL_MODEL


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

    def _invoke(self, prompt, system, temperature, max_tokens, model=None):
        from agent.modules.llm import _gemini

        return _gemini(prompt, system, temperature, max_tokens, model)


class GroqProvider(Provider):
    name = "groq"
    key_env = "GROQ_API_KEY"

    def _invoke(self, prompt, system, temperature, max_tokens, model=None):
        from agent.modules.llm import _groq

        return _groq(prompt, system, temperature, max_tokens, model)


class OpenRouterProvider(Provider):
    name = "openrouter"
    key_env = "OPENROUTER_API_KEY"

    def _invoke(self, prompt, system, temperature, max_tokens, model=None):
        from agent.modules.llm import _openrouter

        return _openrouter(prompt, system, temperature, max_tokens, model)


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
