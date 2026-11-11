"""
test_adversarial_stress_m1_it3.py — Milestone 1 Iteration 3 Empirical Stress & Concurrency Harness.

Authored by: Milestone 1 Challenger 1 (teamwork_challenger_m1_it3_1)

Adversarially stress-tests:
1. 100-worker high-concurrency CAS task races, lease stealing, and fencing token validation.
2. Concurrent MessageBus / MessageLogger JSONL streaming and reader/writer isolation.
3. Exhaustive boundary probes on `_is_null_or_dummy()` with weird whitespace, mixed quotes,
   escaped characters, multi-nested brackets, and frontmatter citation bypass vectors.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import pytest

from agent.modules.knowledge import (
    ConfidenceLevel as KbConfidenceLevel,
    DUMMY_NULL_VALUES,
    KnowledgeCategory,
    KnowledgeEntry,
    KnowledgeMetadata,
    VerificationStatus,
    _is_null_or_dummy,
    _normalize_string_list,
    _parse_yaml_frontmatter,
    _serialize_yaml_frontmatter,
    get_knowledge,
    parse_knowledge_file,
    query_knowledge,
    search_knowledge_text,
    sync_to_db,
    validate_knowledge_file,
    write_knowledge,
)
from agent.modules.orchestration import (
    AgentAction,
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


# ==============================================================================
# SECTION 1: HIGH-CONCURRENCY CAS TASK LOCKING STRESS (50-100 THREADS)
# ==============================================================================

class TestHighConcurrencyTaskLocking:
    """100-thread empirical stress testing of TaskManager CAS invariants."""

    def test_100_threads_competing_for_single_pending_task(self, tmp_path: Path):
        """
        Adversarial Scenario:
        100 concurrent worker threads attempt to claim the exact same pending task simultaneously.
        Invariant:
        Exactly ONE thread must succeed with a valid UUID token.
        Exactly 99 threads must receive TaskCollisionError.
        The task in SQLite must be IN_PROGRESS and owned by the winning thread.
        """
        db_path = tmp_path / "cas_100_race.sqlite"
        task_md = tmp_path / "task-ownership.md"
        manager = TaskManager(db_path=db_path, task_md=task_md)

        # Seed 1 pending task
        with manager._get_conn() as conn:
            conn.execute(
                "INSERT INTO task_registry (task_id, task_name, owner, status) VALUES (?, ?, NULL, 'PENDING')",
                ("TASK-CONCURRENT-100", "100 Worker Race Task")
            )
            conn.commit()

        start_gate = threading.Barrier(100)
        tokens: List[Tuple[str, str]] = []  # (worker_id, token)
        collisions: List[Tuple[str, Exception]] = []
        errors: List[Exception] = []

        def worker_task(worker_id: int):
            agent_id = f"agent-worker-{worker_id:03d}"
            try:
                start_gate.wait(timeout=10.0)
                tok = manager.claim_task("TASK-CONCURRENT-100", agent_id=agent_id, lease_seconds=120)
                tokens.append((agent_id, tok))
            except TaskCollisionError as exc:
                collisions.append((agent_id, exc))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker_task, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)

        assert len(errors) == 0, f"Unexpected errors during 100-thread claim: {errors}"
        assert len(tokens) == 1, f"Expected exactly 1 winner, but got {len(tokens)} winners: {tokens}"
        assert len(collisions) == 99, f"Expected 99 collisions, got {len(collisions)}"

        winner_agent, winner_token = tokens[0]
        task_record = manager.get_task("TASK-CONCURRENT-100")
        assert task_record is not None
        assert task_record["status"] == "IN_PROGRESS"
        assert task_record["owner"] == winner_agent
        assert task_record["lock_token"] == winner_token

    def test_100_threads_racing_across_20_distinct_tasks(self, tmp_path: Path):
        """
        Adversarial Scenario:
        20 pending tasks exist. 100 threads (5 threads per task) race to claim them.
        Invariant:
        Every one of the 20 tasks must be claimed by exactly ONE worker.
        Exactly 20 claims succeed; exactly 80 claims raise TaskCollisionError.
        Zero deadlocks, zero double claims.
        """
        db_path = tmp_path / "cas_20_tasks.sqlite"
        task_md = tmp_path / "task-ownership.md"
        manager = TaskManager(db_path=db_path, task_md=task_md)

        with manager._get_conn() as conn:
            for i in range(20):
                conn.execute(
                    "INSERT INTO task_registry (task_id, task_name, owner, status) VALUES (?, ?, NULL, 'PENDING')",
                    (f"TASK-BATCH-{i:02d}", f"Batch Task {i:02d}")
                )
            conn.commit()

        start_gate = threading.Barrier(100)
        successes: List[Tuple[str, str, str]] = []  # (task_id, agent_id, token)
        lock = threading.Lock()
        collision_count = 0
        error_list: List[Exception] = []

        def worker_task(worker_id: int):
            task_idx = worker_id % 20
            task_id = f"TASK-BATCH-{task_idx:02d}"
            agent_id = f"agent-batch-worker-{worker_id:03d}"
            nonlocal collision_count
            try:
                start_gate.wait(timeout=10.0)
                tok = manager.claim_task(task_id, agent_id=agent_id, lease_seconds=60)
                with lock:
                    successes.append((task_id, agent_id, tok))
            except TaskCollisionError:
                with lock:
                    collision_count += 1
            except Exception as exc:
                with lock:
                    error_list.append(exc)

        threads = [threading.Thread(target=worker_task, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20.0)

        assert len(error_list) == 0, f"Errors occurred: {error_list}"
        assert len(successes) == 20, f"Expected 20 successful claims, got {len(successes)}"
        assert collision_count == 80, f"Expected 80 collisions, got {collision_count}"

        claimed_tasks = {s[0] for s in successes}
        assert len(claimed_tasks) == 20, "Not all 20 distinct tasks were claimed!"

    def test_expired_lease_stealing_under_high_concurrency(self, tmp_path: Path):
        """
        Adversarial Scenario:
        A task is IN_PROGRESS but its lease has expired in the past.
        50 threads simultaneously attempt to claim/steal the expired task.
        Invariant:
        Exactly ONE worker successfully steals the expired lease and receives a new token.
        Exactly 49 workers receive TaskCollisionError.
        """
        db_path = tmp_path / "cas_expired_steal.sqlite"
        task_md = tmp_path / "task-ownership.md"
        manager = TaskManager(db_path=db_path, task_md=task_md)

        # Insert task locked in the past (expired lease)
        past_time = "2020-01-01 00:00:00"
        with manager._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO task_registry (
                    task_id, task_name, owner, status, lock_token, locked_at, lease_expires_at
                ) VALUES (?, ?, 'old-stale-agent', 'IN_PROGRESS', 'old-stale-token-1234', ?, ?)
                """,
                ("TASK-EXPIRED-STEAL", "Expired Task", past_time, past_time)
            )
            conn.commit()

        start_gate = threading.Barrier(50)
        stolen_tokens: List[Tuple[str, str]] = []
        collisions: List[str] = []
        errors: List[Exception] = []
        lock = threading.Lock()

        def worker_task(worker_id: int):
            agent_id = f"agent-thief-{worker_id:03d}"
            try:
                start_gate.wait(timeout=10.0)
                new_tok = manager.claim_task("TASK-EXPIRED-STEAL", agent_id=agent_id, lease_seconds=120)
                with lock:
                    stolen_tokens.append((agent_id, new_tok))
            except TaskCollisionError:
                with lock:
                    collisions.append(agent_id)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker_task, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)

        assert len(errors) == 0, f"Unexpected errors: {errors}"
        assert len(stolen_tokens) == 1, f"Expected exactly 1 thief to steal the lease, got {len(stolen_tokens)}"
        assert len(collisions) == 49, f"Expected 49 collisions, got {len(collisions)}"

        new_owner, new_token = stolen_tokens[0]
        assert new_token != "old-stale-token-1234"
        task_row = manager.get_task("TASK-EXPIRED-STEAL")
        assert task_row["owner"] == new_owner
        assert task_row["lock_token"] == new_token

    def test_stale_token_rejection_matrix(self, tmp_path: Path):
        """
        Adversarial Scenario:
        Verify that passing invalid, empty, or outdated fencing tokens is strictly rejected
        across ALL mutation methods: heartbeat, complete_task, release_task, block_task, fail_task.
        """
        db_path = tmp_path / "stale_token_matrix.sqlite"
        task_md = tmp_path / "task-ownership.md"
        manager = TaskManager(db_path=db_path, task_md=task_md)

        with manager._get_conn() as conn:
            conn.execute(
                "INSERT INTO task_registry (task_id, task_name, owner, status) VALUES (?, ?, NULL, 'PENDING')",
                ("TASK-TOKEN-TEST", "Token Integrity Task")
            )
            conn.commit()

        real_token = manager.claim_task("TASK-TOKEN-TEST", agent_id="agent-legit", lease_seconds=300)
        bad_tokens = ["", "forged-uuid-9999", "null", real_token[:-1] + "x"]

        for bad in bad_tokens:
            with pytest.raises(StaleLockTokenError):
                manager.heartbeat("TASK-TOKEN-TEST", agent_id="agent-legit", token=bad)

            with pytest.raises(StaleLockTokenError):
                manager.block_task("TASK-TOKEN-TEST", agent_id="agent-legit", token=bad, reason="test")

            with pytest.raises(StaleLockTokenError):
                manager.fail_task("TASK-TOKEN-TEST", agent_id="agent-legit", token=bad, error="test")

            with pytest.raises(StaleLockTokenError):
                manager.release_task("TASK-TOKEN-TEST", agent_id="agent-legit", token=bad)

            with pytest.raises(StaleLockTokenError):
                manager.complete_task("TASK-TOKEN-TEST", agent_id="agent-legit", token=bad, artifact_path="art.md")

        # Wrong agent with real token must also fail!
        with pytest.raises(StaleLockTokenError):
            manager.complete_task("TASK-TOKEN-TEST", agent_id="agent-imposter", token=real_token, artifact_path="art.md")

        # Now legitimate agent completes with real token
        manager.complete_task("TASK-TOKEN-TEST", agent_id="agent-legit", token=real_token, artifact_path="valid.md")
        rec = manager.get_task("TASK-TOKEN-TEST")
        assert rec["status"] == "COMPLETED"
        assert rec["artifact_path"] == "valid.md"


