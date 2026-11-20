"""
Failure-mode behaviour of the routed completion path.

Every provider call here is a stub. No test in this file contacts Gemini, Groq,
OpenRouter or any other service, so a pass proves the router's control flow and
nothing about a provider's live health.
"""
from __future__ import annotations

import json

import pytest

from agent.router import ROUTING_TABLE, RoutingError


@pytest.fixture
def llm(monkeypatch, router):
    """The llm module with every provider backend replaced by a controllable stub."""
    from agent.modules import llm as llm_mod

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    return llm_mod


def _stub(llm_mod, name, behaviour):
    setattr(llm_mod, name, behaviour)


# ─── Happy path ──────────────────────────────────────────────────────────────

def test_valid_provider_returns_its_completion(router, llm):
    _stub(llm, "_gemini", lambda *a: "HOOK: nine to five, but make it loud")
    assert router.complete("write a hook") == "HOOK: nine to five, but make it loud"


def test_successful_call_is_recorded_with_the_right_model(router, llm):
    _stub(llm, "_gemini", lambda *a: "ok")
    router.complete("write a hook", task="scriptwriting")
    ledger = json.loads(router.QUOTA_PATH.read_text())
    assert ledger["google-gemini"]["requests_today"] == 1
    assert ledger["google-gemini"]["model"] == "gemini-flash-lite-latest"


# ─── Provider failures must fall through ─────────────────────────────────────

def test_timeout_falls_through_to_the_next_provider(router, llm):
    import requests

    def timeout(*a):
        raise requests.exceptions.Timeout("read timed out")

    _stub(llm, "_gemini", timeout)
    _stub(llm, "_groq", lambda *a: "from groq")
    assert router.complete("write a hook") == "from groq"


def test_rate_limit_falls_through_to_the_next_provider(router, llm):
    _stub(llm, "_gemini", lambda *a: (_ for _ in ()).throw(llm._RateLimited("429")))
    _stub(llm, "_groq", lambda *a: "from groq")
    assert router.complete("write a hook") == "from groq"


def test_http_error_falls_through(router, llm):
    import requests

    _stub(llm, "_gemini", lambda *a: (_ for _ in ()).throw(
        requests.exceptions.HTTPError("500 Server Error")))
    _stub(llm, "_groq", lambda *a: "from groq")
    assert router.complete("write a hook") == "from groq"


def test_a_failed_provider_is_recorded_with_its_error(router, llm):
    import requests

    _stub(llm, "_gemini", lambda *a: (_ for _ in ()).throw(
        requests.exceptions.Timeout("read timed out")))
    _stub(llm, "_groq", lambda *a: "from groq")
    router.complete("write a hook")

    ledger = json.loads(router.QUOTA_PATH.read_text())
    assert "Timeout" in ledger["google-gemini"]["last_error"]


# ─── Bad payloads must never be returned as success ──────────────────────────

def test_empty_response_is_not_returned_as_a_completion(router, llm):
    """
    A provider that answers with an empty body has not produced a script.
    Falling through beats returning "" and letting an empty script reach render.
    """
    _stub(llm, "_gemini", lambda *a: "")
    _stub(llm, "_groq", lambda *a: "real content")
    assert router.complete("write a hook") == "real content"


def test_whitespace_only_response_is_rejected(router, llm):
    _stub(llm, "_gemini", lambda *a: "   \n\t  ")
    _stub(llm, "_groq", lambda *a: "real content")
    assert router.complete("write a hook") == "real content"


def test_non_string_response_is_rejected(router, llm):
    """A dict or None from a backend is the wrong type, not a completion."""
    _stub(llm, "_gemini", lambda *a: {"content": "not a string"})
    _stub(llm, "_groq", lambda *a: "real content")
    assert router.complete("write a hook") == "real content"


def test_none_response_is_rejected(router, llm):
    _stub(llm, "_gemini", lambda *a: None)
    _stub(llm, "_groq", lambda *a: "real content")
    assert router.complete("write a hook") == "real content"


# ─── Total failure ───────────────────────────────────────────────────────────

def test_all_providers_failing_raises_llm_error(router, llm):
    _stub(llm, "_gemini", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    _stub(llm, "_groq", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    _stub(llm, "_openrouter", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(llm.LLMError) as exc:
        router.complete("write a hook")
    assert "all eligible providers failed" in str(exc.value)


def test_all_providers_returning_empty_raises(router, llm):
    _stub(llm, "_gemini", lambda *a: "")
    _stub(llm, "_groq", lambda *a: "")
    _stub(llm, "_openrouter", lambda *a: "")

    with pytest.raises(llm.LLMError):
        router.complete("write a hook")


def test_task_with_no_backend_at_all_raises(router, llm):
    """voiceover has no LLM backend; the router must not fabricate one."""
    with pytest.raises(RoutingError):
        router.route("video_generation")


def test_error_message_names_every_provider_tried(router, llm):
    _stub(llm, "_gemini", lambda *a: (_ for _ in ()).throw(RuntimeError("a")))
    _stub(llm, "_groq", lambda *a: (_ for _ in ()).throw(RuntimeError("b")))
    _stub(llm, "_openrouter", lambda *a: (_ for _ in ()).throw(RuntimeError("c")))

    with pytest.raises(llm.LLMError) as exc:
        router.complete("write a hook")
    message = str(exc.value)
    for provider in ROUTING_TABLE["scriptwriting"]:
        assert provider in message, f"{provider} missing from {message}"
