"""
test_llm_integration.py — Unit tests for the multi-provider LLM fallback chain.
"""

from unittest.mock import MagicMock, patch
import pytest

from agent.modules import llm


def test_pollinations_fallback_when_no_keys_configured(monkeypatch):
    # Ensure all provider keys are unset
    for k in ["GEMINI_API_KEY", "OMNIROUTE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "DEEPINFRA_API_KEY"]:
        monkeypatch.delenv(k, raising=False)

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "Generated text via Pollinations fallback"
        mock_post.return_value = mock_resp

        result = llm.complete("Generate hook", system="You are a shorts creator")
        assert result == "Generated text via Pollinations fallback"
        assert mock_post.called
        assert "text.pollinations.ai" in mock_post.call_args[0][0]


def test_omniroute_priority(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-omniroute-token")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Generated text via local OmniRoute port 20128"}}]
        }
        mock_post.return_value = mock_resp

        result = llm.complete("Prompt for omniroute")
        assert result == "Generated text via local OmniRoute port 20128"
        assert "localhost:20128" in mock_post.call_args[0][0]


def test_complete_json_fallback_with_pollinations(monkeypatch):
    for k in ["GEMINI_API_KEY", "OMNIROUTE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "DEEPINFRA_API_KEY"]:
        monkeypatch.delenv(k, raising=False)

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '```json\n{"hook": "Did you know?", "retention_score": 9.5}\n```'
        mock_post.return_value = mock_resp

        data = llm.complete_json("Generate JSON hook")
        assert isinstance(data, dict)
        assert data["hook"] == "Did you know?"
        assert data["retention_score"] == 9.5
