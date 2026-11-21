"""
Provider adapter contract.

The adapters delegate to the existing agent.modules.llm backends, so these
tests stub those backends and assert the adapter layer's own behaviour:
contract uniformity, credential handling, capability reporting, fallback order
and dispatch through the router.

No network access is required.
"""
from __future__ import annotations

import pytest

from agent.modules import providers
from agent.modules.providers import (
    BY_NAME,
    PROVIDERS,
    GeminiProvider,
    GroqProvider,
    HealthStatus,
    OpenRouterProvider,
    Provider,
    UnsupportedCapability,
)

# ─── Contract uniformity ─────────────────────────────────────────────────────

EXPECTED = {
    "google-gemini": ("GEMINI_API_KEY", GeminiProvider),
    "groq": ("GROQ_API_KEY", GroqProvider),
    "openrouter": ("OPENROUTER_API_KEY", OpenRouterProvider),
}


def test_all_three_existing_providers_have_adapters():
    assert {p.name for p in PROVIDERS} == set(EXPECTED)


def test_every_adapter_declares_a_name_and_key_env():
    for provider in PROVIDERS:
        assert provider.name, provider
        assert provider.key_env, provider
        assert isinstance(provider, Provider)


@pytest.mark.parametrize("name,key_env", [(n, k) for n, (k, _) in EXPECTED.items()])
def test_adapter_exposes_the_full_contract(name, key_env):
    adapter = BY_NAME[name]
    assert adapter.key_env == key_env
    for method in ("complete", "list_models", "health", "probe",
                   "has_credential", "_call"):
        assert callable(getattr(adapter, method)), f"{name} lacks {method}"


def test_by_name_index_matches_the_provider_tuple():
    assert set(BY_NAME) == {p.name for p in PROVIDERS}
    assert all(BY_NAME[p.name] is p for p in PROVIDERS)


# ─── Fallback order ──────────────────────────────────────────────────────────

def test_fallback_order_is_gemini_groq_openrouter():
    """The order the llm.complete() chain has always used must be preserved."""
    assert [p.name for p in PROVIDERS] == [
        "google-gemini", "groq", "openrouter",
    ]


