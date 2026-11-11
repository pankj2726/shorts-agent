"""
Unit test suite for SQLite Compare-And-Swap (CAS) task locking, fencing tokens,
lease expiration, and Markdown synchronization.
Milestone 1 / Requirement R1.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest

from agent.modules.orchestration import (
    TaskManager,
    TaskCollisionError,
    StaleLockTokenError,
    InvalidStateTransitionError,
)


def test_atomic_claim_success(tmp_path):
    db_file = tmp_path / "test_tasks.sqlite"
    md_file = tmp_path / "test_ownership.md"
    tm = TaskManager(db_path=db_file, task_md=md_file)

    with tm._get_conn() as conn:
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('T001', 'Test Task', 'PENDING')")
        conn.commit()

    token = tm.claim_task("T001", "agent-4-providers", lease_seconds=120)
    assert token is not None
    assert len(token) > 10

    task = tm.get_task("T001")
    assert task["status"] == "IN_PROGRESS"
    assert task["owner"] == "agent-4-providers"
    assert task["lock_token"] == token


def test_collision_prevention_simultaneous_claim(tmp_path):
    db_file = tmp_path / "test_tasks.sqlite"
    md_file = tmp_path / "test_ownership.md"
    tm = TaskManager(db_path=db_file, task_md=md_file)

    with tm._get_conn() as conn:
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('T001', 'Contested Task', 'PENDING')")
        conn.commit()

    results = []
    errors = []

    def attempt_claim(agent_name):
        try:
            tok = tm.claim_task("T001", agent_name, lease_seconds=60)
            results.append((agent_name, tok))
        except TaskCollisionError as err:
            errors.append((agent_name, err))

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(attempt_claim, "agent-1-research")
        f2 = executor.submit(attempt_claim, "agent-5-innovation")
        f1.result()
        f2.result()

    assert len(results) == 1, "Exactly one agent must successfully claim the task"
    assert len(errors) == 1, "The contending agent must receive TaskCollisionError"


def test_fencing_token_completion_success(tmp_path):
    db_file = tmp_path / "test_tasks.sqlite"
    md_file = tmp_path / "test_ownership.md"
    tm = TaskManager(db_path=db_file, task_md=md_file)

    with tm._get_conn() as conn:
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('T002', 'Completion Task', 'PENDING')")
        conn.commit()

    token = tm.claim_task("T002", "agent-1-research", lease_seconds=60)
    tm.complete_task("T002", "agent-1-research", token, "agents/agent-1-research/script.json")

    task = tm.get_task("T002")
    assert task["status"] == "COMPLETED"
    assert task["artifact_path"] == "agents/agent-1-research/script.json"
    assert task["lock_token"] is None


def test_stale_fencing_token_rejected(tmp_path):
    db_file = tmp_path / "test_tasks.sqlite"
    md_file = tmp_path / "test_ownership.md"
    tm = TaskManager(db_path=db_file, task_md=md_file)

    with tm._get_conn() as conn:
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('T003', 'Stale Token Task', 'PENDING')")
        conn.commit()

    tm.claim_task("T003", "agent-1-research", lease_seconds=60)

    bogus_token = "bogus-uuid-token-12345"
    with pytest.raises(StaleLockTokenError):
        tm.complete_task("T003", "agent-1-research", bogus_token, "fake_artifact.json")


def test_lease_expiration_and_takeover(tmp_path):
    db_file = tmp_path / "test_tasks.sqlite"
    md_file = tmp_path / "test_ownership.md"
    tm = TaskManager(db_path=db_file, task_md=md_file)

    with tm._get_conn() as conn:
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('T004', 'Lease Expiry Task', 'PENDING')")
        conn.commit()

    # Claim with very short lease (1 second)
    token1 = tm.claim_task("T004", "agent-1-research", lease_seconds=1)
    time.sleep(1.5)  # Wait for lease to expire

    # Second agent takes over
    token2 = tm.claim_task("T004", "agent-4-providers", lease_seconds=60)
    assert token2 != token1

    task = tm.get_task("T004")
    assert task["owner"] == "agent-4-providers"
    assert task["lock_token"] == token2

    # Agent 1 attempts to complete with original token; must be rejected
    with pytest.raises(StaleLockTokenError):
        tm.complete_task("T004", "agent-1-research", token1, "agents/agent-1-research/output.md")


def test_blocked_state_lifecycle(tmp_path):
    db_file = tmp_path / "test_tasks.sqlite"
    md_file = tmp_path / "test_ownership.md"
    tm = TaskManager(db_path=db_file, task_md=md_file)

    with tm._get_conn() as conn:
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('T005', 'Blocked Task', 'PENDING')")
        conn.commit()

    token = tm.claim_task("T005", "agent-3-social", lease_seconds=60)
    tm.block_task("T005", "agent-3-social", token, reason="Waiting for operator auth", condition="auth_granted")

    task = tm.get_task("T005")
    assert task["status"] == "BLOCKED"
    assert task["block_reason"] == "Waiting for operator auth"
    assert task["lock_token"] is None

    # Unblocking transitions back to PENDING
    tm.unblock_task("T005", reason="Auth granted")
    unblocked = tm.get_task("T005")
    assert unblocked["status"] == "PENDING"
    assert unblocked["block_reason"] is None


def test_illegal_completed_to_in_progress(tmp_path):
    db_file = tmp_path / "test_tasks.sqlite"
    md_file = tmp_path / "test_ownership.md"
    tm = TaskManager(db_path=db_file, task_md=md_file)

    with tm._get_conn() as conn:
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('T006', 'Finished Task', 'COMPLETED')")
        conn.commit()

    with pytest.raises((TaskCollisionError, InvalidStateTransitionError)):
        tm.claim_task("T006", "agent-1-research")


def test_markdown_sync_formatting(tmp_path):
    db_file = tmp_path / "test_tasks.sqlite"
    md_file = tmp_path / "test_ownership.md"
    tm = TaskManager(db_path=db_file, task_md=md_file)

    with tm._get_conn() as conn:
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('T007', 'Markdown Sync Test', 'PENDING')")
        conn.commit()

    tm.claim_task("T007", "agent-2-anti-slop", lease_seconds=60)

    # Verify markdown file exists and has formatted table row
    assert md_file.exists()
    content = md_file.read_text(encoding="utf-8")
    assert "| T007 | Markdown Sync Test | agent-2-anti-slop | 🔄 In Progress | — |" in content


def test_heartbeat_lease_renewal(tmp_path):
    db_file = tmp_path / "test_tasks.sqlite"
    md_file = tmp_path / "test_ownership.md"
    tm = TaskManager(db_path=db_file, task_md=md_file)

    with tm._get_conn() as conn:
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('T008', 'Heartbeat Test', 'PENDING')")
        conn.commit()

    token = tm.claim_task("T008", "agent-1-research", lease_seconds=30)
    original_task = tm.get_task("T008")
    original_expiry = original_task["lease_expires_at"]

    time.sleep(1.0)
    tm.heartbeat("T008", "agent-1-research", token, extension_seconds=120)

    renewed_task = tm.get_task("T008")
    assert renewed_task["lease_expires_at"] > original_expiry