# ==============================================================================
# SECTION 2: CONCURRENT MESSAGE BUS & JSONL STREAMING
# ==============================================================================

class TestMessageBusConcurrentStreaming:
    """Multi-threaded stress testing of MessageBus and MessageLogger JSONL streaming."""

    def test_50_threads_concurrent_logging_zero_dropped_zero_corruption(self, tmp_path: Path):
        """
        Adversarial Scenario:
        50 threads concurrently send 20 messages each (1000 total messages) through MessageBus.
        Simultaneously, 5 reader threads continuously execute read_messages().
        Invariants:
        1. All 1000 messages must be written to logs/agent-comms.jsonl.
        2. No line corruption or partial writes (torn lines).
        3. No reader threads encounter JSONDecodeError.
        """
        log_path = tmp_path / "comms_stress.jsonl"
        bus = MessageBus(log_path=log_path)

        stop_readers = threading.Event()
        reader_errors: List[Exception] = []
        read_counts: List[int] = []

        def reader_loop():
            local_reads = 0
            while not stop_readers.is_set():
                try:
                    msgs = bus.logger.read_messages()
                    local_reads += len(msgs)
                except Exception as exc:
                    reader_errors.append(exc)
                time.sleep(0.005)
            read_counts.append(local_reads)

        readers = [threading.Thread(target=reader_loop) for _ in range(5)]
        for r in readers:
            r.start()

        writer_errors: List[Exception] = []
        start_gate = threading.Barrier(50)

        def writer_task(thread_id: int):
            try:
                start_gate.wait(timeout=10.0)
                for msg_idx in range(20):
                    msg = AgentMessage(
                        sender=AgentID.AGENT_1_RESEARCH.value,
                        recipient=AgentID.AGENT_2_ANTI_SLOP.value,
                        task_id=f"TASK-STREAM-{thread_id:02d}",
                        action=AgentAction.TASK_PROGRESS.value,
                        payload={"thread": thread_id, "seq": msg_idx, "payload": "x" * 50},
                        confidence=ConfidenceLevel.EXPERIMENTAL.value,
                    )
                    bus.dispatch(msg)
            except Exception as exc:
                writer_errors.append(exc)

        writers = [threading.Thread(target=writer_task, args=(i,)) for i in range(50)]
        for w in writers:
            w.start()
        for w in writers:
            w.join(timeout=20.0)

        stop_readers.set()
        for r in readers:
            r.join(timeout=10.0)

        assert len(writer_errors) == 0, f"Writer errors occurred: {writer_errors}"
        assert len(reader_errors) == 0, f"Reader encountered JSON corruption: {reader_errors}"

        # Verify all 1000 messages exist and parse cleanly
        all_msgs = bus.logger.read_messages()
        assert len(all_msgs) == 1000, f"Expected 1000 logged messages, found {len(all_msgs)}"

        # Verify line-by-line file integrity directly
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 1000, f"Expected 1000 physical JSONL lines, found {len(lines)}"

        for line_no, raw in enumerate(lines, start=1):
            parsed = json.loads(raw)
            assert "message_id" in parsed
            assert "sender" in parsed
            assert "recipient" in parsed


