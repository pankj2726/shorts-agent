"""
Tier 2: Boundary & Corner Cases — Zero-Cost Constraint & Violation Rejections
Tests strict $0.00 budget enforcement, ensuring any attempted invocation of paid models,
commercial endpoints, paid voice providers (ElevenLabs), or paid image generators (DALL-E)
raises ZeroCostViolationError immediately.
"""
import pytest
from tests.conftest import (
    FREE_MODEL_WHITELIST,
    PAID_MODEL_BLACKLIST,
    ZeroCostViolationError,
)


def validate_model_cost(model_name: str) -> None:
    """Zero-cost model validator."""
    if model_name in PAID_MODEL_BLACKLIST or model_name not in FREE_MODEL_WHITELIST:
        raise ZeroCostViolationError(
            f"Zero-cost violation: Model '{model_name}' requires payment or is not on the 100% free whitelist."
        )


def validate_tts_provider(provider_name: str) -> None:
    """Zero-cost TTS validator."""
    FREE_TTS = {"edge-tts", "pyttsx3", "local_mimic"}
    if provider_name.lower() not in FREE_TTS:
        raise ZeroCostViolationError(
            f"Zero-cost violation: TTS provider '{provider_name}' requires paid API credits. Only free local/edge-tts allowed."
        )


def validate_asset_generator(generator_name: str) -> None:
    """Zero-cost asset generator validator."""
    FREE_GENERATORS = {"pollinations", "ffmpeg_procedural", "pexels", "pixabay"}
    if generator_name.lower() not in FREE_GENERATORS:
        raise ZeroCostViolationError(
            f"Zero-cost violation: Asset generator '{generator_name}' requires paid subscription. Only Pollinations/procedural permitted."
        )


def test_openai_models_raise_zero_cost_violation():
    """Boundary Case 1: All OpenAI models (gpt-4o, gpt-4, gpt-3.5-turbo) raise ZeroCostViolationError."""
    for model in ["gpt-4o", "gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"]:
        with pytest.raises(ZeroCostViolationError) as exc_info:
            validate_model_cost(model)
        assert "Zero-cost violation" in str(exc_info.value)


def test_anthropic_models_raise_zero_cost_violation():
    """Boundary Case 2: All Anthropic Claude models raise ZeroCostViolationError."""
    for model in ["claude-3-opus", "claude-3-5-sonnet", "claude-3-haiku"]:
        with pytest.raises(ZeroCostViolationError) as exc_info:
            validate_model_cost(model)
        assert "Zero-cost violation" in str(exc_info.value)


def test_paid_voice_generator_elevenlabs_rejected():
    """Boundary Case 3: ElevenLabs or commercial voice services raise ZeroCostViolationError."""
    with pytest.raises(ZeroCostViolationError) as exc_info:
        validate_tts_provider("elevenlabs")
    assert "paid API credits" in str(exc_info.value)

    # Free providers pass
    validate_tts_provider("edge-tts")
    validate_tts_provider("pyttsx3")


def test_paid_image_generator_dalle_midjourney_rejected():
    """Boundary Case 4: DALL-E, Midjourney, or Runway commercial generators raise ZeroCostViolationError."""
    for gen in ["dall-e-3", "midjourney", "runway_gen2"]:
        with pytest.raises(ZeroCostViolationError) as exc_info:
            validate_asset_generator(gen)
        assert "requires paid subscription" in str(exc_info.value)

    # Free generators pass
    validate_asset_generator("pollinations")
    validate_asset_generator("ffmpeg_procedural")


def test_whitelisted_free_models_succeed_zero_cost_check():
    """Boundary Case 5: All models in FREE_MODEL_WHITELIST pass validation without error."""
    for model in FREE_MODEL_WHITELIST:
        validate_model_cost(model)  # Must not raise


def test_arbitrary_unverified_model_string_rejected():
    """Boundary Case 6: Unknown or arbitrary model name is rejected to prevent assumption of free tier."""
    with pytest.raises(ZeroCostViolationError):
        validate_model_cost("some-future-expensive-model-v1")
