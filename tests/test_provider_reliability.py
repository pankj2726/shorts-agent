"""
Provider reliability: error taxonomy, runtime health, circuit breakers, budget.

Every test here is deterministic. Health probes, provider responses and prices
are all supplied by the test; nothing reaches a provider. Where the blueprint
asks for "real provider tests", those are called out separately in the report
because they cannot run in an environment with no outbound access.
"""
from __future__ import annotations

import json
import threading

import pytest
import requests

from agent.modules import budget, circuit_breaker, provider_errors, provider_health
from agent.modules.provider_errors import (
    AuthenticationError,
    BillingError,
    ConnectionFailureError,
    InvalidRequestError,
    MalformedResponseError,
    QuotaExhaustedError,
    RateLimitError,
    RequestTimeoutError,
    ServerError,
    UnsupportedModelError,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Keep persisted state out of the repo and out of other tests."""
    monkeypatch.setenv(provider_health.STATE_PATH_ENV,
                       str(tmp_path / "health.json"))
    monkeypatch.setenv(budget.STATE_PATH_ENV, str(tmp_path / "budget.json"))
    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


# ─── Error taxonomy: HTTP ────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_are_classified_and_not_retryable(status):
    err = provider_errors.classify_http("groq", status, "invalid api key")
    assert isinstance(err, AuthenticationError)
    assert err.retryable is False
    assert err.status_code == status


def test_authentication_is_never_a_timeout():
    """The specific confusion this taxonomy exists to prevent."""
    err = provider_errors.classify_http("groq", 401, "unauthorized")
    assert not isinstance(err, RequestTimeoutError)
    assert provider_errors.is_retryable(err) is False


def test_billing_failure():
    err = provider_errors.classify_http("openrouter", 402, "payment required")
    assert isinstance(err, BillingError)
    assert err.retryable is False


def test_rate_limit_is_retryable():
    err = provider_errors.classify_http("groq", 429, "rate limit reached")
    assert isinstance(err, RateLimitError)
    assert err.retryable is True


def test_quota_exhaustion_is_not_a_rate_limit():
    """Waiting seconds will not restore a daily allowance."""
    err = provider_errors.classify_http(
        "google-gemini", 429, "You exceeded your current quota")
    assert isinstance(err, QuotaExhaustedError)
    assert err.retryable is False


def test_billing_markers_win_over_quota_markers():
    err = provider_errors.classify_http(
        "openrouter", 429, "insufficient credit for this quota period")
    assert isinstance(err, BillingError)


@pytest.mark.parametrize("status", [400, 422])
def test_invalid_request(status):
    err = provider_errors.classify_http("groq", status, "temperature out of range")
    assert isinstance(err, InvalidRequestError)
    assert err.retryable is False


def test_bad_request_naming_an_unknown_model():
    err = provider_errors.classify_http("groq", 400, "model_not_found: acme/x")
    assert isinstance(err, UnsupportedModelError)


def test_not_found_is_an_unsupported_model():
    err = provider_errors.classify_http("google-gemini", 404, "")
    assert isinstance(err, UnsupportedModelError)


@pytest.mark.parametrize("status", [500, 502, 503])
def test_server_errors_are_retryable(status):
    err = provider_errors.classify_http("groq", status, "upstream failure")
    assert isinstance(err, ServerError)
    assert err.retryable is True


def test_unrecognised_status_is_not_guessed_as_transient():
    err = provider_errors.classify_http("groq", 418, "teapot")
    assert err.retryable is False


# ─── Error taxonomy: transport and parsing ───────────────────────────────────

def test_timeout_is_retryable():
    err = provider_errors.classify_exception(
        "groq", requests.exceptions.Timeout("read timed out"))
    assert isinstance(err, RequestTimeoutError)
    assert err.retryable is True


def test_connection_failure_is_retryable():
    err = provider_errors.classify_exception(
        "groq", requests.exceptions.ConnectionError("name resolution failed"))
    assert isinstance(err, ConnectionFailureError)
    assert err.retryable is True


def test_http_error_carries_its_status_through_classification():
    resp = requests.Response()
    resp.status_code = 401
    resp._content = b"invalid key"
    err = provider_errors.classify_exception(
        "groq", requests.exceptions.HTTPError(response=resp))
    assert isinstance(err, AuthenticationError)


def test_parse_failure_is_malformed_response():
    err = provider_errors.classify_exception("groq", KeyError("choices"))
    assert isinstance(err, MalformedResponseError)
    assert err.retryable is False


def test_unknown_exception_is_not_retried():
    """An unclassified exception is more likely a bug than a transient fault."""
    assert provider_errors.is_retryable(RuntimeError("unexpected")) is False


def test_classified_errors_pass_through_unchanged():
    original = RateLimitError("groq", "slow down")
    assert provider_errors.classify_exception("groq", original) is original


# ─── No credential leakage ───────────────────────────────────────────────────

def test_credentials_are_redacted_from_error_detail():
    err = provider_errors.classify_http(
        "groq", 401, "Authorization: Bearer sk-live-abcdef1234567890 failed")
    assert "sk-live-abcdef1234567890" not in err.detail
    assert "sk-live-abcdef1234567890" not in str(err)
    assert "[redacted]" in err.detail


def test_long_bodies_are_truncated():
    err = provider_errors.classify_http("groq", 500, "x" * 5000)
    assert len(err.detail) <= provider_errors.MAX_DETAIL_CHARS + 3


def test_as_dict_carries_no_secret():
    err = provider_errors.classify_http(
        "groq", 401, "api_key=SUPERSECRETVALUE12345678901234567890")
    assert "SUPERSECRET" not in json.dumps(err.as_dict())


# ─── Runtime health ──────────────────────────────────────────────────────────

def test_no_observation_is_unknown_and_does_not_block():
    assert provider_health.status("groq")["state"] == provider_health.UNKNOWN
    available, reason = provider_health.is_available("groq")
    assert available is True
    assert "unknown" in reason


def test_failures_degrade_then_disable(monkeypatch):
    monkeypatch.setenv(provider_health.DEGRADED_THRESHOLD_ENV, "2")
    monkeypatch.setenv(provider_health.UNHEALTHY_THRESHOLD_ENV, "3")

    provider_health.record_failure("groq", "timeout")
    assert provider_health.status("groq")["state"] == provider_health.HEALTHY

    provider_health.record_failure("groq", "timeout")
    assert provider_health.status("groq")["state"] == provider_health.DEGRADED
    assert provider_health.is_available("groq")[0] is True

    provider_health.record_failure("groq", "timeout")
    assert provider_health.status("groq")["state"] == provider_health.UNHEALTHY
    assert provider_health.is_available("groq")[0] is False


def test_success_recovers_a_failing_provider(monkeypatch):
    monkeypatch.setenv(provider_health.UNHEALTHY_THRESHOLD_ENV, "2")
    provider_health.record_failure("groq", "timeout")
    provider_health.record_failure("groq", "timeout")
    assert provider_health.status("groq")["state"] == provider_health.UNHEALTHY

    provider_health.record_success("groq")
    assert provider_health.status("groq")["state"] == provider_health.HEALTHY
    assert provider_health.is_available("groq")[0] is True


def test_expired_observation_becomes_unknown(monkeypatch):
    monkeypatch.setenv(provider_health.TTL_ENV, "60")
    provider_health.record_failure("groq", "timeout")

    now = __import__("time").time() + 3600
    info = provider_health.status("groq", now=now)
    assert info["state"] == provider_health.UNKNOWN
    assert "expired" in info["reason"]


def test_corrupt_state_file_does_not_break_routing(tmp_path, monkeypatch):
    path = tmp_path / "health.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv(provider_health.STATE_PATH_ENV, str(path))

    assert provider_health.status("groq")["state"] == provider_health.UNKNOWN
    assert provider_health.is_available("groq")[0] is True


@pytest.mark.parametrize("value", ["0", "-5", "abc", ""])
def test_bad_ttl_falls_back_to_the_default(monkeypatch, value):
    monkeypatch.setenv(provider_health.TTL_ENV, value)
    assert provider_health.ttl_s() == provider_health.DEFAULT_TTL_S


def test_registry_health_is_separate_from_runtime_health():
    """A registry claim of 'ok' must not be reported as verified at runtime."""
    assert provider_health.status("groq")["state"] == provider_health.UNKNOWN


# ─── Circuit breakers ────────────────────────────────────────────────────────

def test_breaker_opens_after_the_threshold(monkeypatch):
    monkeypatch.setenv(circuit_breaker.THRESHOLD_ENV, "3")
    breaker = circuit_breaker.CircuitBreaker("groq")

    for _ in range(2):
        breaker.record_failure("timeout")
        assert breaker.state == circuit_breaker.CLOSED
    breaker.record_failure("timeout")
    assert breaker.state == circuit_breaker.OPEN
    assert breaker.allow()[0] is False


def test_non_retryable_configuration_failure_opens_immediately():
    breaker = circuit_breaker.CircuitBreaker("groq", threshold=10)
    breaker.record_failure("authentication")
    assert breaker.state == circuit_breaker.OPEN


def test_half_open_after_cooldown(monkeypatch):
    breaker = circuit_breaker.CircuitBreaker("groq", threshold=1, cooldown_s=0.05)
    breaker.record_failure("timeout")
    assert breaker.allow()[0] is False

    __import__("time").sleep(0.06)
    allowed, reason = breaker.allow()
    assert allowed is True
    assert "half-open" in reason


def test_half_open_admits_only_the_configured_probes():
    breaker = circuit_breaker.CircuitBreaker(
        "groq", threshold=1, cooldown_s=0.01, half_open_probes=2)
    breaker.record_failure("timeout")
    __import__("time").sleep(0.02)

    assert breaker.allow()[0] is True
    assert breaker.allow()[0] is True
    allowed, reason = breaker.allow()
    assert allowed is False
    assert "probes in flight" in reason


def test_successful_probe_closes_the_breaker():
    breaker = circuit_breaker.CircuitBreaker("groq", threshold=1, cooldown_s=0.01)
    breaker.record_failure("timeout")
    __import__("time").sleep(0.02)
    breaker.allow()
    breaker.record_success()
    assert breaker.state == circuit_breaker.CLOSED
    assert breaker.failures == 0


def test_failed_probe_reopens_the_breaker():
    breaker = circuit_breaker.CircuitBreaker("groq", threshold=1, cooldown_s=0.01)
    breaker.record_failure("timeout")
    __import__("time").sleep(0.02)
    breaker.allow()
    breaker.record_failure("timeout")
    assert breaker.state == circuit_breaker.OPEN


def test_concurrent_requests_do_not_bypass_a_half_open_breaker():
    """The point of a probe allowance: a herd must not all get through."""
    breaker = circuit_breaker.CircuitBreaker(
        "groq", threshold=1, cooldown_s=0.01, half_open_probes=1)
    breaker.record_failure("timeout")
    __import__("time").sleep(0.02)

    results = []
    barrier = threading.Barrier(20)

    def attempt():
        barrier.wait()
        results.append(breaker.allow()[0])

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 1, f"{sum(results)} requests bypassed the breaker"


def test_breakers_are_scoped_independently():
    a = circuit_breaker.get("groq/model-a")
    b = circuit_breaker.get("groq/model-b")
    assert a is not b
    a.record_failure("authentication")
    assert a.state == circuit_breaker.OPEN
    assert b.state == circuit_breaker.CLOSED


def test_registry_returns_the_same_breaker_for_a_scope():
    assert circuit_breaker.get("groq") is circuit_breaker.get("groq")


# ─── Budget ──────────────────────────────────────────────────────────────────

def test_budget_is_off_until_a_ceiling_is_configured():
    assert budget.enabled() is False
    reservation = budget.reserve("groq", "m", "scriptwriting", "hi", 100)
    assert reservation.outcome == "unenforced"


@pytest.fixture
def priced(tmp_path, monkeypatch):
    """A cost config with invented-for-test prices only."""
    # Prices invented for the test only. Nothing here is a real provider rate,
    # and no default price table exists in the module.
    price = {"per_1k_input": 1.0, "per_1k_output": 2.0}
    config = {"groq": {"model-a": price, "openai/gpt-oss-120b": price}}
    path = tmp_path / "costs.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv(budget.COST_CONFIG_ENV, str(path))
    return config


def test_no_price_is_invented_for_an_unlisted_provider(priced):
    assert budget.pricing_for("openrouter", "anything") is None
    assert budget.estimate_cost("openrouter", "anything", "hi", 10) is None


def test_estimate_uses_the_configured_price(priced):
    cost = budget.estimate_cost("groq", "model-a", "x" * 4000, 1000)
    assert cost == pytest.approx(1.0 * 1.0 + 1.0 * 2.0)


def test_unpriceable_request_is_blocked_by_default(priced, monkeypatch):
    monkeypatch.setenv(budget.MAX_PER_REQUEST_ENV, "10")
    with pytest.raises(budget.BudgetExceededError) as exc:
        budget.reserve("openrouter", "m", "scriptwriting", "hi", 100)
    assert "no configured price" in str(exc.value)


def test_unpriceable_request_allowed_only_when_explicitly_configured(priced, monkeypatch):
    monkeypatch.setenv(budget.MAX_PER_REQUEST_ENV, "10")
    monkeypatch.setenv(budget.UNKNOWN_POLICY_ENV, budget.ALLOW)
    reservation = budget.reserve("openrouter", "m", "scriptwriting", "hi", 100)
    assert reservation.estimated == 0.0


def test_per_request_ceiling_rejects_before_dispatch(priced, monkeypatch):
    monkeypatch.setenv(budget.MAX_PER_REQUEST_ENV, "0.001")
    with pytest.raises(budget.BudgetExceededError) as exc:
        budget.reserve("groq", "model-a", "scriptwriting", "x" * 40000, 4000)
    assert "per-request ceiling" in str(exc.value)


def test_per_task_ceiling_accumulates(priced, monkeypatch):
    monkeypatch.setenv(budget.MAX_PER_TASK_ENV, "7")
    budget.reserve("groq", "model-a", "scriptwriting", "x" * 4000, 1000)  # 3.0
    budget.reserve("groq", "model-a", "scriptwriting", "x" * 4000, 1000)  # 6.0 <= 7
    with pytest.raises(budget.BudgetExceededError) as exc:
        budget.reserve("groq", "model-a", "scriptwriting", "x" * 4000, 1000)
    assert "ceiling" in str(exc.value)


def test_period_ceiling_blocks(priced, monkeypatch):
    monkeypatch.setenv(budget.MAX_PER_PERIOD_ENV, "4")
    budget.reserve("groq", "model-a", "scriptwriting", "x" * 4000, 1000)
    with pytest.raises(budget.BudgetExceededError):
        budget.reserve("groq", "model-a", "content_analysis", "x" * 4000, 1000)


def test_release_refunds_the_reservation(priced, monkeypatch):
    monkeypatch.setenv(budget.MAX_PER_PERIOD_ENV, "4")
    reservation = budget.reserve("groq", "model-a", "t", "x" * 4000, 1000)
    assert budget.usage_summary()["spent"] == pytest.approx(3.0)

    budget.release(reservation)
    assert budget.usage_summary()["spent"] == pytest.approx(0.0)


def test_failed_call_does_not_consume_budget(priced, monkeypatch):
    monkeypatch.setenv(budget.MAX_PER_PERIOD_ENV, "4")
    reservation = budget.reserve("groq", "model-a", "t", "x" * 4000, 1000)
    budget.settle(reservation, outcome="error")
    assert budget.usage_summary()["spent"] == pytest.approx(0.0)


def test_settle_reconciles_estimate_against_reported_usage(priced, monkeypatch):
    monkeypatch.setenv(budget.MAX_PER_PERIOD_ENV, "100")
    reservation = budget.reserve("groq", "model-a", "t", "x" * 4000, 1000)
    assert reservation.estimated == pytest.approx(3.0)

    actual = budget.cost_from_usage(
        "groq", "model-a", {"prompt_tokens": 100, "completion_tokens": 50})
    assert actual == pytest.approx(0.1 + 0.1)

    budget.settle(reservation, actual=actual, outcome="ok")
    assert budget.usage_summary()["spent"] == pytest.approx(0.2)


def test_cost_from_usage_returns_none_without_a_price(priced):
    assert budget.cost_from_usage(
        "openrouter", "m", {"prompt_tokens": 10, "completion_tokens": 5}) is None


def test_cost_from_usage_handles_gemini_field_names(priced):
    cost = budget.cost_from_usage(
        "groq", "model-a",
        {"promptTokenCount": 1000, "candidatesTokenCount": 1000})
    assert cost == pytest.approx(3.0)


def test_concurrent_reservations_cannot_overspend(priced, monkeypatch):
    """
    Each reservation is held under the lock, so N threads racing for a budget
    that fits fewer than N of them must not all be admitted.
    """
    monkeypatch.setenv(budget.MAX_PER_PERIOD_ENV, "9")  # three calls at 3.0
    admitted = []
    barrier = threading.Barrier(10)

    def attempt():
        barrier.wait()
        try:
            budget.reserve("groq", "model-a", "t", "x" * 4000, 1000)
            admitted.append(1)
        except budget.BudgetExceededError:
            admitted.append(0)

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(admitted) == 3, f"{sum(admitted)} of 10 admitted against a budget of 3"
    assert budget.usage_summary()["spent"] <= 9.0


def test_period_rollover_resets_spend(priced, monkeypatch, tmp_path):
    monkeypatch.setenv(budget.MAX_PER_PERIOD_ENV, "4")
    monkeypatch.setenv(budget.PERIOD_S_ENV, "60")
    budget.reserve("groq", "model-a", "t", "x" * 4000, 1000)

    state_path = tmp_path / "budget.json"
    data = json.loads(state_path.read_text())
    data["period_start"] = __import__("time").time() - 3600
    state_path.write_text(json.dumps(data))

    assert budget.usage_summary()["spent"] == pytest.approx(0.0)


# ─── Lifecycle integration ───────────────────────────────────────────────────

@pytest.fixture
def routed(router, real_registry, all_credentials, monkeypatch):
    from agent.modules import llm

    router.REGISTRY_PATH.write_text(json.dumps(real_registry), encoding="utf-8")
    monkeypatch.setenv("LLM_ROUTER", "true")
    monkeypatch.setenv("LLM_PROVIDER_SCRIPTWRITING", "groq")
    return router, llm


def test_unhealthy_provider_is_skipped_and_the_reason_recorded(routed, monkeypatch):
    router, llm = routed
    monkeypatch.setenv(provider_health.UNHEALTHY_THRESHOLD_ENV, "1")
    provider_health.record_failure("groq", "timeout")
    calls = []
    monkeypatch.setattr(llm, "_groq", lambda *a, **k: calls.append(1) or "text")
    monkeypatch.setattr(llm, "_openrouter", lambda *a, **k: "from openrouter")

    assert router.complete("hook", task="scriptwriting") == "from openrouter"
    assert calls == []
    assert any("groq" in f and "health" in f for f in router.last_execution()["fallbacks"])


def test_open_breaker_is_skipped(routed, monkeypatch):
    router, llm = routed
    circuit_breaker.get("groq").record_failure("authentication")
    calls = []
    monkeypatch.setattr(llm, "_groq", lambda *a, **k: calls.append(1) or "text")
    monkeypatch.setattr(llm, "_openrouter", lambda *a, **k: "from openrouter")

    assert router.complete("hook", task="scriptwriting") == "from openrouter"
    assert calls == []


def test_budget_ceiling_stops_the_call_before_dispatch(routed, monkeypatch):
    router, llm = routed
    monkeypatch.setenv(budget.MAX_PER_REQUEST_ENV, "0.000001")
    calls = []
    monkeypatch.setattr(llm, "_groq", lambda *a, **k: calls.append(1) or "text")
    monkeypatch.setattr(llm, "_openrouter", lambda *a, **k: calls.append(1) or "text")

    with pytest.raises(llm.LLMError):
        router.complete("hook", task="scriptwriting")
    assert calls == []


def test_failure_records_health_and_opens_the_breaker(routed, monkeypatch):
    router, llm = routed
    monkeypatch.setenv(provider_health.UNHEALTHY_THRESHOLD_ENV, "99")

    def fail(*a, **k):
        raise provider_errors.AuthenticationError("groq", "bad key")

    monkeypatch.setattr(llm, "_groq", fail)
    monkeypatch.setattr(llm, "_openrouter", lambda *a, **k: "text")

    router.complete("hook", task="scriptwriting")

    assert provider_health.status("groq")["consecutive_failures"] == 1
    # Authentication opens immediately: the next call would fail identically.
    assert circuit_breaker.get("groq").state == circuit_breaker.OPEN


def test_success_records_cost_and_usage_in_telemetry(routed, monkeypatch, priced):
    router, llm = routed
    monkeypatch.setenv(budget.MAX_PER_PERIOD_ENV, "100")
    # Must be a model the registry declares for groq, or routing rejects it
    # before the budget is ever consulted.
    monkeypatch.setenv("LLM_MODEL_SCRIPTWRITING", "openai/gpt-oss-120b")

    def serve(*a, **k):
        llm.LAST_ACTUAL_MODEL = "model-a"
        llm.LAST_USAGE = {"prompt_tokens": 100, "completion_tokens": 50}
        return "text"

    monkeypatch.setattr(llm, "_groq", serve)

    router.complete("hook", task="scriptwriting")
    exec_info = router.last_execution()

    assert exec_info["usage"] == {"prompt_tokens": 100, "completion_tokens": 50}
    assert exec_info["cost_actual"] == pytest.approx(0.2)


# ─── The retry defect this taxonomy fixes ────────────────────────────────────

def test_authentication_failure_is_attempted_exactly_once(monkeypatch):
    """
    tenacity was previously keyed on RequestException, so raise_for_status()
    turned a 401 into three attempts with exponential backoff before falling
    through. Retrying bad credentials cannot succeed and burns the budget that
    a genuinely transient failure would have needed.
    """
    from agent.modules import llm

    monkeypatch.setenv("GROQ_API_KEY", "k")
    calls = []

    class _Resp:
        status_code = 401
        text = "invalid api key"

        def json(self):
            return {}

        def raise_for_status(self):
            raise RuntimeError("should not be reached")

    def fake_post(url, **kw):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr(llm.requests, "post", fake_post)

    with pytest.raises(provider_errors.AuthenticationError):
        llm._groq("p", None, 0.7, 100)

    assert len(calls) == 1, f"a 401 was attempted {len(calls)} times"


def test_server_error_is_still_retried(monkeypatch):
    """The fix must not disable retries for failures that genuinely are transient."""
    from agent.modules import llm

    monkeypatch.setenv("GROQ_API_KEY", "k")
    calls = []

    class _Resp:
        status_code = 503
        text = "upstream unavailable"

        def json(self):
            return {}

        def raise_for_status(self):
            raise RuntimeError("should not be reached")

    def fake_post(url, **kw):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr(llm.requests, "post", fake_post)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    with pytest.raises(provider_errors.ServerError):
        llm._groq("p", None, 0.7, 100)

    assert len(calls) == llm._MAX_RETRIES
