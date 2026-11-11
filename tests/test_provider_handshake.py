"""
test_provider_handshake.py — Unit tests for Directive 6.1 Provider Handshake Engine.
"""

import os
from agent.modules.provider_handshake import ProviderHandshakeEngine, PROVIDER_SPECS


def test_specs_coverage():
    """All 10 required providers are defined in specifications."""
    expected_providers = [
        "omniroute",
        "openrouter",
        "agentrouter",
        "agnes",
        "vertex_ai",
        "pollinations",
        "deepinfra",
        "anyapi",
        "fal_ai",
        "opencode",
    ]
    for p in expected_providers:
        assert p in PROVIDER_SPECS, f"Provider {p} missing from PROVIDER_SPECS"


def test_unconfigured_status():
    """Providers without environment keys return UNCONFIGURED safely."""
    # Ensure a random key is definitely unset
    key_name = PROVIDER_SPECS["openrouter"]["secret_name"]
    old_val = os.environ.pop(key_name, None)
    try:
        status = ProviderHandshakeEngine.check_credential_status("openrouter")
        assert status["configured"] is False
        assert status["secret_name"] == "OPENROUTER_API_KEY"

        handshake = ProviderHandshakeEngine.execute_handshake("openrouter")
        assert handshake["status"] == "UNCONFIGURED"
    finally:
        if old_val is not None:
            os.environ[key_name] = old_val


def test_pollinations_public_tier_fallback():
    """Pollinations AI succeeds even with zero key configured."""
    key_name = PROVIDER_SPECS["pollinations"]["secret_name"]
    old_val = os.environ.pop(key_name, None)
    try:
        handshake = ProviderHandshakeEngine.execute_handshake("pollinations")
        assert handshake["status"] == "PASSED"
        assert handshake.get("mode") == "free_public_tier"
    finally:
        if old_val is not None:
            os.environ[key_name] = old_val


def test_zero_secrets_leaked_in_results():
    """Check that result dicts contain zero raw secret tokens."""
    res = ProviderHandshakeEngine.run_all_handshakes()
    serialized = str(res)
    assert "sk-" not in serialized
    assert "key-" not in serialized
    for r in res["results"]:
        # Only status, display_name, provider_id, message, and status_code are exposed
        assert set(r.keys()).issubset({"provider_id", "display_name", "status", "message", "status_code", "mode"})
