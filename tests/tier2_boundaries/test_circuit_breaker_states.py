"""
Tier 2: Boundary & Corner Cases — Multi-Provider Circuit Breaker State Machine (Feature 7)
Tests 3-state transitions: CLOSED -> OPEN (on failure threshold) -> HALF-OPEN (on cooldown) -> CLOSED (on canary success),
and immediate re-OPEN on canary failure.
"""
import time
import pytest


class CircuitState:
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 0.5):
        self.state = CircuitState.CLOSED
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.failure_count = 0
        self.last_failure_time = 0.0

    def can_execute(self, current_time: float | None = None) -> bool:
        now = current_time if current_time is not None else time.time()
        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.OPEN:
            if now - self.last_failure_time >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        elif self.state == CircuitState.HALF_OPEN:
            # Canary attempt allowed
            return True
        return False

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self, current_time: float | None = None) -> None:
        now = current_time if current_time is not None else time.time()
        self.failure_count += 1
        self.last_failure_time = now
        if self.state == CircuitState.HALF_OPEN or self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN


def test_circuit_breaker_starts_closed():
    """Boundary Case 1: Initial state is CLOSED and allows requests."""
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=1.0)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True


def test_circuit_breaker_transitions_to_open_at_threshold():
    """Boundary Case 2: Exactly threshold failures transitions state from CLOSED to OPEN."""
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=1.0)

    # 1st failure: still closed
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 2nd failure: still closed
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 3rd failure (exact threshold boundary): transitions to OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False


def test_circuit_breaker_open_blocks_execution_before_cooldown():
    """Boundary Case 3: While OPEN and before cooldown, can_execute returns False (fail-fast)."""
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=10.0)
    t0 = 1000.0
    cb.record_failure(current_time=t0)
    cb.record_failure(current_time=t0)
    assert cb.state == CircuitState.OPEN

    # Check at t0 + 5.0s (less than 10.0s cooldown)
    assert cb.can_execute(current_time=t0 + 5.0) is False
    assert cb.state == CircuitState.OPEN


def test_circuit_breaker_transitions_to_half_open_after_cooldown():
    """Boundary Case 4: After cooldown elapsed, transitions to HALF_OPEN to allow canary test."""
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=5.0)
    t0 = 1000.0
    cb.record_failure(current_time=t0)
    cb.record_failure(current_time=t0)
    assert cb.state == CircuitState.OPEN

    # Check at t0 + 5.0s (exact cooldown boundary)
    assert cb.can_execute(current_time=t0 + 5.0) is True
    assert cb.state == CircuitState.HALF_OPEN


def test_circuit_breaker_half_open_success_resets_to_closed():
    """Boundary Case 5: Canary success in HALF_OPEN resets state to CLOSED and clears failure count."""
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=1.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    time.sleep(1.05)
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN

    # Canary succeeds
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0


def test_circuit_breaker_half_open_failure_immediately_reopens():
    """Boundary Case 6: Canary failure in HALF_OPEN immediately re-opens circuit."""
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=1.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    time.sleep(1.05)
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN

    # Canary fails
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False
