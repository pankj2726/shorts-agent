"""
Shared fixtures for the Shorts Agent test suite.

Two invariants every test relies on:

* the repo is never mutated — the quota tracker and registry are redirected to
  temporary paths, because both are module-level constants rather than env vars;
* no test makes a live provider call. Anything that would reach the network gets
  a stub injected, and results from stubs are labelled as such.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Environment variables that would let a test reach a real provider, or change
# routing based on whatever happens to be set on the developer's machine.
CREDENTIAL_VARS = [
    "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
    "POLLINATIONS_AI_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY",
    "FAL_AI_API_KEY", "OPENAI_API_KEY", "VERTEX_AI_API_KEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "WEBHOOK_URL", "WEBHOOK_SECRET",
    "YT_API_KEY", "YT_REFRESH_TOKEN", "DATABASE_URL",
]
BEHAVIOUR_VARS = ["LLM_ROUTER", "ROUTER_MODALITY_POLICY", "DB_BACKEND"]
# Per-task model selection is read from LLM_PROVIDER_<TASK> / LLM_MODEL_<TASK>
# plus the two generic defaults. A developer who has any of these set locally
# would otherwise change routing for the whole suite, so they are cleared too.
BEHAVIOUR_VARS += [
    "LLM_DEFAULT_PROVIDER", "LLM_DEFAULT_MODEL",
    "LLM_PROVIDER_SCRIPTWRITING", "LLM_MODEL_SCRIPTWRITING",
    "LLM_PROVIDER_CONTENT_ANALYSIS", "LLM_MODEL_CONTENT_ANALYSIS",
    # Telegram video delivery is paused; a value left in the developer shell
    # would silently re-enable it for the whole suite.
    "SHORTS_TELEGRAM_VIDEO_ENABLED",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test from a known-empty credential environment."""
    for name in CREDENTIAL_VARS + BEHAVIOUR_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def router(tmp_path, monkeypatch):
    """
    The router module with its mutable state redirected into tmp_path.

    QUOTA_PATH and REGISTRY_PATH are module constants read at call time, so
    reassigning them is effective. Restored automatically by monkeypatch.
    """
    from agent import router as router_mod

    monkeypatch.setattr(router_mod, "QUOTA_PATH", tmp_path / "quota_tracker.json")
    monkeypatch.setattr(router_mod, "REGISTRY_PATH", tmp_path / "providers.json")
    # Point the decision log at tmp too so tests never append to the repo's log.
    monkeypatch.setattr(router_mod, "DECISION_LOG", tmp_path / "decisions.jsonl")
    return router_mod


@pytest.fixture
def real_registry():
    """The committed registry, unmodified, as a parsed list."""
    import json

    path = REPO_ROOT / "agents" / "agent-4-providers" / "registry" / "providers.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def all_credentials(monkeypatch):
    """Every provider key present, with dummy values. No network is implied."""
    from agent import router as router_mod

    for env_name in router_mod.PROVIDER_KEY_ENV.values():
        monkeypatch.setenv(env_name, "test-key-not-a-real-credential")
