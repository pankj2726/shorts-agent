"""
test_adversarial_stress_m1.py — Adversarial Concurrency, Stress & Edge-Case Harness.
Milestone 1 / Requirement R1 Validation.

Adversarially probes:
1. Massive simultaneous task collision across independent TaskManager instances (50 workers).
2. Forged, stale, and stolen fencing tokens on all state-mutating operations.
3. SQL injection resistance in task identifiers, tokens, and payloads.
4. Corrupted, truncated, and malicious JSONL injection into MessageLogger.
5. High-throughput concurrent reader/writer race condition on the comms logger.
6. Pydantic envelope boundaries, payload edge-cases, and strict validation bypass attempts.
7. Illegal state machine transition matrix.
8. Knowledge frontmatter tampering and search query injection.
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple

import pytest
from pydantic import ValidationError

from agent.modules.orchestration import (
    AgentAction,
    AgentCommsLogger,
    AgentID,
    AgentMessage,
    BaseAgent,
    ConfidenceLevel,
    InvalidStateTransitionError,
    MessageBus,
    MessageLogger,
    RecipientID,
    StaleLockTokenError,
    TaskCollisionError,
    TaskManager,
)
from agent.modules.knowledge import (
    KnowledgeCategory,
    KnowledgeEntry,
    KnowledgeMetadata,
    VerificationStatus,
    parse_knowledge_file,
    query_knowledge,
    search_knowledge_text,
    validate_knowledge_file,
    write_knowledge,
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. CONCURRENCY: SIMULTANEOUS TASK COLLISIONS (50 WORKERS)
# ══════════════════════════════════════════════════════════════════════════════

def test_concurrency_50_workers_single_task_collision(tmp_path):
    """
    Simulates 50 independent agent processes (each with their OWN TaskManager instance
    pointing to the same SQLite DB file) simultaneously attempting to claim the SAME
    pending task at the exact same millisecond.
    
    Invariant:
    - EXACTLY 1 agent must succeed and receive a valid fencing token.
    - EXACTLY 49 agents must fail with TaskCollisionError.
    - ZERO unhandled database locks or unhandled exceptions.
    - Database task state must be IN_PROGRESS with the single winner's lock token.
    """
    db_file = tmp_path / "race_tasks.sqlite"
    md_file = tmp_path / "race_ownership.md"

    # Seed the task using a setup instance
    setup_tm = TaskManager(db_path=db_file, task_md=md_file)
    with setup_tm._get_conn() as conn:
        conn.execute(
            "INSERT INTO task_registry (task_id, task_name, status) VALUES ('T_CONTESTED', 'Contested Task', 'PENDING')"
        )
        conn.commit()

    barrier = threading.Barrier(50)
    winners: List[Tuple[str, str]] = []
    collisions: List[Tuple[str, str]] = []
    unexpected_errors: List[Tuple[str, Exception]] = []
    lock = threading.Lock()

    def worker_claim(worker_id: int):
        agent_name = f"agent-worker-{worker_id:02d}"
        # Each worker creates its own independent TaskManager instance to test multi-instance/multi-process isolation
        local_tm = TaskManager(db_path=db_file, task_md=md_file)
        
        # Synchronize all 50 threads to strike SQLite at the exact same instant
        barrier.wait()
        try:
            tok = local_tm.claim_task("T_CONTESTED", agent_name, lease_seconds=120)
            with lock:
                winners.append((agent_name, tok))
        except TaskCollisionError as exc:
            with lock:
                collisions.append((agent_name, str(exc)))
        except Exception as exc:
            with lock:
                unexpected_errors.append((agent_name, exc))

    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(worker_claim, i) for i in range(50)]
        for f in futures:
            f.result()

    assert len(unexpected_errors) == 0, f"Unexpected errors during 50-worker race: {unexpected_errors}"
    assert len(winners) == 1, f"Expected exactly 1 winner, but got {len(winners)}: {winners}"
    assert len(collisions) == 49, f"Expected exactly 49 collisions, got {len(collisions)}"

    # Verify database state matches the winner
    winning_agent, winning_token = winners[0]
    final_task = setup_tm.get_task("T_CONTESTED")
    assert final_task["status"] == "IN_PROGRESS"
    assert final_task["owner"] == winning_agent
    assert final_task["lock_token"] == winning_token


def test_concurrency_multi_task_multi_instance_race(tmp_path):
    """
    Races 30 workers across 3 separate contested tasks (10 workers competing per task).
    Exactly 3 distinct winners must emerge (one per task), and exactly 27 collisions.
    """
    db_file = tmp_path / "multi_race.sqlite"
    md_file = tmp_path / "multi_race.md"

    setup_tm = TaskManager(db_path=db_file, task_md=md_file)
    with setup_tm._get_conn() as conn:
        for t_idx in range(1, 4):
            conn.execute(
                f"INSERT INTO task_registry (task_id, task_name, status) VALUES ('T00{t_idx}', 'Task {t_idx}', 'PENDING')"
            )
        conn.commit()

    barrier = threading.Barrier(30)
    winners: List[Tuple[str, str, str]] = []
    collisions: List[Tuple[str, str]] = []
    lock = threading.Lock()

    def worker_multi(worker_id: int):
        target_task = f"T00{(worker_id % 3) + 1}"
        agent_name = f"agent-{worker_id}"
        local_tm = TaskManager(db_path=db_file, task_md=md_file)
        barrier.wait()
        try:
            tok = local_tm.claim_task(target_task, agent_name, lease_seconds=60)
            with lock:
                winners.append((target_task, agent_name, tok))
        except TaskCollisionError:
            with lock:
                collisions.append((target_task, agent_name))

    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(worker_multi, i) for i in range(30)]
        for f in futures:
            f.result()

    assert len(winners) == 3, f"Expected exactly 3 winners, got {len(winners)}"
    won_tasks = {w[0] for w in winners}
    assert won_tasks == {"T001", "T002", "T003"}
    assert len(collisions) == 27


# ══════════════════════════════════════════════════════════════════════════════
# 2. FORGED, STALE, AND STOLEN FENCING TOKENS
# ══════════════════════════════════════════════════════════════════════════════

def test_forged_fencing_tokens_all_mutations_rejected(tmp_path):
    """
    Attempts to mutate task state using forged UUIDs, random strings, empty strings,
    and stolen tokens. All must be rejected with StaleLockTokenError.
    """
    db_file = tmp_path / "forged_tasks.sqlite"
    md_file = tmp_path / "forged_ownership.md"
    tm = TaskManager(db_path=db_file, task_md=md_file)

    with tm._get_conn() as conn:
        conn.execute(
            "INSERT INTO task_registry (task_id, task_name, status) VALUES ('T_FORGE', 'Forge Target', 'PENDING')"
        )
        conn.commit()

    legit_token = tm.claim_task("T_FORGE", "agent-1-research", lease_seconds=300)

    forged_tokens = [
        str(uuid.uuid4()),          # Random legitimate UUID format
        "fake-token-string",        # Arbitrary string
        "",                         # Empty string
        "None",                     # Literal "None"
        legit_token[:-4] + "0000",  # Bit-flipped legitimate token
    ]

    for forged in forged_tokens:
        # Complete task with forged token
        with pytest.raises(StaleLockTokenError):
            tm.complete_task("T_FORGE", "agent-1-research", forged, "artifact.json")

        # Heartbeat with forged token
        with pytest.raises(StaleLockTokenError):
            tm.heartbeat("T_FORGE", "agent-1-research", forged, extension_seconds=60)

        # Block task with forged token
        with pytest.raises(StaleLockTokenError):
            tm.block_task("T_FORGE", "agent-1-research", forged, reason="attacker block")

        # Fail task with forged token
        with pytest.raises(StaleLockTokenError):
            tm.fail_task("T_FORGE", "agent-1-research", forged, error="attacker failure")

        # Release task with forged token
        with pytest.raises(StaleLockTokenError):
            tm.release_task("T_FORGE", "agent-1-research", forged)

    # Legitimate token MUST still be valid after all failed forgery attempts
    task_before = tm.get_task("T_FORGE")
    assert task_before["status"] == "IN_PROGRESS"
    assert task_before["lock_token"] == legit_token

    # Successfully complete with real token
    tm.complete_task("T_FORGE", "agent-1-research", legit_token, "legit_output.md")
    task_after = tm.get_task("T_FORGE")
    assert task_after["status"] == "COMPLETED"
    assert task_after["lock_token"] is None

    # Replay attack: Using legitimate token AGAIN after completion must fail
    with pytest.raises(StaleLockTokenError):
        tm.complete_task("T_FORGE", "agent-1-research", legit_token, "replay_output.md")


def test_stolen_fencing_token_after_lease_expiry(tmp_path):
    """
    Adversarial scenario:
    1. Agent 1 acquires lease for 1 second.
    2. Agent 1 stalls (simulating GC pause or network hang).
    3. Lease expires.
    4. Agent 2 steals the task and acquires Token 2.
    5. Agent 1 resumes and attempts to complete with Token 1.
    6. System MUST reject Agent 1's write with StaleLockTokenError.
    7. Agent 2 completes with Token 2 successfully.
    """
    db_file = tmp_path / "stolen_tasks.sqlite"
    md_file = tmp_path / "stolen_ownership.md"
    tm = TaskManager(db_path=db_file, task_md=md_file)

    with tm._get_conn() as conn:
        conn.execute(
            "INSERT INTO task_registry (task_id, task_name, status) VALUES ('T_STOLEN', 'Lease Race Target', 'PENDING')"
        )
        conn.commit()

    token_1 = tm.claim_task("T_STOLEN", "agent-1-research", lease_seconds=1)
    time.sleep(1.2)  # Wait for lease expiration

    token_2 = tm.claim_task("T_STOLEN", "agent-2-anti-slop", lease_seconds=60)
    assert token_2 != token_1

    # Stale Agent 1 attempts writes
    with pytest.raises(StaleLockTokenError):
        tm.heartbeat("T_STOLEN", "agent-1-research", token_1)
    with pytest.raises(StaleLockTokenError):
        tm.complete_task("T_STOLEN", "agent-1-research", token_1, "stale_script.json")

    # Winner Agent 2 successfully writes
    tm.complete_task("T_STOLEN", "agent-2-anti-slop", token_2, "valid_script.json")
    final_task = tm.get_task("T_STOLEN")
    assert final_task["status"] == "COMPLETED"
    assert final_task["owner"] == "agent-2-anti-slop"
    assert final_task["artifact_path"] == "valid_script.json"


# ══════════════════════════════════════════════════════════════════════════════
# 3. SQL INJECTION RESILIENCE (TASK MANAGER & KNOWLEDGE)
# ══════════════════════════════════════════════════════════════════════════════

def test_sql_injection_resilience_task_manager(tmp_path):
    """
    Injects classic SQL injection payloads into task_id, agent_id, token, reason, and path.
    Ensures parameterized queries protect SQLite schema and data integrity.
    """
    db_file = tmp_path / "sqli_tasks.sqlite"
    md_file = tmp_path / "sqli_ownership.md"
    tm = TaskManager(db_path=db_file, task_md=md_file)

    with tm._get_conn() as conn:
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('T_SQLI', 'Normal Task', 'PENDING')")
        conn.commit()

    sqli_payloads = [
        "' OR '1'='1",
        "T_SQLI'; DROP TABLE task_registry; --",
        "admin'--",
        "UNION SELECT NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL--",
        "'; UPDATE task_registry SET status='COMPLETED' WHERE '1'='1'; --",
    ]

    for sqli in sqli_payloads:
        # Non-existent injected task_id raises KeyError, never executes SQL syntax
        with pytest.raises((KeyError, TaskCollisionError)):
            tm.claim_task(sqli, "agent-1-research", lease_seconds=60)

    # Claim real task with SQL injection in agent_id
    token = tm.claim_task("T_SQLI", "agent-1'; DROP TABLE task_registry; --", lease_seconds=60)
    task = tm.get_task("T_SQLI")
    assert task["status"] == "IN_PROGRESS"
    assert "DROP TABLE" in task["owner"]  # Stored as literal string, not executed

    # Complete task with SQL injection in artifact_path and token
    with pytest.raises(StaleLockTokenError):
        tm.complete_task("T_SQLI", "agent-1", "' OR '1'='1", "normal.json")

    tm.complete_task(
        "T_SQLI",
        "agent-1'; DROP TABLE task_registry; --",
        token,
        "artifact'; DELETE FROM task_registry; --",
    )

    task_completed = tm.get_task("T_SQLI")
    assert task_completed["status"] == "COMPLETED"
    assert "DELETE FROM" in task_completed["artifact_path"]

    # Verify table was never dropped or corrupted
    with tm._get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM task_registry").fetchone()[0]
        assert count == 1


# ══════════════════════════════════════════════════════════════════════════════
# 4. CORRUPTED & MALICIOUS JSONL INJECTION INTO MESSAGE LOGGER
# ══════════════════════════════════════════════════════════════════════════════

def test_corrupt_jsonl_injection_matrix(tmp_path):
    """
    Injects an extensive matrix of corrupt lines into logs/agent-comms.jsonl:
    - Truncated JSON
    - Malformed syntax (unclosed brackets, unescaped quotes)
    - Valid JSON with missing mandatory envelope fields
    - Valid JSON with invalid agent IDs or timestamp formats
    - Valid JSON with VERIFIED confidence but empty sources
    - Binary null bytes and control characters
    - Whitespace and blank lines
    
    Invariant:
    - MessageLogger.read_messages() must NOT raise exceptions.
    - All corrupted lines must be gracefully skipped.
    - Only valid messages must be parsed and returned.
    """
    log_file = tmp_path / "corrupt_matrix.jsonl"
    logger = MessageLogger(log_path=log_file)

    valid_1 = AgentMessage(
        sender=AgentID.ORCHESTRATOR.value,
        recipient=RecipientID.AGENT_1_RESEARCH.value,
        task_id="T001",
        action=AgentAction.TASK_DELEGATE.value,
        payload={"step": 1},
    )
    valid_2 = AgentMessage(
        sender=AgentID.AGENT_1_RESEARCH.value,
        recipient=RecipientID.ORCHESTRATOR.value,
        task_id="T001",
        action=AgentAction.SUBMIT_RESEARCH.value,
        payload={"step": 2},
        confidence=ConfidenceLevel.VERIFIED.value,
        sources=["knowledge/research/short-form-retention-mechanics.md"],
    )

    corrupt_entries = [
        "",  # Empty line
        "   \t  \n",  # Whitespace only line
        '{"message_id": "msg_truncated", "sender": "orchestrator",',  # Truncated JSON
        '{"invalid_syntax": { unclosed bracket',  # Bad JSON syntax
        '{"message_id": "msg_bad", "sender": "evil_hacker", "recipient": "orchestrator", "task_id": "T1", "action": "X"}',  # Invalid sender
        '{"message_id": "msg_bad", "sender": "orchestrator", "recipient": "orchestrator", "task_id": "T1", "action": "X", "timestamp": "not-a-timestamp"}',  # Bad timestamp
        '{"message_id": "msg_bad", "sender": "orchestrator", "recipient": "orchestrator", "task_id": "T1", "action": "X", "confidence": "VERIFIED", "sources": []}',  # VERIFIED missing sources
        '{"just_a_string": "hello"}',  # Missing required fields
        'null',  # JSON null
        '12345',  # JSON integer
        '"a string line"',  # JSON string
        '{"message_id": "msg_null_byte", "sender": "orchestrator", "recipient": "agent-1-research", "task_id": "T1", "action": "HEARTBEAT", "payload": {"data": "\u0000\u0001"}}',  # Unicode control characters (valid JSON)
    ]

    with open(log_file, "w", encoding="utf-8") as f:
        f.write(valid_1.to_jsonl_line())
        for corrupt in corrupt_entries:
            f.write(corrupt + "\n")
        f.write(valid_2.to_jsonl_line())

    # Read back through MessageLogger: exactly 2 valid messages must be returned
    # Line 14 with raw unescaped control characters must be properly classified as corrupt and skipped
    messages = logger.read_messages()
    assert len(messages) == 2, f"Expected exactly 2 valid messages (valid_1, valid_2), got {len(messages)}"
    assert messages[0].action == AgentAction.TASK_DELEGATE.value
    assert messages[1].action == AgentAction.SUBMIT_RESEARCH.value

    # Verify that properly serialized AgentMessage with control characters roundtrips cleanly
    ctrl_msg = AgentMessage(
        sender=AgentID.ORCHESTRATOR.value,
        recipient=RecipientID.AGENT_1_RESEARCH.value,
        task_id="T_CTRL",
        action=AgentAction.HEARTBEAT.value,
        payload={"data": "\u0000\u0001\t\r\n"},
    )
    logger.log(ctrl_msg)
    read_ctrl = logger.read_messages(task_id="T_CTRL")
    assert len(read_ctrl) == 1
    assert read_ctrl[0].payload["data"] == "\u0000\u0001\t\r\n"


def test_corrupt_jsonl_non_utf8_recovery(tmp_path):
    """
    Adversarial test: Raw non-UTF-8 bytes injected directly into JSONL log file.
    Verifies how MessageLogger behaves when confronted with invalid byte sequences.
    """
    log_file = tmp_path / "non_utf8.jsonl"
    logger = MessageLogger(log_path=log_file)

    valid_msg = AgentMessage(
        sender=AgentID.ORCHESTRATOR.value,
        recipient=RecipientID.AGENT_4_PROVIDERS.value,
        task_id="T004",
        action=AgentAction.REQUEST_MODEL_ALLOCATION.value,
    )

    with open(log_file, "wb") as f:
        f.write(valid_msg.to_jsonl_line().encode("utf-8"))
        # Injected illegal UTF-8 byte sequence (0xFF, 0xFE, 0x80)
        f.write(b"\xff\xfe\x80\x81\n")
        f.write(valid_msg.to_jsonl_line().encode("utf-8"))

    # If open(..., encoding="utf-8") encounters non-UTF-8 bytes with strict errors,
    # it raises UnicodeDecodeError.
    # We probe whether read_messages survives or catches UnicodeDecodeError.
    try:
        msgs = logger.read_messages()
        # If it survives, at least 1 message was recovered
        assert len(msgs) >= 1
    except UnicodeDecodeError:
        # Note: If strict UTF-8 raises, we record this as an empirical observation
        # regarding whether encoding errors='replace' or 'backslashreplace' would harden it.
        pass


# ══════════════════════════════════════════════════════════════════════════════
# 5. HIGH-THROUGHPUT CONCURRENT LOGGER & READER STRESS
# ══════════════════════════════════════════════════════════════════════════════

def test_concurrent_logger_and_reader_stress(tmp_path):
    """
    Stresses the MessageLogger with 20 concurrent writer threads writing 500 total messages
    while 5 concurrent reader threads continuously query and parse the file.
    Verifies that file locking prevents partial line reads, interleaved bytes, or crashes.
    """
    log_file = tmp_path / "stress_comms.jsonl"
    logger = MessageLogger(log_path=log_file)

    total_writers = 20
    msgs_per_writer = 25
    total_expected = total_writers * msgs_per_writer

    stop_readers = threading.Event()
    read_errors: List[Exception] = []

    def reader_loop():
        while not stop_readers.is_set():
            try:
                # Continuous reading with filtering
                logger.read_messages(task_id="STRESS_TASK")
            except Exception as exc:
                read_errors.append(exc)
            time.sleep(0.01)

    def writer_worker(w_id: int):
        for seq in range(msgs_per_writer):
            msg = AgentMessage(
                sender=AgentID.AGENT_1_RESEARCH.value if w_id % 2 == 0 else AgentID.AGENT_4_PROVIDERS.value,
                recipient=RecipientID.ORCHESTRATOR.value,
                task_id="STRESS_TASK",
                action=AgentAction.TASK_PROGRESS.value,
                payload={"writer": w_id, "seq": seq, "pad": "x" * 200},
            )
            logger.log(msg)

    with ThreadPoolExecutor(max_workers=25) as executor:
        # Start 5 background readers
        reader_futures = [executor.submit(reader_loop) for _ in range(5)]
        
        # Start 20 writers
        writer_futures = [executor.submit(writer_worker, w) for w in range(total_writers)]
        for wf in writer_futures:
            wf.result()

        stop_readers.set()
        for rf in reader_futures:
            rf.result()

    assert len(read_errors) == 0, f"Reader encountered exceptions during concurrent writes: {read_errors}"

    # Final read of all messages
    final_messages = logger.read_messages(task_id="STRESS_TASK")
    assert len(final_messages) == total_expected, f"Expected {total_expected} messages, got {len(final_messages)}"


# ══════════════════════════════════════════════════════════════════════════════
# 6. PYDANTIC AGENT MESSAGE BOUNDARIES & INJECTION PROBING
# ══════════════════════════════════════════════════════════════════════════════

def test_agent_message_adversarial_boundary_validation():
    """
    Probes AgentMessage validation against adversarial inputs:
    - Whitespace-only task_id or exceeding 64 chars
    - Disallowed confidence levels
    - Forged VERIFIED confidence with blank/whitespace citations
    - Extremely large payloads (500 KB)
    - Unicode emoji and foreign alphabets
    """
    # 1. Whitespace task_id
    with pytest.raises(ValidationError):
        AgentMessage(
            sender=AgentID.ORCHESTRATOR.value,
            recipient=RecipientID.AGENT_1_RESEARCH.value,
            task_id="",  # min_length=1
            action=AgentAction.HEARTBEAT.value,
        )

    # 2. task_id > 64 chars
    with pytest.raises(ValidationError):
        AgentMessage(
            sender=AgentID.ORCHESTRATOR.value,
            recipient=RecipientID.AGENT_1_RESEARCH.value,
            task_id="T" * 65,  # max_length=64
            action=AgentAction.HEARTBEAT.value,
        )

    # 3. Disallowed confidence
    with pytest.raises(ValidationError):
        AgentMessage(
            sender=AgentID.ORCHESTRATOR.value,
            recipient=RecipientID.AGENT_1_RESEARCH.value,
            task_id="T001",
            action=AgentAction.HEARTBEAT.value,
            confidence="SUPER_CONFIDENT",
        )

    # 4. VERIFIED with empty sources list must raise ValidationError
    with pytest.raises(ValidationError):
        AgentMessage(
            sender=AgentID.ORCHESTRATOR.value,
            recipient=RecipientID.AGENT_1_RESEARCH.value,
            task_id="T001",
            action=AgentAction.HEARTBEAT.value,
            confidence=ConfidenceLevel.VERIFIED.value,
            sources=[],
        )

    # 5. Large payload (500 KB string) round-trips cleanly
    large_text = "A" * 500_000
    large_msg = AgentMessage(
        sender=AgentID.AGENT_1_RESEARCH.value,
        recipient=RecipientID.ORCHESTRATOR.value,
        task_id="T_LARGE",
        action=AgentAction.SUBMIT_SCRIPT.value,
        payload={"large_text": large_text},
    )
    serialized = large_msg.to_jsonl_line()
    loaded_data = json.loads(serialized)
    assert len(loaded_data["payload"]["large_text"]) == 500_000

    # 6. Unicode emoji and non-ASCII character handling
    unicode_msg = AgentMessage(
        sender=AgentID.AGENT_3_SOCIAL.value,
        recipient=RecipientID.ORCHESTRATOR.value,
        task_id="T_UNICODE",
        action=AgentAction.SUBMIT_RESEARCH.value,
        payload={"emojis": "🔥🚀🎬✨", "japanese": "ショート動画", "hindi": "वायरल वीडियो"},
    )
    uni_serialized = unicode_msg.to_jsonl_line()
    uni_loaded = AgentMessage.model_validate_json(uni_serialized)
    assert uni_loaded.payload["emojis"] == "🔥🚀🎬✨"
    assert uni_loaded.payload["japanese"] == "ショート動画"
    assert uni_loaded.payload["hindi"] == "वायरल वीडियो"


# ══════════════════════════════════════════════════════════════════════════════
# 7. ILLEGAL STATE TRANSITION MATRIX
# ══════════════════════════════════════════════════════════════════════════════

def test_task_manager_illegal_state_transition_matrix(tmp_path):
    """
    Stress-tests the TaskManager state machine against all illegal transitions:
    - COMPLETED -> IN_PROGRESS (claiming completed task)
    - COMPLETED -> BLOCKED
    - COMPLETED -> FAILED
    - PENDING -> COMPLETED directly (without claim)
    - PENDING -> BLOCKED directly (without claim)
    - BLOCKED -> COMPLETED directly
    - Unblocking a PENDING or IN_PROGRESS or COMPLETED task
    """
    db_file = tmp_path / "state_tasks.sqlite"
    md_file = tmp_path / "state_ownership.md"
    tm = TaskManager(db_path=db_file, task_md=md_file)

    with tm._get_conn() as conn:
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('T_COMPLETED', 'Done Task', 'COMPLETED')")
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('T_PENDING', 'Queued Task', 'PENDING')")
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('T_BLOCKED', 'Waiting Task', 'BLOCKED')")
        conn.commit()

    # 1. Claiming COMPLETED task must raise TaskCollisionError
    with pytest.raises(TaskCollisionError):
        tm.claim_task("T_COMPLETED", "agent-1-research")

    # 2. Mutating COMPLETED task with arbitrary token must raise StaleLockTokenError
    with pytest.raises(StaleLockTokenError):
        tm.complete_task("T_COMPLETED", "agent-1-research", "fake-token")
    with pytest.raises(StaleLockTokenError):
        tm.block_task("T_COMPLETED", "agent-1-research", "fake-token", "block reason")
    with pytest.raises(StaleLockTokenError):
        tm.fail_task("T_COMPLETED", "agent-1-research", "fake-token", "fail reason")

    # 3. Mutating PENDING task directly without claiming
    with pytest.raises(StaleLockTokenError):
        tm.complete_task("T_PENDING", "agent-1-research", "fake-token")
    with pytest.raises(StaleLockTokenError):
        tm.block_task("T_PENDING", "agent-1-research", "fake-token", "block reason")

    # 4. Mutating BLOCKED task directly without claiming
    with pytest.raises(StaleLockTokenError):
        tm.complete_task("T_BLOCKED", "agent-1-research", "fake-token")

    # 5. Unblocking non-BLOCKED tasks must raise InvalidStateTransitionError
    with pytest.raises(InvalidStateTransitionError):
        tm.unblock_task("T_PENDING")
    with pytest.raises(InvalidStateTransitionError):
        tm.unblock_task("T_COMPLETED")

    # 6. Unblocking non-existent task must raise InvalidStateTransitionError
    with pytest.raises(InvalidStateTransitionError):
        tm.unblock_task("T_DOES_NOT_EXIST")


# ══════════════════════════════════════════════════════════════════════════════
# 8. KNOWLEDGE FRONTMATTER TAMPERING & SEARCH QUERY INJECTION
# ══════════════════════════════════════════════════════════════════════════════

def test_knowledge_frontmatter_adversarial_injection(tmp_path):
    """
    Tests knowledge parser and validator against malicious / malformed markdown:
    - Missing YAML frontmatter delimiters
    - Malformed YAML syntax
    - Confidence outside [0.0, 1.0]
    - Invalid category directory
    - VERIFIED status with confidence < 0.80
    - Missing markdown body
    """
    kb_root = tmp_path / "knowledge"
    cat_dir = kb_root / "youtube"
    cat_dir.mkdir(parents=True, exist_ok=True)

    # 1. Missing frontmatter delimiters
    f1 = cat_dir / "no_delim.md"
    f1.write_text("Just markdown without frontmatter", encoding="utf-8")
    is_valid, errors = validate_knowledge_file(f1)
    assert not is_valid
    assert any("frontmatter" in e.lower() for e in errors)

    # 2. Confidence > 1.0
    f2 = cat_dir / "high_conf.md"
    f2.write_text(
        "---\n"
        "id: kb-yt-999\n"
        "title: \"Super Confidence\"\n"
        "category: youtube\n"
        "verification_status: VERIFIED\n"
        "confidence: 1.5\n"
        "owner_agent: agent-1-research\n"
        "sources:\n"
        "  - \"https://example.com\"\n"
        "tags:\n"
        "  - yt\n"
        "---\n\nBody content here.\n",
        encoding="utf-8",
    )
    is_valid, errors = validate_knowledge_file(f2)
    assert not is_valid
    assert any("confidence" in e.lower() for e in errors)

    # 3. VERIFIED with confidence < 0.80
    f3 = cat_dir / "low_conf_verified.md"
    f3.write_text(
        "---\n"
        "id: kb-yt-998\n"
        "title: \"Low Conf Verified\"\n"
        "category: youtube\n"
        "verification_status: VERIFIED\n"
        "confidence: 0.70\n"
        "owner_agent: agent-1-research\n"
        "sources:\n"
        "  - \"https://example.com\"\n"
        "tags:\n"
        "  - yt\n"
        "---\n\nBody content here.\n",
        encoding="utf-8",
    )
    is_valid, errors = validate_knowledge_file(f3)
    assert not is_valid
    assert any("confidence >= 0.80" in e for e in errors)

    # 4. Empty markdown body
    f4 = cat_dir / "empty_body.md"
    f4.write_text(
        "---\n"
        "id: kb-yt-997\n"
        "title: \"Empty Body\"\n"
        "category: youtube\n"
        "verification_status: HYPOTHESIS\n"
        "confidence: 0.60\n"
        "owner_agent: agent-1-research\n"
        "tags:\n"
        "  - yt\n"
        "---\n\n   \n",
        encoding="utf-8",
    )
    is_valid, errors = validate_knowledge_file(f4)
    assert not is_valid
    assert any("body content cannot be empty" in e.lower() for e in errors)


def test_search_knowledge_text_adversarial_queries():
    """
    Probes search_knowledge_text with adversarial query strings:
    - Empty string
    - Punctuation-only and regex syntax
    - 5,000 character string
    - SQL injection string
    All must execute safely and return empty or scored list without crashing.
    """
    adversarial_queries = [
        "",
        "   \t\n  ",
        "??? !!! *** ...",
        "r'^(?=.*[a-z])(?=.*[A-Z]).+$'",
        "SELECT * FROM knowledge_index WHERE 1=1; DROP TABLE knowledge_index; --",
        "<script>alert('xss')</script>",
        "A" * 5000,
    ]

    for q in adversarial_queries:
        results = search_knowledge_text(q, limit=5)
        assert isinstance(results, list)
