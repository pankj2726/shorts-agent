"""
Provider-routing verification matrix.

Derived from the repository, not from a hand-written expectation list: the tasks
come from TASK_MODALITY and the providers from the committed registry, so a new
entry in either is covered automatically.

Every result here is produced against the local registry with stubbed or absent
credentials. Nothing in this file contacts a provider.
"""
from __future__ import annotations

import json

import pytest

from agent.router import (
    COMPAT_EXCEPTION_PREFIX,
    LOCAL_PROVIDERS,
    ROUTING_TABLE,
    TASK_MODALITY,
    RoutingError,
)

# ─── Structural invariants ───────────────────────────────────────────────────

def test_every_routed_task_declares_a_modality_contract(router):
    """A task with no TASK_MODALITY entry would bypass validation silently."""
    missing = set(ROUTING_TABLE) - set(TASK_MODALITY)
    assert not missing, f"tasks routed without a modality contract: {sorted(missing)}"


def test_routing_table_has_no_unregistered_non_local_provider(router, real_registry):
    known = {e["provider"] for e in real_registry}
    for task, providers in ROUTING_TABLE.items():
        for provider in providers:
            assert provider in known or provider in LOCAL_PROVIDERS, (
                f"{task} routes to {provider!r}, which is neither registered nor local"
            )


def test_no_integrated_provider_has_a_capability_gap(router, real_registry):
    """
    A provider marked `integrated` can receive production traffic, so it must
    declare models and their modalities. Providers still `not_started` are
    allowed to be incomplete — they are reported at info severity instead.
    """
    findings = router.validate_registry(real_registry, policy="strict")
    errors = [f for f in findings if f["severity"] == "error"]
    assert errors == [], f"integrated providers with capability gaps: {errors}"


def test_incomplete_unintegrated_providers_are_still_reported(router, real_registry):
    """Gaps must be observable, not silently ignored."""
    findings = router.validate_registry(real_registry, policy="strict")
    info = [f for f in findings if f["severity"] == "info"]
    assert info, "expected at least cerebras to be reported as incomplete"
    assert all(f["provider"] == "cerebras" for f in info), info


def test_no_video_task_can_reach_an_image_only_provider(router, real_registry):
    """Regression: video_generation used to fall back to pollinations-ai/flux."""
    by = {e["provider"]: e for e in real_registry}
    for task, providers in ROUTING_TABLE.items():
        if "text-to-video" not in TASK_MODALITY.get(task, ()):
            continue
        for provider in providers:
            if provider in LOCAL_PROVIDERS:
                continue
            ok, why = router.supports_task_modality(task, provider, by.get(provider))
            assert ok, f"{provider} cannot serve {task}: {why}"


# ─── The matrix: every provider x every task ─────────────────────────────────

def _provider_ids(real_registry):
    return [e["provider"] for e in real_registry]


@pytest.mark.parametrize("task", sorted(TASK_MODALITY))
def test_modality_verdict_matches_declared_capabilities(router, real_registry, task):
    """
    For each provider, the guard's verdict must agree with the modalities the
    registry actually declares for it.
    """
    by = {e["provider"]: e for e in real_registry}
    required = set(TASK_MODALITY[task])

    for provider, entry in by.items():
        declared = router.provider_modalities(entry)
        ok, why = router.supports_task_modality(task, provider, entry, policy="strict")

        if not required:
            # No constraint: allowed, and marked as an explicit exception.
            assert ok and why == "compat-exception:no-modality-constraint", (provider, why)
        elif not declared:
            assert not ok and "no declared modality" in why, (provider, why)
        elif declared & required:
            assert ok and why == "", (provider, why)
        else:
            assert not ok and "modality mismatch" in why, (provider, why)


@pytest.mark.parametrize("task", sorted(TASK_MODALITY))
def test_an_allowed_provider_always_reports_a_capable_model(
    router, real_registry, task
):
    """
    The real invariant: once the guard allows a provider for a task, the model
    _pick_model reports must be able to serve that task.

    Providers the guard rejects are deliberately excluded — _pick_model falls
    back to models[0] for them, but that combination never reaches a decision.
    Asserting on it would be asserting on a state routing cannot produce.
    """
    by = {e["provider"]: e for e in real_registry}
    required = set(TASK_MODALITY[task])
    if not required:
        pytest.skip("task imposes no modality constraint")

    checked = 0
    for provider, entry in by.items():
        allowed, _ = router.supports_task_modality(
            task, provider, entry, policy="strict"
        )
        if not allowed:
            continue
        checked += 1
        model_id = router._pick_model(entry, task)
        assert model_id is not None, f"{provider} allowed for {task} but has no model"
        chosen = next(
            (m for m in entry["models"] if m.get("model_id") == model_id), None
        )
        assert chosen is not None, f"{provider}: {model_id} not in its own models list"
        assert set(chosen.get("modality") or []) & required, (
            f"{provider}/{model_id} declares "
            f"{sorted(chosen.get('modality') or [])} but {task} needs "
            f"{sorted(required)}"
        )
    assert checked > 0, f"no provider is allowed for {task}; matrix is vacuous"


# ─── Per-task routing with credentials present ───────────────────────────────

EXPECTED_WITH_CREDENTIALS = {
    "scriptwriting": "google-gemini",
    "content_analysis": "google-gemini",
    "image_generation": "pollinations-ai",
    "speech_recognition": "groq",
    "voiceover": "edge-tts",
    "music_generation": "cc0-music-library",
    "captions": "groq",
    "subtitle_formatting": "ffmpeg-local",
    "rendering": "ffmpeg-local",
}


