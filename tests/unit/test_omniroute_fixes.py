"""
test_omniroute_fixes.py — Unit Tests for OmniRoute Slug Preservation, Groq Fixes & Cascade.
"""

import json
from unittest.mock import MagicMock, patch
import pytest

from agent.modules.omniroute_embedded import EmbeddedOmniRoute


def test_openrouter_preserves_frontier_model_slug():
    """Verifies that line 438 no longer appends :free to frontier paid models."""
    router = EmbeddedOmniRoute()

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "choices": [{"message": {"role": "assistant", "content": "OpenRouter response"}}]
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test"}):
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            res = router._call_openrouter_text(
                prompt="Hello",
                system_prompt=None,
                model="anthropic/claude-3.5-sonnet",
                timeout=10,
            )
            assert res["status"] == "success"
            assert res["model"] == "anthropic/claude-3.5-sonnet"
            assert ":free" not in res["model"]

            # Verify the payload sent in HTTP request
            sent_req = mock_urlopen.call_args[0][0]
            sent_payload = json.loads(sent_req.data.decode("utf-8"))
            assert sent_payload["model"] == "anthropic/claude-3.5-sonnet"


def test_groq_preserves_gpt_oss_model():
    """Verifies that line 278 does not clobber openai/gpt-oss-120b into dead llama-3.3."""
    router = EmbeddedOmniRoute()

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "choices": [{"message": {"role": "assistant", "content": "Groq response"}}]
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch.dict("os.environ", {"GROQ_API_KEY": "gsk_test"}):
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            res = router._call_groq_text(
                prompt="Hello",
                system_prompt=None,
                model="openai/gpt-oss-120b",
                timeout=10,
            )
            assert res["status"] == "success"
            assert res["model"] == "openai/gpt-oss-120b"
            assert res["model"] != "llama-3.3-70b-versatile"

            # Verify payload
            sent_req = mock_urlopen.call_args[0][0]
            sent_payload = json.loads(sent_req.data.decode("utf-8"))
            assert sent_payload["model"] == "openai/gpt-oss-120b"


def test_groq_request_headers_include_user_agent():
    """Verifies Groq requests include User-Agent: ShortsAgent-GroqRouter/1.0."""
    router = EmbeddedOmniRoute()

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({
        "choices": [{"message": {"role": "assistant", "content": "pong"}}]
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch.dict("os.environ", {"GROQ_API_KEY": "gsk_test"}):
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            router._call_groq_text("ping", None, "openai/gpt-oss-120b", 5)
            sent_req = mock_urlopen.call_args[0][0]
            assert sent_req.headers.get("User-agent") == "ShortsAgent-GroqRouter/1.0"


def test_arena_delegates_to_arena_cluster_client():
    """Verifies _call_arena_text delegates to ArenaClusterClient.get_instance().complete()."""
    router = EmbeddedOmniRoute()

    with patch("agent.modules.arena_cluster.ArenaClusterClient.get_instance") as mock_get_inst:
        mock_client = MagicMock()
        mock_client.complete.return_value = {
            "provider": "arena",
            "model": "coding-router-preview",
            "cost": 0.0,
            "text": "Arena cluster text",
            "status": "success",
        }
        mock_get_inst.return_value = mock_client

        res = router._call_arena_text(
            prompt="Hello Arena",
            system_prompt="System",
            model="coding-router-preview",
            timeout=15,
            effort="high",
        )
        assert res["status"] == "success"
        assert res["text"] == "Arena cluster text"
        mock_client.complete.assert_called_once_with(
            prompt="Hello Arena",
            system="System",
            model="coding-router-preview",
            effort="high",
            timeout=15,
        )


def test_available_routes_includes_anthropic_and_openai():
    """Verifies get_available_routes registers anthropic and openai adapters."""
    router = EmbeddedOmniRoute()
    with patch.dict("os.environ", {
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "OPENAI_API_KEY": "sk-openai-test"
    }):
        routes = router.get_available_routes()
        providers = [r["provider"] for r in routes]
        assert "anthropic" in providers
        assert "openai" in providers

        ant_route = next(r for r in routes if r["provider"] == "anthropic")
        assert ant_route["online"] is True
        assert ant_route["tier"] == "frontier_direct"

        openai_route = next(r for r in routes if r["provider"] == "openai")
        assert openai_route["online"] is True
        assert openai_route["tier"] == "frontier_direct"


def test_same_model_cascade_across_providers():
    """
    Verifies Model-Preserving Same-Model Cascade (IMRCM):
    When requesting claude-3-5-sonnet and Arena encounters 429/failure,
    it cascades to the next provider hosting the same model (e.g. OpenRouter or Anthropic).
    """
    router = EmbeddedOmniRoute()

    call_log = []

    def fake_call_arena(*args, **kwargs):
        call_log.append("arena")
        raise RuntimeError("HTTP 429 Rate limited on Arena")

    def fake_call_openrouter(*args, **kwargs):
        call_log.append("openrouter")
        return {
            "provider": "openrouter",
            "model": "anthropic/claude-3.5-sonnet",
            "cost": 0.003,
            "text": "Same-model fallback success via OpenRouter",
            "status": "success",
        }

    with patch.dict("os.environ", {
        "ARENA_API_KEY": "arena_key",
        "OPENROUTER_API_KEY": "or_key",
    }):
        with patch.object(router, "_call_arena_text", side_effect=fake_call_arena):
            with patch.object(router, "_call_openrouter_text", side_effect=fake_call_openrouter):
                res = router.complete_text(
                    prompt="Solve this problem",
                    model="claude-3-5-sonnet",
                    timeout=10,
                )
                assert res["status"] == "success"
                assert res["provider"] == "openrouter"
                assert res["canonical_model"] == "claude-3-5-sonnet"
                assert "Same-model fallback success" in res["text"]
                # Verified that Arena was attempted first, then OpenRouter
                assert call_log == ["arena", "openrouter"]
                assert len(res["attempt_history"]) == 1
                assert res["attempt_history"][0]["provider"] == "arena"
