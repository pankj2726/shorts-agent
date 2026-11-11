"""
test_omniroute_embedded.py — Tests for the in-project embedded OmniRoute router.
"""

from unittest.mock import MagicMock, patch
import pytest

from agent.modules.omniroute_embedded import EmbeddedOmniRoute


def test_embedded_routes_discovery():
    router = EmbeddedOmniRoute()
    routes = router.get_available_routes()
    assert len(routes) >= 4
    
    # Pollinations must always be online and cost 0.0
    pol = next(r for r in routes if r["provider"] == "pollinations")
    assert pol["online"] is True
    assert pol["cost_per_token"] == 0.0
    assert pol["requires_key"] is False


@patch("urllib.request.urlopen")
def test_pollinations_completion(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"Hello from embedded Pollinations AI"
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    router = EmbeddedOmniRoute()
    result = router.complete_text(
        prompt="Write a hook",
        system_prompt="You are a shorts hook writer",
        model="openai/gpt-5.4-nano",
        provider_preference=["pollinations"]
    )
    assert result["status"] == "success"
    assert result["provider"] == "pollinations"
    assert result["cost"] == 0.0
    assert "Hello from embedded" in result["text"]


@patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-test-key"})
@patch("urllib.request.urlopen")
def test_openrouter_fallback(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"choices":[{"message":{"content":"Generated via OpenRouter"}}]}'
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    router = EmbeddedOmniRoute()
    result = router.complete_text(
        prompt="Test prompt",
        provider_preference=["openrouter"]
    )
    assert result["status"] == "success"
    assert result["provider"] == "openrouter"
    assert result["text"] == "Generated via OpenRouter"