@pytest.mark.parametrize("task,expected", sorted(EXPECTED_WITH_CREDENTIALS.items()))
def test_task_resolves_to_expected_provider(router, all_credentials, task, expected):
    decision = router.route(task)
    assert decision.provider == expected


def test_video_generation_raises_rather_than_returning_wrong_medium(
    router, all_credentials
):
    """
    wan-video is not_started and no other provider can make video. The router
    must fail loudly instead of handing back a still frame.
    """
    with pytest.raises(RoutingError) as exc:
        router.route("video_generation")
    message = str(exc.value)
    assert "wan-video" in message
    assert "pollinations" not in message


def test_captions_no_longer_routes_to_a_text_to_speech_provider(router, all_credentials):
    """edge-tts synthesises speech; captions need transcription."""
    decision = router.route("captions")
    assert decision.provider != "edge-tts"
    assert decision.model == "whisper-large-v3"


# ─── Missing credentials ─────────────────────────────────────────────────────

def test_missing_key_skips_that_provider_and_uses_the_fallback(router, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    # GEMINI_API_KEY deliberately unset.
    decision = router.route("scriptwriting")
    assert decision.provider == "groq"
    skipped = {c["provider"]: c.get("skipped") for c in decision.considered}
    assert skipped["google-gemini"] == "no credential configured"


def test_all_keyed_providers_missing_raises(router):
    with pytest.raises(RoutingError) as exc:
        router.route("scriptwriting")
    assert "no credential configured" in str(exc.value)


def test_credential_free_tasks_still_resolve_with_no_keys(router):
    for task in ("voiceover", "rendering", "subtitle_formatting", "music_generation"):
        assert router.route(task).provider


# ─── Quota accounting ────────────────────────────────────────────────────────

def test_quota_is_recorded_under_the_model_actually_used(router, all_credentials):
    """A multi-model provider must not be logged under its first model."""
    decision = router.route("captions")
    assert decision.provider == "groq"

    router.record_request(decision.provider, model=decision.model, tokens=7)
    ledger = json.loads(router.QUOTA_PATH.read_text())

    assert ledger["groq"]["model"] == "whisper-large-v3"
    assert ledger["groq"]["requests_today"] == 1
    assert ledger["groq"]["tokens_today"] == 7


def test_same_provider_different_tasks_record_different_models(router, all_credentials):
    router.QUOTA_PATH.parent.mkdir(parents=True, exist_ok=True)

    d1 = router.route("captions")
    router.record_request(d1.provider, model=d1.model)
    first = json.loads(router.QUOTA_PATH.read_text())["groq"]["model"]

    router.QUOTA_PATH.unlink()
    d2 = router.route("scriptwriting")
    # Force groq to be the selection by removing the higher-priority key.
    import os

    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("OPENROUTER_API_KEY", None)
    d2 = router.route("scriptwriting")
    router.record_request(d2.provider, model=d2.model)
    second = json.loads(router.QUOTA_PATH.read_text())["groq"]["model"]

    assert first == "whisper-large-v3"
    assert second == "openai/gpt-oss-120b"


def test_exhausted_provider_is_skipped(router, monkeypatch, all_credentials):
    monkeypatch.setenv("LLM_QUOTA_PATH", str(router.QUOTA_PATH))
    router.save_quota({
        "google-gemini": {
            "provider": "google-gemini",
            "requests_today": 10,
            "requests_limit_daily": 10,
            "tokens_today": 0,
            "tokens_limit_daily": None,
            "last_reset_date": router._today(),
        }
    })
    decision = router.route("scriptwriting")
    assert decision.provider == "groq"


# ─── Compatibility policy ────────────────────────────────────────────────────

def test_strict_rejects_a_registered_provider_with_no_modality(router):
    entry = {"provider": "mystery", "models": [{"model_id": "m1", "modality": []}]}
    ok, why = router.supports_task_modality(
        "scriptwriting", "mystery", entry, policy="strict"
    )
    assert not ok
    assert "no declared modality" in why


def test_legacy_allows_it_but_marks_the_exception(router):
    entry = {"provider": "mystery", "models": [{"model_id": "m1", "modality": []}]}
    ok, why = router.supports_task_modality(
        "scriptwriting", "mystery", entry, policy="legacy"
    )
    assert ok
    assert why.startswith(COMPAT_EXCEPTION_PREFIX)


def test_policy_is_configurable_via_environment(router, monkeypatch):
    assert router.modality_policy() == "strict"
    monkeypatch.setenv("ROUTER_MODALITY_POLICY", "legacy")
    assert router.modality_policy() == "legacy"
    monkeypatch.setenv("ROUTER_MODALITY_POLICY", "nonsense")
    assert router.modality_policy() == "strict"


def test_local_tooling_is_a_marked_documented_exception(router):
    for provider in sorted(LOCAL_PROVIDERS):
        ok, why = router.supports_task_modality(
            "rendering", provider, None, policy="strict"
        )
        assert ok
        assert why == "compat-exception:local-tooling"


def test_exceptions_are_visible_on_the_decision(router):
    decision = router.route("rendering")
    assert decision.provider == "ffmpeg-local"
    assert any(
        e.endswith("compat-exception:local-tooling") for e in decision.compat_exceptions
    ), decision.compat_exceptions
    assert "compat_exceptions" in decision.as_dict()


def test_a_normal_decision_reports_no_exceptions(router, all_credentials):
    decision = router.route("scriptwriting")
    assert decision.compat_exceptions == []
