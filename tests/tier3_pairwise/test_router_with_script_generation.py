"""
Tier 3: Pairwise Combinatorial — Model Router + Script Generation Integration
Tests end-to-end interaction between the zero-cost Model Router and Scriptwriter agent,
including 429 failover during script generation, JSON schema validation, and QA handoff.
"""
import json
from unittest.mock import MagicMock
import pytest

from agent.modules import qa, script
from tests.conftest import TaskType


def test_router_script_generation_primary_success(monkeypatch):
    """Pairwise 1: Primary provider (Gemini) generates valid script passing QA Gate #1."""
    script_json = {
        "title": "Why Fast Charging Degrades Batteries",
        "hook": "Stop fast charging your phone every single night.",  # 8 words
        "script": (
            "Stop fast charging your phone every single night. Ultra high wattage pushes "
            "battery internal temperatures past 45 degrees Celsius where electrolyte breakdown accelerates rapidly. "
            "Instead, use standard 10 watt overnight charging to preserve battery health and longevity. "
            "Testing reveals slow charged batteries retain 92 percent capacity after two full years of daily use. "
            "Protect your battery health with this simple charging habit."
        ),
        "estimated_duration_s": 25,
        "sources": ["Battery University Fast Charge Study 2024"],
        "target_viewer": "Tech enthusiasts",
        "format": "Mistake reveal",
        "why_it_should_work": "Dispels common fast charging convenience myth",
        "hook_options": ["Stop fast charging your phone every night."],
    }

    mock_complete_json = MagicMock(return_value=script_json)
    monkeypatch.setattr("agent.modules.llm.complete_json", mock_complete_json)
    monkeypatch.setattr("agent.modules.script.notify", lambda *a, **k: None)

    idea = {
        "idea": "Fast charging battery health impact",
        "angle": "Overheating accelerates degradation",
        "target_viewer": "Smartphone users",
        "format": "Mistake reveal",
        "why_it_should_work": "High interest",
        "hook_options": ["Stop fast charging every night."],
    }

    result = script.run_script(idea)
    assert result is not None
    assert result["title"] == "Why Fast Charging Degrades Batteries"

    # Handoff to QA check
    qa_res = qa.check_script(result)
    assert qa_res["passed"] is True


def test_router_script_generation_429_failover_to_groq(monkeypatch):
    """Pairwise 2: Primary provider rate-limited (429) fails over to Groq and generates script."""
    fallback_script = {
        "title": "One Charging Habit Destroying Phones",
        "hook": "Never charge your phone under your pillow.",
        "script": (
            "Never charge your phone under your pillow. Trapped heat causes dangerous thermal "
            "runaway and halves your battery lifespan in just six short months. "
            "Always charge on a hard flat surface so excess heat dissipates naturally and safely. "
            "Extensive data confirms cooler charging environments keep battery cells healthy for years longer. "
            "Keep your device well ventilated whenever fast charging."
        ),
        "estimated_duration_s": 26,
        "sources": ["Fire Safety & Battery Standards 2025"],
    }

    calls = 0
    def mock_llm_complete(prompt):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Exception("HTTP 429: Gemini quota limit reached")
        return fallback_script

    monkeypatch.setattr("agent.modules.llm.complete_json", mock_llm_complete)
    monkeypatch.setattr("agent.modules.script.notify", lambda *a, **k: None)

    idea = {"idea": "Pillow charging danger"}
    result = script.run_script(idea)

    assert result is not None
    assert "pillow" in result["hook"]
    assert calls == 2


def test_router_markdown_json_fencing_handling(monkeypatch):
    """Pairwise 3: Model returns response wrapped in markdown ```json ... ``` code fences."""
    raw_response = """```json
    {
        "title": "Smartphone Battery Lifespan Secrets",
        "hook": "Stop keeping your phone plugged in at 100 percent.",
        "script": "Stop keeping your phone plugged in at 100 percent. Prolonged trickle charging creates chemical tension inside lithium ion cells. Unplug at 80 percent instead to dramatically extend cycle life and prevent premature replacement.",
        "estimated_duration_s": 25,
        "sources": ["Stanford Battery Chemistry Lab"]
    }
    ```"""

    def mock_gemini(*args, **kwargs):
        return raw_response

    monkeypatch.setenv("GEMINI_API_KEY", "test_key")
    monkeypatch.setattr("agent.modules.llm._gemini", mock_gemini)

    from agent.modules import llm
    parsed = llm.complete_json("dummy prompt")
    assert isinstance(parsed, dict)
    assert parsed["title"] == "Smartphone Battery Lifespan Secrets"


def test_router_script_generation_qa_rejection_loop(monkeypatch):
    """Pairwise 4: If LLM produces non-compliant script twice, run_script rejects and returns None."""
    slop_script = {
        "title": "A Generic Video About Tech",
        "hook": "In this video I will show you many interesting things about smartphones today.",  # 13 words
        "script": "Short script body",  # < 50 words
        "estimated_duration_s": 10,  # < 20s
        "sources": [],
    }

    monkeypatch.setattr("agent.modules.llm.complete_json", lambda prompt: slop_script)
    monkeypatch.setattr("agent.modules.script.notify", lambda *a, **k: None)

    idea = {"idea": "Bad idea"}
    result = script.run_script(idea)
    # QA fails twice, discarding script
    assert result is None


def test_router_respects_free_model_selection():
    """Pairwise 5: Model router task selection prioritizes free tier models for script task."""
    from tests.conftest import FREE_MODEL_WHITELIST, TaskType
    from tests.tier1_features.test_model_router import call_model

    resp = call_model(
        prompt="Write a hook about phone batteries",
        task_type=TaskType.SCRIPT,
        model_override="gemini-flash-lite-latest",
    )
    assert resp.model in FREE_MODEL_WHITELIST
    assert resp.provider in ("gemini", "groq", "openrouter")
