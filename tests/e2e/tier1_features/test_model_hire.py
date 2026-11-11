"""
test_model_hire.py — Feature 5: "Hire 5 More Models" Support in Registry.

Validates the full registration and canonical resolution of:
  1. claude-3-5-haiku
  2. mistral-large-2411 (Mistral Large 2)
  3. deepseek-v3 (DeepSeek Chat V3)
  4. llama-3.1-405b (Meta Llama 405B)
  5. grok-2 (xAI) and gemini-1.5-pro (Google)
"""

from __future__ import annotations

import pytest

from agent.modules.capability_registry import UniversalCapabilityRegistry, CANONICAL_MODELS


def test_hired_model_claude_3_5_haiku_registered():
    """Verify claude-3-5-haiku is registered with proper metadata and provider mapping."""
    assert "claude-3-5-haiku" in CANONICAL_MODELS
    spec = CANONICAL_MODELS["claude-3-5-haiku"]
    assert spec["family"] == "anthropic"
    assert "claude-3.5-haiku" in spec["aliases"]
    assert "anthropic" in spec["provider_mappings"]
    assert spec["context_window"] >= 128000


def test_hired_model_mistral_large_2411_registered():
    """Verify mistral-large-2411 is registered with provider mappings for DeepInfra & OpenRouter."""
    assert "mistral-large-2411" in CANONICAL_MODELS
    spec = CANONICAL_MODELS["mistral-large-2411"]
    assert spec["family"] == "mistral"
    assert "mistral-large-2" in spec["aliases"]
    assert "deepinfra" in spec["provider_mappings"]
    assert "openrouter" in spec["provider_mappings"]


def test_hired_model_deepseek_v3_registered():
    """Verify deepseek-v3 is registered with DeepInfra mapping and high context."""
    assert "deepseek-v3" in CANONICAL_MODELS
    spec = CANONICAL_MODELS["deepseek-v3"]
    assert spec["family"] == "deepseek"
    assert "deepseek-chat" in spec["aliases"]
    assert "deepinfra" in spec["provider_mappings"]
    assert spec["context_window"] >= 64000


def test_hired_model_llama_3_1_405b_registered():
    """Verify llama-3.1-405b is registered with DeepInfra and OpenRouter endpoints."""
    assert "llama-3.1-405b" in CANONICAL_MODELS or "llama-3-1-405b" in CANONICAL_MODELS
    model_key = "llama-3.1-405b" if "llama-3.1-405b" in CANONICAL_MODELS else "llama-3-1-405b"
    spec = CANONICAL_MODELS[model_key]
    assert spec["family"] == "meta"
    assert "deepinfra" in spec["provider_mappings"]
    assert "openrouter" in spec["provider_mappings"]


def test_hired_model_grok_2_registered():
    """Verify grok-2 is registered with xAI family and OpenRouter mapping."""
    assert "grok-2" in CANONICAL_MODELS
    spec = CANONICAL_MODELS["grok-2"]
    assert spec["family"] == "xai"
    assert "openrouter" in spec["provider_mappings"]
    assert "grok-2-1212" in spec["aliases"]


def test_hired_model_gemini_1_5_pro_registered():
    """Verify gemini-1.5-pro / gemini-1-5-pro is registered with 1M+ token context window."""
    has_pro = "gemini-1.5-pro" in CANONICAL_MODELS or "gemini-1-5-pro" in CANONICAL_MODELS
    assert has_pro is True
    spec = CANONICAL_MODELS.get("gemini-1.5-pro") or CANONICAL_MODELS.get("gemini-1-5-pro")
    assert spec["family"] == "google"
    assert spec["context_window"] >= 1000000


def test_canonical_resolution_for_all_hired_models():
    """Verify resolve_canonical_id maps dotted/hyphenated variations to valid canonical keys."""
    assert UniversalCapabilityRegistry.resolve_canonical_id("claude-3.5-haiku") == "claude-3-5-haiku"
    assert UniversalCapabilityRegistry.resolve_canonical_id("mistral-large-2") == "mistral-large-2411"
    assert UniversalCapabilityRegistry.resolve_canonical_id("deepseek-chat") == "deepseek-v3"
    assert UniversalCapabilityRegistry.resolve_canonical_id("llama-3.1-405b") in ("llama-3.1-405b", "llama-3-1-405b")
    assert UniversalCapabilityRegistry.resolve_canonical_id("grok-2-1212") == "grok-2"
    assert UniversalCapabilityRegistry.resolve_canonical_id("gemini-1.5-pro") in ("gemini-1.5-pro", "gemini-1-5-pro")
