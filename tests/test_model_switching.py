"""
Model selection, routing unification and execution telemetry.

These cover the defect where the router chose a model and the backend then
re-read its own module constant, so the selection never reached the request.
The strongest assertions here inspect the outgoing HTTP call itself, not just
the return value: a model that is selected but not sent is exactly the bug.

No network access. requests.post is stubbed at the module boundary.
"""
from __future__ import annotations

import json

import pytest

from agent.modules import llm
from agent.router import RoutingError


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _gemini_ok(text="ok", model_version="reported-gemini"):
    return _Resp(200, {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "modelVersion": model_version,
    })


def _chat_ok(text="ok", model="reported-model"):
    return _Resp(200, {"choices": [{"message": {"content": text}}], "model": model})


@pytest.fixture
def routed(router, real_registry, all_credentials, monkeypatch):
    """Router enabled against the committed registry, with a usable quota file."""
    router.REGISTRY_PATH.write_text(json.dumps(real_registry), encoding="utf-8")
    monkeypatch.setenv("LLM_ROUTER", "true")
    return router


# ─── The selected model must reach the wire ──────────────────────────────────

def test_gemini_puts_the_selected_model_in_the_endpoint(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-value-abc")
    seen = {}

    def fake_post(url, json=None, timeout=None, **kw):
        seen["url"] = url
        return _gemini_ok()

    monkeypatch.setattr(llm.requests, "post", fake_post)
    llm._gemini("p", None, 0.7, 100, model="acme/model-x")

    assert "acme/model-x:generateContent" in seen["url"]


def test_groq_puts_the_selected_model_in_the_payload(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "secret-value-abc")
    seen = {}

    def fake_post(url, json=None, timeout=None, **kw):
        seen["json"] = json
        return _chat_ok()

    monkeypatch.setattr(llm.requests, "post", fake_post)
    llm._groq("p", None, 0.7, 100, model="acme/model-y")

    assert seen["json"]["model"] == "acme/model-y"


def test_openrouter_puts_the_selected_model_in_the_payload(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-value-abc")
    seen = {}

    def fake_post(url, json=None, timeout=None, **kw):
        seen["json"] = json
        return _chat_ok()

    monkeypatch.setattr(llm.requests, "post", fake_post)
    llm._openrouter("p", None, 0.7, 100, model="acme/model-z")

    assert seen["json"]["model"] == "acme/model-z"


# ─── Defaults are unchanged ──────────────────────────────────────────────────

def test_backends_keep_their_historical_default_models(monkeypatch):
    """No model argument must reproduce exactly the pre-change behaviour."""
    for env in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(env, "k")
    urls, payloads = [], []

    def fake_post(url, json=None, timeout=None, **kw):
        urls.append(url)
        payloads.append(json)
        if "generativelanguage" in url:
            return _gemini_ok()
        return _chat_ok()

    monkeypatch.setattr(llm.requests, "post", fake_post)
    llm._gemini("p", None, 0.7, 100)
    llm._groq("p", None, 0.7, 100)
    llm._openrouter("p", None, 0.7, 100)

    assert f"{llm.GEMINI_MODEL}:generateContent" in urls[0]
    assert payloads[1]["model"] == llm.GROQ_MODEL
    assert payloads[2]["model"] == llm.OPENROUTER_MODEL


def test_timeouts_and_retry_constants_are_unchanged():
    """Timeout limits and retry semantics must survive the refactor."""
    assert llm._MAX_RETRIES == 3
    assert llm._BACKOFF_MIN == 2
    assert llm._BACKOFF_MAX == 30


# ─── Telemetry reports what was actually used ────────────────────────────────

def test_actual_model_comes_from_the_provider_response(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")

    def fake_post(url, json=None, timeout=None, **kw):
        return _chat_ok(model="groq-served-something-else")

    monkeypatch.setattr(llm.requests, "post", fake_post)
    llm._groq("p", None, 0.7, 100, model="requested-model")

    assert llm.LAST_ACTUAL_MODEL == "groq-served-something-else"


def test_telemetry_flags_a_substituted_model(routed, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_SCRIPTWRITING", "groq")
    monkeypatch.setenv("LLM_MODEL_SCRIPTWRITING", "openai/gpt-oss-120b")

    def serve(*a, **k):
        # The provider answered, but with a different model than asked for.
        llm.LAST_ACTUAL_MODEL = "a-different-model"
        return "text"

    monkeypatch.setattr(llm, "_groq", serve)

    routed.complete("write a hook", task="scriptwriting")
    exec_info = routed.last_execution()

    assert exec_info["model_requested"] == "openai/gpt-oss-120b"
    assert exec_info["model_used"] == "a-different-model"
    assert exec_info["model_substituted"] is True


def test_provider_model_state_does_not_leak_between_calls(routed, monkeypatch):
    """
    A provider that reports no model must not inherit the previous provider's.
    Without a reset before each call, the ledger would name a model that never
    served the request.
    """
    monkeypatch.setenv("LLM_PROVIDER_SCRIPTWRITING", "groq")
    monkeypatch.setattr(llm, "LAST_ACTUAL_MODEL", "stale-model-from-earlier")

    def serve(*a, **k):
        return "text"  # reports nothing

    monkeypatch.setattr(llm, "_groq", serve)

    routed.complete("write a hook", task="scriptwriting")
    exec_info = routed.last_execution()

    assert exec_info["model_used"] != "stale-model-from-earlier"
    assert exec_info["model_substituted"] is False


def test_telemetry_records_the_provider_that_served(routed, monkeypatch):
    """Every backend is stubbed, so no test here reaches the network."""
    monkeypatch.setenv("LLM_PROVIDER_SCRIPTWRITING", "groq")
    monkeypatch.setattr(llm, "_groq", lambda *a, **k: "text")
    for backend in ("_gemini", "_openrouter"):
        monkeypatch.setattr(
            llm, backend,
            lambda *a, **k: pytest.fail(f"{backend} must not be called"),
        )

    routed.complete("write a hook", task="scriptwriting")
    exec_info = routed.last_execution()

    assert exec_info["provider_used"] == "groq"
    assert exec_info["requested_provider"] == "groq"
    assert exec_info["task"] == "scriptwriting"
    assert exec_info["at"]


# ─── Task-specific switching ─────────────────────────────────────────────────

def test_task_specific_model_reaches_the_backend(routed, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_SCRIPTWRITING", "groq")
    monkeypatch.setenv("LLM_MODEL_SCRIPTWRITING", "openai/gpt-oss-120b")
    seen = {}
    monkeypatch.setattr(
        llm, "_groq",
        lambda p, s, t, m, model=None: seen.update(model=model) or "text",
    )

    routed.complete("write a hook", task="scriptwriting")
    assert seen["model"] == "openai/gpt-oss-120b"


def test_different_tasks_get_different_models(routed, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_SCRIPTWRITING", "groq")
    monkeypatch.setenv("LLM_MODEL_SCRIPTWRITING", "openai/gpt-oss-120b")
    monkeypatch.setenv("LLM_PROVIDER_CONTENT_ANALYSIS", "groq")
    monkeypatch.setenv("LLM_MODEL_CONTENT_ANALYSIS", "whisper-large-v3")
    seen = []
    monkeypatch.setattr(
        llm, "_groq",
        lambda p, s, t, m, model=None: seen.append(model) or "text",
    )

    routed.complete("write a hook", task="scriptwriting")
    routed.complete("analyse this", task="content_analysis")

    assert seen == ["openai/gpt-oss-120b", "whisper-large-v3"]


def test_env_resolution_prefers_task_over_default(monkeypatch):
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "default-model")
    monkeypatch.setenv("LLM_MODEL_SCRIPTWRITING", "task-model")
    from agent.router import resolve_provider_model

    assert resolve_provider_model("scriptwriting") == (None, "task-model")
    assert resolve_provider_model("content_analysis") == (None, "default-model")


def test_unconfigured_env_leaves_routing_to_the_table(routed, monkeypatch):
    from agent.router import resolve_provider_model

    for var in ("LLM_DEFAULT_MODEL", "LLM_DEFAULT_PROVIDER",
                "LLM_MODEL_SCRIPTWRITING", "LLM_PROVIDER_SCRIPTWRITING"):
        monkeypatch.delenv(var, raising=False)
    assert resolve_provider_model("scriptwriting") == (None, None)


# ─── Unsupported model rejection ─────────────────────────────────────────────

def test_pinned_unsupported_model_is_rejected_before_any_request(routed, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_SCRIPTWRITING", "groq")
    monkeypatch.setenv("LLM_MODEL_SCRIPTWRITING", "no-such-model-9000")
    calls = []
    monkeypatch.setattr(
        llm, "_groq", lambda *a, **k: calls.append(1) or "text",
    )

    with pytest.raises(RoutingError) as exc:
        routed.complete("write a hook", task="scriptwriting")

    assert "no-such-model-9000" in str(exc.value)
    assert "groq" in str(exc.value)
    assert calls == [], "the request must not be sent when validation fails"


def test_generic_default_model_falls_back_rather_than_failing(routed, monkeypatch):
    """
    LLM_DEFAULT_MODEL is a preference, not a pin: a provider that does not
    declare it uses its own registry default, and says so in telemetry.
    """
    monkeypatch.setenv("LLM_PROVIDER_SCRIPTWRITING", "groq")
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "not-a-groq-model")
    seen = {}
    monkeypatch.setattr(
        llm, "_groq",
        lambda p, s, t, m, model=None: seen.update(model=model) or "text",
    )

    routed.complete("write a hook", task="scriptwriting")

    assert seen["model"] != "not-a-groq-model"
    assert seen["model"] is not None
    assert routed.last_execution()["fallbacks"]


# ─── Fallback order ──────────────────────────────────────────────────────────

def test_fallback_tries_the_next_provider_and_records_it(routed, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_SCRIPTWRITING", "groq")
    order = []

    def fail(*a, **k):
        order.append("groq")
        raise RuntimeError("boom")

    monkeypatch.setattr(llm, "_groq", fail)
    monkeypatch.setattr(
        llm, "_openrouter",
        lambda *a, **k: order.append("openrouter") or "text",
    )
    monkeypatch.setattr(
        llm, "_gemini", lambda *a, **k: order.append("gemini") or "text",
    )

    assert routed.complete("write a hook", task="scriptwriting") == "text"
    assert order[0] == "groq"
    assert routed.last_execution()["fallbacks"]


def test_all_providers_failing_raises(routed, monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("boom")

    for backend in ("_gemini", "_groq", "_openrouter"):
        monkeypatch.setattr(llm, backend, fail)

    with pytest.raises(llm.LLMError):
        routed.complete("write a hook", task="scriptwriting")


# ─── JSON completion shares the path ─────────────────────────────────────────

def test_complete_json_routes_like_complete(routed, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_SCRIPTWRITING", "groq")
    monkeypatch.setenv("LLM_MODEL_SCRIPTWRITING", "openai/gpt-oss-120b")
    seen = {}
    monkeypatch.setattr(
        llm, "_groq",
        lambda p, s, t, m, model=None: seen.update(model=model) or '{"a": 1}',
    )

    assert llm.complete_json("make json", task="scriptwriting") == {"a": 1}
    assert seen["model"] == "openai/gpt-oss-120b"


def test_complete_json_strips_code_fences(monkeypatch):
    monkeypatch.delenv("LLM_ROUTER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(llm, "_gemini", lambda *a, **k: '```json\n{"a": 2}\n```')

    assert llm.complete_json("make json") == {"a": 2}


def test_complete_json_retries_then_fails(monkeypatch):
    monkeypatch.delenv("LLM_ROUTER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_gemini", lambda *a, **k: "not json at all")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    with pytest.raises(llm.LLMError):
        llm.complete_json("make json")


def test_complete_json_uses_the_direct_chain_when_the_router_is_off(monkeypatch):
    monkeypatch.delenv("LLM_ROUTER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    seen = {}
    monkeypatch.setattr(
        llm, "_gemini",
        lambda p, s, t, m, model=None: seen.update(model=model) or '{"ok": true}',
    )

    assert llm.complete_json("make json") == {"ok": True}
    assert seen["model"] is None


# ─── Registry correction ─────────────────────────────────────────────────────

def test_openrouter_registry_declares_the_model_the_code_sends(real_registry):
    """
    The openrouter entry previously listed two Groq models, neither of which
    was ever requested from OpenRouter, while the model actually sent was
    undeclared.
    """
    entry = next(e for e in real_registry if e["provider"] == "openrouter")
    declared = {m["model_id"] for m in entry["models"]}

    assert llm.OPENROUTER_MODEL in declared
    assert "openai/gpt-oss-120b" not in declared
    assert "qwen/qwen3.8-27b" not in declared


def test_openrouter_registry_entry_states_it_is_unverified(real_registry):
    """Availability cannot be asserted from here, and must not look asserted."""
    entry = next(e for e in real_registry if e["provider"] == "openrouter")
    notes = " ".join(m.get("notes") or "" for m in entry["models"]).lower()

    assert "unverified" in notes


# ─── Credential redaction ────────────────────────────────────────────────────

SECRET = "super-secret-credential-value-9182"


def test_telemetry_carries_no_credential(routed, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", SECRET)
    monkeypatch.setattr(llm, "_groq", lambda *a, **k: "text")
    monkeypatch.setattr(llm, "LAST_ACTUAL_MODEL", "some-model")

    routed.complete("write a hook", task="scriptwriting")

    assert SECRET not in json.dumps(routed.last_execution())


def test_routing_error_carries_no_credential(routed, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", SECRET)
    monkeypatch.setenv("LLM_PROVIDER_SCRIPTWRITING", "groq")
    monkeypatch.setenv("LLM_MODEL_SCRIPTWRITING", "no-such-model")

    with pytest.raises(RoutingError) as exc:
        routed.complete("write a hook", task="scriptwriting")

    assert SECRET not in str(exc.value)


def test_missing_credential_error_names_the_variable_not_the_value(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(llm.LLMError) as exc:
        llm._gemini("p", None, 0.7, 100)

    assert "GEMINI_API_KEY" in str(exc.value)
    assert SECRET not in str(exc.value)


def test_pinned_model_without_a_provider_resolves_the_provider(routed, monkeypatch):
    """
    Naming only a model must work: the provider that declares it is selected
    from the task's routing table, rather than rejecting the request.
    """
    monkeypatch.setenv("LLM_MODEL_SCRIPTWRITING", "openai/gpt-oss-120b")
    seen = {}
    monkeypatch.setattr(
        llm, "_groq",
        lambda p, s, t, m, model=None: seen.update(model=model) or "text",
    )
    monkeypatch.setattr(
        llm, "_gemini", lambda *a, **k: "should not be used",
    )

    routed.complete("write a hook", task="scriptwriting")

    assert seen["model"] == "openai/gpt-oss-120b"
    assert routed.last_execution()["provider_used"] == "groq"


def test_pinned_model_no_provider_can_serve_it_is_rejected(routed, monkeypatch):
    monkeypatch.setenv("LLM_MODEL_SCRIPTWRITING", "totally-unknown-model")
    calls = []
    for backend in ("_gemini", "_groq", "_openrouter"):
        monkeypatch.setattr(
            llm, backend, lambda *a, **k: calls.append(1) or "text",
        )

    with pytest.raises(RoutingError) as exc:
        routed.complete("write a hook", task="scriptwriting")

    assert "totally-unknown-model" in str(exc.value)
    assert calls == []
