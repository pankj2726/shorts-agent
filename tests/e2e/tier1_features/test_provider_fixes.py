"""
test_provider_fixes.py — Features 3 & 4: OpenRouter Suffix Fix & Groq Translation Correction.

Feature 3 (OpenRouter):
  - Removal of blind :free suffixing corrupting frontier models.
  - Proper mapping of canonical frontier models in UniversalCapabilityRegistry.
  - Preservation of explicit free model slugs.

Feature 4 (Groq):
  - Correction of model translation clobbering valid models into dead slugs.
  - Accurate mapping of GPT / nano aliases to active versatile models.
  - Proper Groq router User-Agent propagation.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from agent.modules.capability_registry import UniversalCapabilityRegistry, CANONICAL_MODELS
from agent.modules.omniroute_embedded import EmbeddedOmniRoute


# ─── Feature 3: OpenRouter Suffix Fix Tests (≥5 tests) ───────────────────────

def test_openrouter_preserves_explicit_free_suffix():
    """Verify models already containing a colon or suffix (e.g. :free) are preserved intact."""
    router = EmbeddedOmniRoute()
    model_with_suffix = "deepseek/deepseek-r1:free"
    with patch("urllib.request.urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
        mock_url.return_value = mock_resp

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test-key"}):
            res = router._call_openrouter_text(prompt="hi", system_prompt=None, model=model_with_suffix, timeout=5)
            assert res["model"] == "deepseek/deepseek-r1:free"


def test_openrouter_frontier_model_mapping():
    """Verify claude-3-5-sonnet maps to anthropic/claude-3.5-sonnet without invalid :free slug."""
    p_params = UniversalCapabilityRegistry.translate_parameters("claude-3-5-sonnet", "openrouter")
    assert p_params["model"] == "anthropic/claude-3.5-sonnet"
    assert ":free" not in p_params["model"]


def test_openrouter_deepseek_v3_mapping():
    """Verify deepseek-v3 translates to clean OpenRouter model slug."""
    p_params = UniversalCapabilityRegistry.translate_parameters("deepseek-v3", "openrouter")
    assert "deepseek" in p_params["model"]
    assert not p_params["model"].endswith(":free:free")


def test_openrouter_mistral_large_mapping():
    """Verify mistral-large-2411 maps to mistralai/mistral-large-2411 without corruption."""
    p_params = UniversalCapabilityRegistry.translate_parameters("mistral-large-2411", "openrouter")
    assert p_params["model"] == "mistralai/mistral-large-2411"
    assert not p_params["model"].endswith(":free")


def test_openrouter_grok_2_mapping():
    """Verify grok-2 maps to x-ai/grok-2-1212 on OpenRouter."""
    p_params = UniversalCapabilityRegistry.translate_parameters("grok-2", "openrouter")
    assert p_params["model"] == "x-ai/grok-2-1212"
    assert not p_params["model"].endswith(":free")


# ─── Feature 4: Groq Model Translation Correction Tests (≥5 tests) ───────────

def test_groq_preserves_valid_llama_model():
    """Verify valid llama model is preserved and not clobbered."""
    router = EmbeddedOmniRoute()
    valid_model = "llama-3.3-70b-versatile"

    with patch("urllib.request.urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = json.dumps({"choices": [{"message": {"content": "groq reply"}}]}).encode("utf-8")
        mock_url.return_value = mock_resp

        with patch.dict("os.environ", {"GROQ_API_KEY": "gsk-test-key"}):
            res = router._call_groq_text(prompt="hello", system_prompt=None, model=valid_model, timeout=5)
            assert res["model"] == "llama-3.3-70b-versatile"
            assert res["status"] == "success"


def test_groq_preserves_specified_gpt_model():
    """Verify specific models are preserved and not blindly clobbered into a single static model."""
    router = EmbeddedOmniRoute()

    with patch("urllib.request.urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
        mock_url.return_value = mock_resp

        with patch.dict("os.environ", {"GROQ_API_KEY": "gsk-test-key"}):
            res = router._call_groq_text(prompt="hello", system_prompt=None, model="gpt-4o", timeout=5)
            assert res["model"] == "gpt-4o"


def test_groq_translates_nano_model():
    """Verify nano models fall back to active Groq flagship model openai/gpt-oss-120b."""
    router = EmbeddedOmniRoute()

    with patch("urllib.request.urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
        mock_url.return_value = mock_resp

        with patch.dict("os.environ", {"GROQ_API_KEY": "gsk-test-key"}):
            res = router._call_groq_text(prompt="hello", system_prompt=None, model="gpt-5.4-nano", timeout=5)
            assert res["model"] == "openai/gpt-oss-120b"


def test_groq_registry_translation_llama():
    """Verify UniversalCapabilityRegistry maps llama-3-3-70b to Groq versatile or high-speed model."""
    p_params = UniversalCapabilityRegistry.translate_parameters("llama-3-3-70b", "groq")
    assert p_params["model"] in ("openai/gpt-oss-120b", "llama-3.3-70b-versatile")


def test_groq_user_agent_and_headers():
    """Verify Groq API requests include proper authorization and User-Agent headers."""
    router = EmbeddedOmniRoute()
    captured_req = None

    def mock_urlopen(req, timeout=30):
        nonlocal captured_req
        captured_req = req
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        with patch.dict("os.environ", {"GROQ_API_KEY": "gsk-test-secret"}):
            router._call_groq_text("test", None, "llama-3.3-70b-versatile", timeout=5)

    assert captured_req is not None
    assert captured_req.headers["Authorization"] == "Bearer gsk-test-secret"
    assert captured_req.headers["User-agent"] == "ShortsAgent-GroqRouter/1.0"
