"""
test_tester_router.py — Unit tests for Tester Router Federation & Free Model Federation.
"""

import tempfile
from pathlib import Path
from agent.modules.tester_router import WorkforceRouterFederation, FREE_ROUTER_CATALOG


def test_catalog_coverage():
    assert "google-gemini" in FREE_ROUTER_CATALOG
    assert "openrouter" in FREE_ROUTER_CATALOG
    assert "groq" in FREE_ROUTER_CATALOG
    assert "cerebras" in FREE_ROUTER_CATALOG
    assert "cloudflare-workers-ai" in FREE_ROUTER_CATALOG
    assert "local-edge-tts" in FREE_ROUTER_CATALOG


def test_register_and_reserve():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        fed = WorkforceRouterFederation(state_file=state_file)

        # Register Tester A for Groq
        acc_a = fed.register_tester(
            user_id="user_alice",
            email="alice@company.com",
            provider="groq",
            sharing_mode="delegated",
        )
        assert acc_a.email == "alice@company.com"
        assert acc_a.daily_request_limit == 14400

        # Register Tester B for OpenRouter
        acc_b = fed.register_tester(
            user_id="user_bob",
            email="bob@company.com",
            provider="openrouter",
            sharing_mode="delegated",
        )
        assert acc_b.email == "bob@company.com"
        assert acc_b.daily_request_limit == 50

        # Atomic reservation for Alice
        res = fed.reserve_capacity(task_modality="text", user_id="user_alice")
        assert res is not None
        assert res.requests_reserved == 1
        assert res.status == "active"

        # Commit reservation
        fed.commit_reservation(res.reservation_id, requests_consumed=1)
        assert fed.reservations[res.reservation_id].status == "committed"

        # Verify catalog shows combined capacity
        cat = fed.get_federated_catalog()
        assert cat["active_providers_count"] >= 2
        assert cat["total_free_models_available"] >= 5
        assert cat["budget_constraint"] == "$0.00 (Zero Expenditure Enforced)"


def test_isolation_owner_only():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        fed = WorkforceRouterFederation(state_file=state_file)

        # Charlie connects personal account with owner_only
        fed.register_tester(
            user_id="user_charlie",
            email="charlie@company.com",
            provider="cerebras",
            sharing_mode="owner_only",
        )

        # Dave requests capacity -> Charlie's account must NOT be used
        res = fed.reserve_capacity(task_modality="text", user_id="user_dave")
        assert res is None  # no eligible shared accounts for Dave

        # Charlie requests capacity -> Charlie's account is used
        res_charlie = fed.reserve_capacity(task_modality="text", user_id="user_charlie")
        assert res_charlie is not None
        assert res_charlie.provider == "cerebras"
        fed.release_reservation(res_charlie.reservation_id)
        assert fed.reservations[res_charlie.reservation_id].status == "released"
