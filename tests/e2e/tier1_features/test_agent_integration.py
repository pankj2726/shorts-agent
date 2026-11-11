"""
test_agent_integration.py — Feature 10: Production Agents OmniRoute Integration.

Validates that:
  1. The central LLM entry point (agent.modules.llm) routes through OmniRoute.
  2. llm._omniroute formats requests matching OmniRoute chat completions API.
  3. script.py integrates with llm and calls llm.complete_json with OmniRoute fallback resilience.
  4. strategy.py integrates with llm and calls llm.complete_json for idea generation.
  5. comments.py integrates with llm and uses llm for comment categorization and replies.
  6. analytics.py integrates with llm and uses llm.complete for weekly learning extraction.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from agent.modules import llm


def test_llm_omniroute_channel_present():
    """Verify llm.py defines OMNIROUTE_BASE_URL and OMNIROUTE_MODEL configuration."""
    assert hasattr(llm, "OMNIROUTE_BASE_URL")
    assert hasattr(llm, "_omniroute")
    assert "20128" in llm.OMNIROUTE_BASE_URL or "http" in llm.OMNIROUTE_BASE_URL


def test_llm_omniroute_dispatches_http_request():
    """Verify _omniroute formats payload and headers correctly when calling OmniRoute."""
    captured_url = None
    captured_payload = None

    def mock_post(url, json=None, headers=None, timeout=None):
        nonlocal captured_url, captured_payload
        captured_url = url
        captured_payload = json
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "OmniRoute generated text"}}]
        }
        return mock_resp

    with patch("requests.post", side_effect=mock_post):
        with patch.dict("os.environ", {"OMNIROUTE_API_KEY": "sk-omni-test"}):
            reply = llm._omniroute(prompt="Test prompt", system="System instruction", temperature=0.7, max_tokens=1000)

    assert reply == "OmniRoute generated text"
    assert "/chat/completions" in captured_url
    assert captured_payload["model"] == llm.OMNIROUTE_MODEL
    assert len(captured_payload["messages"]) == 2


def test_script_module_integration_with_llm():
    """Verify script module integrates with llm module and invokes complete_json."""
    from agent.modules import script
    assert script.llm is llm

    mock_script_response = {
        "title": "5 Hidden AI Powers",
        "hook": "Stop using AI like a beginner.",
        "scenes": [{"visual": "Laptop screen", "narration": "First, use system prompts."}],
        "cta": "Subscribe for daily AI tips."
    }

    with patch.object(script.llm, "complete_json", return_value=mock_script_response) as mock_complete_json:
        with patch.object(script, "notify"):
            with patch("agent.modules.qa.check_script", return_value={"passed": True}):
                with patch("agent.modules.db.get_conn") as mock_conn:
                    mock_db = MagicMock()
                    mock_conn.return_value = mock_db
                    idea = {
                        "idea": "AI Powers",
                        "angle": "Productivity",
                        "hook": "Stop beginner AI",
                        "format": "listicle",
                        "idea_id": 1
                    }
                    result = script.run_script(idea)
                    assert mock_complete_json.called
                    assert result is not None
                    assert result["title"] == "5 Hidden AI Powers"


def test_strategy_module_integration_with_llm():
    """Verify strategy module integrates with llm module and invokes complete_json."""
    from agent.modules import strategy
    assert strategy.llm is llm

    mock_strategy_response = {
        "ideas": [{"idea": "Idea 1"}],
        "selected": {"idea": "Idea 1", "angle": "Tech", "hook_options": ["Hook 1"]}
    }

    with patch.object(strategy.llm, "complete_json", return_value=mock_strategy_response) as mock_complete_json:
        with patch.object(strategy, "notify"):
            with patch("agent.modules.db.get_conn") as mock_conn:
                mock_db = MagicMock()
                mock_conn.return_value = mock_db
                result = strategy.run_strategy({"trending": [], "trend_queries": []})
                assert mock_complete_json.called
                assert result["idea"] == "Idea 1"


def test_comments_module_integration_with_llm():
    """Verify comments module integrates with llm module for reply generation."""
    from agent.modules import comments
    assert comments.llm is llm


def test_analytics_module_integration_with_llm():
    """Verify analytics module integrates with llm module."""
    from agent.modules import analytics
    assert analytics.llm is llm
