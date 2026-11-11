"""
test_omniroute_failover.py — Automated Unit Tests for Adaptive 429 Failover Routing.
"""

from unittest.mock import patch, MagicMock
import pytest
from agent.modules.omniroute_embedded import EmbeddedOmniRoute


def test_omniroute_failover_success_on_second_tier():
    """Verify that when primary provider hits 429, router automatically cascades to tier 2."""
    router = EmbeddedOmniRoute()

    # Mock provider 1 failing with 429 and provider 2 succeeding
    def mock_pollinations(*args, **kwargs):
        raise RuntimeError("HTTP 429: Too Many Requests / Rate Limit Exceeded")

    def mock_openrouter(*args, **kwargs):
        return {
            "provider": "openrouter",
            "model": "deepseek/deepseek-r1:free",
            "cost": 0.0,
            "text": "Fallback generation successful.",
            "status": "success",
        }

    with patch.object(router, "_call_pollinations_text", side_effect=mock_pollinations), \
         patch.object(router, "_call_openrouter_text", side_effect=mock_openrouter), \
         patch.dict("os.environ", {"OPENROUTER_API_KEY": "test_mock_key"}):

        res = router.complete_text(
            prompt="Test prompt",
            provider_preference=["pollinations", "openrouter", "groq"]
        )

        assert res["status"] == "success"
        assert res["provider"] == "openrouter"
        assert res["text"] == "Fallback generation successful."
        assert len(res["attempt_history"]) == 1
        assert res["attempt_history"][0]["provider"] == "pollinations"
        assert "429" in res["attempt_history"][0]["error"]


def test_omniroute_arena_routing():
    """Verify Arena.AI router succeeds when requested or cascaded."""
    router = EmbeddedOmniRoute()

    def mock_arena(*args, **kwargs):
        return {
            "provider": "arena",
            "model": "coding-router-preview",
            "cost": 0.0,
            "text": "Arena frontier response.",
            "status": "success",
        }

    with patch.object(router, "_call_arena_text", side_effect=mock_arena), \
         patch.dict("os.environ", {"ARENA_API_KEY": "test_arena_key"}):

        res = router.complete_text(
            prompt="Complex task",
            provider_preference=["arena"]
        )

        assert res["status"] == "success"
        assert res["provider"] == "arena"
        assert res["text"] == "Arena frontier response."
