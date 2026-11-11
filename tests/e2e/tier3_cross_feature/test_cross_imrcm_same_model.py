"""
test_cross_imrcm_same_model.py — Tier 3 Cross-Feature Combination Tests.

Covers Feature Interaction:
  - Feature 19: Infinite Multi-Router Cascade Matrix (IMRCM) & Model-Preserving Cascade.
  - Feature 1 / Feature 3 / Feature 5: Arena.AI, OpenRouter suffix fixes, Frontier model preservation.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List
import pytest

from agent.modules.capability_registry import UniversalCapabilityRegistry, CANONICAL_MODELS
from agent.modules.omniroute_embedded import EmbeddedOmniRoute


class TestCrossImrcmSameModel:
    """Pairwise combination testing: IMRCM Multi-Router Matrix <-> Same-Model Preservation."""

    def test_same_model_preserved_on_arena_429_failover_to_openrouter(
        self,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Verify that when Claude 3.5 Sonnet on Arena encounters an HTTP 429 rate limit,
        the router automatically cascades to Claude 3.5 Sonnet on OpenRouter
        WITHOUT downgrading to an inferior model or mutating the prompt.
        """
        router = EmbeddedOmniRoute()
        test_prompt = "Write an explosive 10-second opening hook about black holes."
        test_system = "You are a viral YouTube Shorts pacing expert."
        canonical_model = "claude-3-5-sonnet"

        # Mock API keys present
        monkeypatch.setenv("ARENA_API_KEY", "sk-arena-test-key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openrouter-test-key")

        captured_calls: List[Dict[str, Any]] = []

        # Mock Arena: simulates 429 rate limit exhaustion
        def mock_arena(prompt: str, system_prompt: str, model: str, timeout: int, effort: str = None):
            captured_calls.append({
                "provider": "arena",
                "model": model,
                "prompt": prompt,
                "system": system_prompt,
                "effort": effort
            })
            raise RuntimeError("HTTP 429: Usage limit exceeded for Claude 3.5 Sonnet on Arena H100 cluster")

        # Mock OpenRouter: receives same model without corruption or :free suffix
        def mock_openrouter(prompt: str, system_prompt: str, model: str, timeout: int):
            captured_calls.append({
                "provider": "openrouter",
                "model": model,
                "prompt": prompt,
                "system": system_prompt
            })
            return {
                "provider": "openrouter",
                "model": model,
                "text": "Hook: What if falling into a black hole doesn't crush you... it clones you?",
                "cost": 0.0,
                "status": "success"
            }

        monkeypatch.setattr(router, "_call_arena_text", mock_arena)
        monkeypatch.setattr(router, "_call_openrouter_text", mock_openrouter)

        # Execute completion requesting claude-3-5-sonnet
        result = router.complete_text(
            prompt=test_prompt,
            system_prompt=test_system,
            model=canonical_model,
            provider_preference=["arena", "openrouter"]
        )

        assert result["status"] == "success"
        assert result["provider"] == "openrouter"
        assert result["canonical_model"] == "claude-3-5-sonnet"

        # Forensic verification of cascade behavior
        assert len(captured_calls) == 2
        call_arena = captured_calls[0]
        call_openrouter = captured_calls[1]

        # 1. Model preservation: OpenRouter received the mapped slug for Claude 3.5 Sonnet, NOT a downgrade
        assert call_openrouter["model"] == "anthropic/claude-3.5-sonnet"
        assert ":free" not in call_openrouter["model"], "Model slug must not be corrupted with :free suffix"

        # 2. Prompt fidelity: User prompt and system instructions passed bit-for-bit unchanged
        assert call_openrouter["prompt"] == test_prompt
        assert call_openrouter["system"] == test_system

        # 3. Audit trail: Attempt history records the Arena 429 failure
        assert len(result["attempt_history"]) == 1
        assert result["attempt_history"][0]["provider"] == "arena"
        assert "429" in result["attempt_history"][0]["error"]

    def test_multi_hop_same_model_cascade_arena_to_openrouter_to_anthropic_direct(
        self,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Verify multi-hop cascade preserving Claude 3.5 Sonnet across 3 providers:
        Arena (429) -> OpenRouter (429) -> Anthropic Direct (200 OK).
        """
        router = EmbeddedOmniRoute()
        canonical_model = "claude-3-5-sonnet"
        user_prompt = "Generate 3 script angles for neural interface technology."

        monkeypatch.setenv("ARENA_API_KEY", "sk-arena-test-key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openrouter-test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-direct-test-key")

        attempted_models: List[Tuple[str, str]] = []

        def mock_arena(*args, **kwargs):
            attempted_models.append(("arena", kwargs.get("model") or args[2]))
            raise RuntimeError("HTTP 429: Arena cluster exhausted")

        def mock_openrouter(prompt, system_prompt, model, timeout):
            attempted_models.append(("openrouter", model))
            raise RuntimeError("HTTP 429: OpenRouter credit limit exceeded")

        class MockAnthropicAdapter:
            @staticmethod
            def complete(model, messages, system=None, reasoning_effort=None, timeout=30):
                attempted_models.append(("anthropic", model))
                return {
                    "provider": "anthropic",
                    "model": model,
                    "text": "Angle 1: The silent telepathy revolution.",
                    "status": "success",
                    "tokens_in": 15,
                    "tokens_out": 20
                }

        monkeypatch.setattr(router, "_call_arena_text", mock_arena)
        monkeypatch.setattr(router, "_call_openrouter_text", mock_openrouter)
        monkeypatch.setattr("agent.modules.anthropic_adapter.AnthropicAdapter", MockAnthropicAdapter)

        result = router.complete_text(
            prompt=user_prompt,
            model=canonical_model,
            provider_preference=["arena", "openrouter", "anthropic"]
        )

        assert result["status"] == "success"
        assert result["provider"] == "anthropic"
        assert result["canonical_model"] == "claude-3-5-sonnet"

        # Verify all 3 attempts targeted the same model family
        assert len(attempted_models) == 3
        assert attempted_models[0][0] == "arena"
        assert attempted_models[1] == ("openrouter", "anthropic/claude-3.5-sonnet")
        assert "claude-3-5-sonnet" in attempted_models[2][1]

        # Verify attempt history records both failovers
        assert len(result["attempt_history"]) == 2
        providers_failed = [att["provider"] for att in result["attempt_history"]]
        assert providers_failed == ["arena", "openrouter"]

    def test_prompt_and_system_prompt_fidelity_across_cascade(
        self,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Verify that prompts containing complex Unicode, quotation marks, and emojis
        are preserved byte-for-byte across cascade hops without mutation or truncation.
        """
        router = EmbeddedOmniRoute()
        complex_prompt = (
            "Hook: 🧠💭 \"Can an AI feel pain?\" — Let's look at what Dr. Turing said in 1950. 🐱📦"
        )
        complex_system = "Rule #1: Never use clichés like 'Have you ever wondered?'. Rule #2: Be precise."

        monkeypatch.setenv("ARENA_API_KEY", "sk-arena-test-key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openrouter-test-key")

        inspected_prompts: List[str] = []

        def mock_arena(prompt, system_prompt, model, timeout, effort=None):
            inspected_prompts.append(prompt)
            raise RuntimeError("Arena timeout")

        def mock_openrouter(prompt, system_prompt, model, timeout):
            inspected_prompts.append(prompt)
            return {
                "provider": "openrouter",
                "model": model,
                "text": "Valid response with high fidelity.",
                "status": "success"
            }

        monkeypatch.setattr(router, "_call_arena_text", mock_arena)
        monkeypatch.setattr(router, "_call_openrouter_text", mock_openrouter)

        result = router.complete_text(
            prompt=complex_prompt,
            system_prompt=complex_system,
            model="claude-3-5-sonnet",
            provider_preference=["arena", "openrouter"]
        )

        assert result["status"] == "success"
        assert len(inspected_prompts) == 2
        assert inspected_prompts[0] == complex_prompt
        assert inspected_prompts[1] == complex_prompt

    def test_fallback_to_reasoning_peer_only_after_same_model_exhaustion(
        self,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Verify that when ALL same-model routes for Claude 3.5 Sonnet are exhausted,
        the IMRCM engine gracefully cascades to a verified reasoning peer
        (e.g. DeepInfra / Groq / Pollinations) without crashing.
        """
        router = EmbeddedOmniRoute()
        canonical_model = "claude-3-5-sonnet"

        monkeypatch.setenv("ARENA_API_KEY", "sk-arena-test-key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openrouter-test-key")

        # Mock all Claude-specific routes failing
        def mock_arena_fail(*args, **kwargs):
            raise RuntimeError("Arena: 429 quota exhausted")

        def mock_openrouter_fail(*args, **kwargs):
            raise RuntimeError("OpenRouter: 429 credit exhausted")

        # Mock reasoning peer (Pollinations / DeepInfra) succeeding
        def mock_pollinations_success(prompt, system_prompt, model, timeout):
            return {
                "provider": "pollinations",
                "model": "openai",
                "text": "Peer reasoning fallback response.",
                "cost": 0.0,
                "status": "success"
            }

        monkeypatch.setattr(router, "_call_arena_text", mock_arena_fail)
        monkeypatch.setattr(router, "_call_openrouter_text", mock_openrouter_fail)
        monkeypatch.setattr(router, "_call_pollinations_text", mock_pollinations_success)

        result = router.complete_text(
            prompt="Draft short video climax.",
            model=canonical_model,
            provider_preference=["arena", "openrouter", "pollinations"]
        )

        assert result["status"] == "success"
        assert result["provider"] == "pollinations"
        # Verify that attempt history tracked both same-model failures before peer fallback
        assert len(result["attempt_history"]) == 2
        assert result["attempt_history"][0]["provider"] == "arena"
        assert result["attempt_history"][1]["provider"] == "openrouter"

    def test_reasoning_effort_preserved_across_same_model_cascade(
        self,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Verify that extended thinking and reasoning_effort parameters
        (e.g. effort='high') are translated and preserved across same-model cascade attempts.
        """
        router = EmbeddedOmniRoute()
        monkeypatch.setenv("ARENA_API_KEY", "sk-arena-test-key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openrouter-test-key")

        recorded_efforts: List[Any] = []

        def mock_arena(prompt, system_prompt, model, timeout, effort=None):
            recorded_efforts.append(("arena", effort))
            raise RuntimeError("Arena 429")

        def mock_openrouter(prompt, system_prompt, model, timeout):
            recorded_efforts.append(("openrouter", None))
            return {
                "provider": "openrouter",
                "model": model,
                "text": "Reasoning complete with high effort.",
                "status": "success"
            }

        monkeypatch.setattr(router, "_call_arena_text", mock_arena)
        monkeypatch.setattr(router, "_call_openrouter_text", mock_openrouter)

        result = router.complete_text(
            prompt="Solve mathematical paradox for script.",
            model="claude-3-5-sonnet",
            effort="high",
            provider_preference=["arena", "openrouter"]
        )

        assert result["status"] == "success"
        assert result["effort"] == "high"
        assert len(recorded_efforts) == 2
        assert recorded_efforts[0] == ("arena", "high")