# ==============================================================================
# SECTION 3: BOUNDARY ATTACKS ON CITATION GATING & NULL SANITIZATION
# ==============================================================================

class TestCitationGateAdversarialBoundaries:
    """
    Exhaustively probe _is_null_or_dummy and validate_knowledge_file against adversarial
    unicode whitespaces, quotes, nested brackets, and dummy tokens.
    """

    @pytest.mark.parametrize(
        "val,expected_null",
        [
            # Standard singletons & dummies
            (None, True),
            ("", True),
            ("   ", True),
            ("none", True),
            ("None", True),
            ("NONE", True),
            ("null", True),
            ("Null", True),
            ("NULL", True),
            ("~", True),
            ("nil", True),
            ("NIL", True),
            ("undefined", True),
            ("UNDEFINED", True),
            ("n/a", True),
            ("N/A", True),
            ("na", True),
            ("NA", True),
            # Weird unicode whitespaces
            ("\u00a0", True),        # non-breaking space
            ("\u200b", False),       # zero-width space is not stripped by standard strip()
            ("\t\r\n  \t", True),    # mixed escape whitespaces
            # Single-bracket sequences
            ("[]", True),
            ("[ ]", True),
            ("[   ]", True),
            ("[~]", True),
            ("['~']", True),
            ('["~"]', True),
            ("[none]", True),
            ("['none']", True),
            ('["none"]', True),
            ("[null]", True),
            ("['null']", True),
            ('["null"]', True),
            ("['']", True),
            ('[""]', True),
            ("[ ' ' ]", True),
            ('[ " " ]', True),
            # Quoted scalars
            ("''", True),
            ('""', True),
            ("'  '", True),
            ('"  "', True),
            ("'~'", True),
            ('"~"', True),
            ("'none'", True),
            ('"none"', True),
            ("'null'", True),
            ('"null"', True),
            # Valid legitimate citations (MUST NOT BE NULL)
            ("https://youtube.com/creators", False),
            ("https://tiktok.com", False),
            ("YouTube Creator Liaison 2024", False),
            ("Internal Benchmark Q3 2024", False),
            ("arxiv:2401.0001", False),
        ],
    )
    def test_is_null_or_dummy_unit_behavior(self, val: Any, expected_null: bool):
        result = _is_null_or_dummy(val)
        assert result is expected_null, f"_is_null_or_dummy({val!r}) returned {result}, expected {expected_null}"

    @pytest.mark.parametrize(
        "nested_bracket_input",
        [
            "[[~]]",
            "[[[~]]]",
            "[[none]]",
            "[[[none]]]",
            "[[NONE]]",
            "[[null]]",
            "[[[null]]]",
            "[[NULL]]",
            "[[ 'none' ]]",
            '[[ "none" ]]',
            "[[ '' ]]",
            '[[ "" ]]',
            "[[  ]]",
            "[[]]",
            "[[[[]]]]",
        ],
    )
    def test_nested_brackets_in_string_list_and_frontmatter(self, tmp_path: Path, nested_bracket_input: str):
        """
        Probe whether multi-nested brackets can bypass _normalize_string_list
        or validate_knowledge_file for a VERIFIED entry.
        """
        # Test 1: _normalize_string_list
        norm = _normalize_string_list(nested_bracket_input)

        # Test 2: Full file validation with VERIFIED status
        probe_file = tmp_path / f"probe_{abs(hash(nested_bracket_input))}.md"
        probe_file.write_text(
            f"---\n"
            f"id: probe-nested-{abs(hash(nested_bracket_input))}\n"
            f"title: Adversarial Nested Probe\n"
            f"category: youtube\n"
            f"verification_status: VERIFIED\n"
            f"confidence: 0.95\n"
            f"sources: {nested_bracket_input}\n"
            f"tags: [test, adversarial]\n"
            f"---\n"
            f"Content claiming verification without valid citations.\n",
            encoding="utf-8"
        )
        is_valid, errors = validate_knowledge_file(probe_file)

        # A VERIFIED document MUST NOT be valid if its only sources are nested dummy brackets!
        assert is_valid is False, (
            f"CRITICAL VULNERABILITY: Nested bracket {nested_bracket_input!r} bypassed citation gating!\n"
            f"Normalized to: {norm}\n"
            f"validate_knowledge_file errors: {errors}"
        )
        assert any("verifiable source citation" in err for err in errors), (
            f"Expected citation error for {nested_bracket_input!r}, got: {errors}"
        )

    def test_programmatic_metadata_with_dummy_combinations(self):
        """
        Probe whether programmatic construction of KnowledgeMetadata with various
        dummy collections can bypass citation enforcement.
        """
        dummy_combos = [
            [],
            [""],
            ["   "],
            ["~"],
            ["none"],
            ["NONE"],
            ["null"],
            ["NULL"],
            ["nil"],
            ["undefined"],
            ["n/a"],
            ["[]"],
            ["[ ]"],
            ["[~]"],
            ["[none]"],
            ["[null]"],
            ["", "  ", "~", "none", "null"],
            ["'~'", '"~"'],
        ]

        for combo in dummy_combos:
            meta = KnowledgeMetadata(
                id="test-dummy-combo",
                title="Dummy Combo Test",
                category=KnowledgeCategory.YOUTUBE,
                verification_status=VerificationStatus.VERIFIED,
                confidence=0.90,
                sources=combo,
                tags=["test"],
            )
            # The normalized sources should contain NO authentic citations
            valid_sources = [s for s in meta.sources if isinstance(s, str) and not _is_null_or_dummy(s)]
            assert len(valid_sources) == 0, (
                f"Combo {combo!r} produced valid sources: {valid_sources} (meta.sources: {meta.sources})"
            )

    @pytest.mark.parametrize(
        "raw_yaml_sources_block",
        [
            "sources: null",
            "sources: Null",
            "sources: NULL",
            "sources: ~",
            "sources: none",
            "sources: NONE",
            "sources: nil",
            "sources: undefined",
            "sources: ''",
            'sources: ""',
            "sources: '   '",
            'sources: "   "',
            "sources: []",
            "sources: [ ]",
            "sources: [~]",
            "sources: [none]",
            "sources: [NONE]",
            "sources: [Null]",
            "sources: [NULL]",
            "sources: ['~']",
            'sources: ["~"]',
            "sources: ['none']",
            'sources: ["none"]',
            "sources: ['null']",
            'sources: ["null"]',
            "sources:\n  - ''",
            "sources:\n  - '  '",
            "sources:\n  - ~",
            "sources:\n  - null",
            "sources:\n  - Null",
            "sources:\n  - none",
            "sources:\n  - NONE",
            "sources:\n  - [~]",
            "sources:\n  - [none]",
            "sources:\n  - '[]'",
        ]
    )
    def test_all_yaml_block_variations_fail_for_verified(self, tmp_path: Path, raw_yaml_sources_block: str):
        """Exhaustive check that all frontmatter variations fail citation validation."""
        f = tmp_path / "block_probe.md"
        f.write_text(
            f"---\n"
            f"id: kb-block-probe\n"
            f"title: Block Probe Title\n"
            f"category: youtube\n"
            f"verification_status: VERIFIED\n"
            f"confidence: 0.95\n"
            f"{raw_yaml_sources_block}\n"
            f"tags: [shorts]\n"
            f"---\n"
            f"Body text claiming verification.\n",
            encoding="utf-8"
        )
        is_valid, errors = validate_knowledge_file(f)
        assert is_valid is False, f"Bypass succeeded for block:\n{raw_yaml_sources_block}\nErrors: {errors}"
        assert any("verifiable source citation" in err for err in errors), f"Missing citation error for {raw_yaml_sources_block}: {errors}"