def test_configured_preserves_fallback_order(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert [p.name for p in providers.configured()] == [
        "google-gemini", "openrouter",
    ]


def test_configured_is_empty_without_credentials():
    assert providers.configured() == []


# ─── Credentials ─────────────────────────────────────────────────────────────

def test_missing_credential_is_detected(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert BY_NAME["groq"].has_credential() is False


def test_blank_credential_counts_as_missing(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "   ")
    assert BY_NAME["groq"].has_credential() is False


def test_complete_without_a_credential_raises_a_clear_error(monkeypatch):
    from agent.modules.llm import LLMError

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError) as exc:
        BY_NAME["google-gemini"].complete("hi")
    assert "GEMINI_API_KEY" in str(exc.value)


def test_health_reports_missing_credential_without_contacting_the_provider(
    monkeypatch,
):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    status = BY_NAME["groq"].health()
    assert status == HealthStatus("groq", "no_credential",
                                  "GROQ_API_KEY is not set")
    assert status.checked is False


def test_health_with_a_credential_says_it_did_not_probe(monkeypatch):
    """Credential presence is not endpoint health, and must not claim to be."""
    monkeypatch.setenv("GROQ_API_KEY", "k")
    status = BY_NAME["groq"].health()
    assert status.state == "ok"
    assert status.checked is False
    assert status.reason == "credential present"


# ─── Delegation preserves the existing backends ──────────────────────────────

@pytest.mark.parametrize("name,backend", [
    ("google-gemini", "_gemini"),
    ("groq", "_groq"),
    ("openrouter", "_openrouter"),
])
def test_adapter_delegates_to_the_existing_backend(monkeypatch, name, backend):
    """
    The whole point of delegating: retry policy, timeouts and response parsing
    stay in one place. Assert the adapter calls through with unchanged args.
    """
    from agent.modules import llm

    monkeypatch.setenv(EXPECTED[name][0], "k")
    seen = {}

    def fake(prompt, system, temperature, max_tokens, model=None):
        seen.update(prompt=prompt, system=system,
                    temperature=temperature, max_tokens=max_tokens, model=model)
        return f"from-{backend}"

    monkeypatch.setattr(llm, backend, fake)

    out = BY_NAME[name].complete("the prompt", system="the system",
                                 temperature=0.3, max_tokens=128)

    assert out == f"from-{backend}"
    assert seen == {"prompt": "the prompt", "system": "the system",
                    "temperature": 0.3, "max_tokens": 128, "model": None}


@pytest.mark.parametrize("name,backend", [
    ("google-gemini", "_gemini"),
    ("groq", "_groq"),
    ("openrouter", "_openrouter"),
])
def test_adapter_forwards_an_explicit_model(monkeypatch, name, backend):
    """The selected model must reach the backend, not be dropped en route."""
    from agent.modules import llm

    monkeypatch.setenv(EXPECTED[name][0], "k")
    seen = {}

    def fake(prompt, system, temperature, max_tokens, model=None):
        seen["model"] = model
        return "ok"

    monkeypatch.setattr(llm, backend, fake)
    BY_NAME[name].complete("p", model="acme/model-1")
    assert seen["model"] == "acme/model-1"


def test_adapter_propagates_backend_exceptions(monkeypatch):
    from agent.modules import llm

    monkeypatch.setenv("GROQ_API_KEY", "k")

    def boom(*a):
        raise llm._RateLimited("429")

    monkeypatch.setattr(llm, "_groq", boom)
    with pytest.raises(llm._RateLimited):
        BY_NAME["groq"].complete("hi")


# ─── Model discovery: explicit unsupported ───────────────────────────────────

@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_model_discovery_is_explicitly_unsupported(name):
    """
    Must raise rather than return [] or None, so a caller cannot mistake
    "not implemented" for "provider has no models".
    """
    with pytest.raises(UnsupportedCapability) as exc:
        BY_NAME[name].list_models()
    assert name in str(exc.value)
    assert "registry" in str(exc.value)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_endpoint_probing_defers_to_the_health_check(name):
    with pytest.raises(UnsupportedCapability):
        BY_NAME[name].probe()


# ─── Router dispatch ─────────────────────────────────────────────────────────

def test_router_dispatches_through_the_adapter(router, monkeypatch):
    from agent.modules import llm

    monkeypatch.setenv("GROQ_API_KEY", "k")
    calls = []
    monkeypatch.setattr(llm, "_groq", lambda *a: calls.append(a) or "routed")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert router.complete("write a hook", task="scriptwriting") == "routed"
    assert len(calls) == 1


def test_router_still_falls_back_in_order(router, monkeypatch):
    from agent.modules import llm

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")

    def fail(*a):
        raise RuntimeError("boom")

    monkeypatch.setattr(llm, "_gemini", fail)
    monkeypatch.setattr(llm, "_groq", lambda *a: "from groq")

    assert router.complete("write a hook", task="scriptwriting") == "from groq"


# ─── Security ────────────────────────────────────────────────────────────────

def test_no_credential_value_appears_in_repr_or_str(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-value-123")
    adapter = BY_NAME["google-gemini"]
    for text in (repr(adapter), str(adapter)):
        assert "super-secret-value-123" not in text


def test_health_status_never_carries_a_credential(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "another-secret-456")
    status = BY_NAME["openrouter"].health()
    assert "another-secret-456" not in str(status)


def test_unsupported_capability_message_has_no_key_material(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "third-secret-789")
    with pytest.raises(UnsupportedCapability) as exc:
        BY_NAME["groq"].list_models()
    assert "third-secret-789" not in str(exc.value)