# ==============================================================================
# SECTION 4: KNOWLEDGE BASE SCHEMA & RETRIEVAL RESILIENCE
# ==============================================================================

class TestKnowledgeBaseResilience:
    """Stress tests on query, search, and SQLite indexing resilience."""

    def test_search_knowledge_special_characters_no_sql_injection(self, tmp_path: Path):
        """
        Verify search_knowledge_text and sync_to_db handle SQL injection attempts,
        special characters, single quotes, double quotes, semicolons, and wildcards.
        """
        db_path = tmp_path / "search_sec.sqlite"
        kb_root = tmp_path / "knowledge"
        cat_dir = kb_root / "youtube"
        cat_dir.mkdir(parents=True, exist_ok=True)

        entry_file = cat_dir / "safe-entry.md"
        entry_file.write_text(
            "---\n"
            "id: safe-entry-01\n"
            "title: Safe Title with Quotes ' and \" and semicolons ;\n"
            "category: youtube\n"
            "verification_status: HYPOTHESIS\n"
            "confidence: 0.75\n"
            "tags: [youtube, test, drop-table]\n"
            "---\n"
            "Robert'); DROP TABLE knowledge_index;-- test payload in body text.\n",
            encoding="utf-8"
        )

        conn = sqlite3.connect(db_path)
        synced = sync_to_db(conn=conn, kb_root=kb_root, db_path=db_path)
        assert synced == 1

        # Search with adversarial SQL injection strings
        injections = [
            "' OR 1=1 --",
            "'; DROP TABLE knowledge_index; --",
            "' UNION SELECT * FROM knowledge_index --",
            "Robert'); DROP TABLE",
            "%",
            "_",
            "\\",
            "''",
            '""',
        ]

        for inj in injections:
            res = search_knowledge_text(inj, kb_root=kb_root)
            # Must return a valid list (results or empty) and never crash or drop table!
            assert isinstance(res, list)

        # Verify table still exists and data is intact
        cur = conn.execute("SELECT COUNT(*) FROM knowledge_index")
        assert cur.fetchone()[0] == 1
        conn.close()

    def test_write_knowledge_overwrite_guard(self, tmp_path: Path):
        """Verify write_knowledge respects overwrite=False flag."""
        kb_root = tmp_path / "knowledge"
        meta = KnowledgeMetadata(
            id="guard-test",
            title="Guard Test Entry",
            category=KnowledgeCategory.RESEARCH,
            verification_status=VerificationStatus.HYPOTHESIS,
            confidence=0.70,
            tags=["guard"],
        )
        entry = KnowledgeEntry(metadata=meta, content="Initial body")

        # First write succeeds
        p1 = write_knowledge(entry, overwrite=False, kb_root=kb_root)
        assert p1.exists()

        # Second write with overwrite=False must raise FileExistsError
        entry2 = KnowledgeEntry(metadata=meta, content="Updated body")
        with pytest.raises(FileExistsError):
            write_knowledge(entry2, overwrite=False, kb_root=kb_root)

        # With overwrite=True, succeeds
        p2 = write_knowledge(entry2, overwrite=True, kb_root=kb_root)
        assert p2.read_text(encoding="utf-8").strip().endswith("Updated body")
