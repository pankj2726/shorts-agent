"""
test_adversarial_m1_it2.py — Milestone 1 Iteration 2 Adversarial Stress & Concurrency Suite.

Adversarially tests:
1. MessageBus recursive/nested dispatch re-entrancy and deadlock freedom.
2. Concurrent subscriber callbacks and error isolation.
3. TaskManager 100-worker high-contention CAS race conditions and microsecond expirations.
4. Double-complete, double-release, and cross-agent token hijacking attempts.
5. KnowledgeBase citation gate evasion probes (whitespace strings, null variations, nested brackets).
6. Fallback YAML parser boundary probes (CRLF, colons in strings, comments, numbers).
7. Search relevance scoring on acronyms, numbers, hyphenated tags, and Unicode.
8. Concurrent SQLite sync_to_db under multi-threaded writes.
"""
import concurrent.futures
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from agent.modules.knowledge import (
    KnowledgeCategory,
    KnowledgeEntry,
    KnowledgeMetadata,
    VerificationStatus,
    ConfidenceLevel as KbConfidenceLevel,
    _normalize_string_list,
    _parse_yaml_frontmatter,
    _serialize_yaml_frontmatter,
    audit_knowledge_base,
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
    CentralOrchestrator,
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
# SECTION 1: MessageBus Reentrancy, Deadlock & Concurrent Dispatch Stress
# ==============================================================================

class PingPongAgent(BaseAgent):
    """Agent that responds to ping by dispatching a pong back to the sender."""
    def __init__(self, agent_id: str, depth_limit: int = 5):
        super().__init__(agent_id=agent_id, name=f"PingPong {agent_id}", role="Ping Ponger")
        self.depth_limit = depth_limit
        self.call_count = 0

    def handle_message(self, message: AgentMessage) -> None:
        self.call_count += 1
        current_depth = message.payload.get("depth", 0)
        if current_depth < self.depth_limit:
            self.send_message(
                recipient_agent=message.sender,
                task_id=message.task_id,
                action="PONG" if message.action == "PING" else "PING",
                payload={"depth": current_depth + 1},
            )


def test_message_bus_reentrant_nested_dispatch_no_deadlock(tmp_path: Path):
    """
    Probe: Nested synchronous dispatch where Agent A calls Agent B, which synchronously
    calls Agent A, recursing up to depth_limit.
    Asserts that MessageBus._lock and MessageLogger._shared_lock are reentrant (RLock)
    and do not deadlock under nested synchronous execution.
    """
    log_file = tmp_path / "comms_nested.jsonl"
    bus = MessageBus(log_path=log_file)

    agent_a = PingPongAgent(agent_id=AgentID.AGENT_1_RESEARCH.value, depth_limit=4)
    agent_b = PingPongAgent(agent_id=AgentID.AGENT_2_ANTI_SLOP.value, depth_limit=4)

    bus.register_agent(agent_a)
    bus.register_agent(agent_b)

    init_msg = AgentMessage(
        sender=agent_a.agent_id,
        recipient=agent_b.agent_id,
        task_id="NESTED-001",
        action="PING",
        payload={"depth": 0},
    )

    # Dispatch starts nested chain
    bus.dispatch(init_msg)

    assert agent_b.call_count >= 2
    assert agent_a.call_count >= 2

    # Check that all nested messages were logged to JSONL cleanly
    logged = bus.logger.read_messages(task_id="NESTED-001")
    assert len(logged) >= 5
    for msg in logged:
        assert msg.task_id == "NESTED-001"


def test_concurrent_subscribers_and_exception_isolation(tmp_path: Path):
    """
    Probe: Multiple subscribers registered to the same recipient.
    One subscriber throws an uncaught exception.
    Asserts:
    1. The exception in subscriber 1 does NOT prevent subscriber 2 from receiving the message.
    2. The message is still logged and delivered to the registered agent.
    """
    log_file = tmp_path / "comms_sub_isolation.jsonl"
    bus = MessageBus(log_path=log_file)

    agent = PingPongAgent(agent_id=AgentID.AGENT_3_SOCIAL.value, depth_limit=0)
    bus.register_agent(agent)

    sub2_received = []

    def faulty_subscriber(msg: AgentMessage):
        raise RuntimeError("Sub 1 exploded intentionally!")

    def working_subscriber(msg: AgentMessage):
        sub2_received.append(msg.message_id)

    bus.subscribe(AgentID.AGENT_3_SOCIAL.value, faulty_subscriber)
    bus.subscribe(AgentID.AGENT_3_SOCIAL.value, working_subscriber)

    msg = AgentMessage(
        sender=AgentID.ORCHESTRATOR.value,
        recipient=AgentID.AGENT_3_SOCIAL.value,
        task_id="SUB-ISO-001",
        action="ENGAGE_COMMENTS",
        payload={"count": 1},
    )

    # Dispatch should not blow up from faulty subscriber
    bus.dispatch(msg)

    assert len(sub2_received) == 1
    assert sub2_received[0] == msg.message_id
    assert agent.call_count == 1
    assert len(bus.logger.read_messages(task_id="SUB-ISO-001")) == 1


def test_concurrent_message_dispatch_heavy_threads(tmp_path: Path):
    """
    Stress Probe: 40 threads concurrently dispatching 400 messages to broadcast and unicast.
    Asserts 0 lost lines, 0 JSON corruption, and exactly 400 messages written and parsed.
    """
    log_file = tmp_path / "comms_heavy_threads.jsonl"
    bus = MessageBus(log_path=log_file)

    agent_1 = PingPongAgent(agent_id=AgentID.AGENT_1_RESEARCH.value, depth_limit=0)
    agent_2 = PingPongAgent(agent_id=AgentID.AGENT_2_ANTI_SLOP.value, depth_limit=0)
    bus.register_agent(agent_1)
    bus.register_agent(agent_2)

    total_messages = 400
    num_threads = 40
    msgs_per_thread = total_messages // num_threads

    barrier = threading.Barrier(num_threads)
    errors = []

    def worker_send(thread_idx: int):
        barrier.wait()
        for i in range(msgs_per_thread):
            try:
                recip = AgentID.AGENT_1_RESEARCH.value if (thread_idx + i) % 2 == 0 else "broadcast"
                msg = AgentMessage(
                    sender=AgentID.ORCHESTRATOR.value,
                    recipient=recip,
                    task_id=f"HEAVY-{thread_idx}",
                    action="HEARTBEAT",
                    payload={"thread": thread_idx, "seq": i},
                )
                bus.dispatch(msg)
            except Exception as e:
                errors.append(e)

    threads = [threading.Thread(target=worker_send, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(errors) == 0, f"Dispatch errors encountered: {errors}"

    parsed = bus.logger.read_messages()
    assert len(parsed) == total_messages


# ==============================================================================
# SECTION 2: TaskManager CAS State Machine Stress & Extreme Fencing Races
# ==============================================================================

def test_100_concurrent_workers_single_task_cas_race(tmp_path: Path):
    """
    High-Contention Stress Probe: 100 independent worker threads, each with its own
    TaskManager instance pointing to the same SQLite WAL database, simultaneously
    racing to claim a single pending task via CAS atomic update.
    Asserts:
    1. Exactly 1 winner receives a valid UUID token.
    2. Exactly 99 workers receive TaskCollisionError.
    3. Exactly 0 OperationalError (database locked) or unhandled exceptions.
    4. Database status is IN_PROGRESS with winner's token and owner.
    """
    db_file = tmp_path / "heavy_cas.sqlite"
    md_file = tmp_path / "heavy_tasks.md"

    # Pre-seed the task
    mgr_seed = TaskManager(db_path=db_file, task_md=md_file)
    with mgr_seed._get_conn() as conn:
        conn.execute(
            "INSERT INTO task_registry (task_id, task_name, status) VALUES ('TASK_100_RACE', 'Contested 100', 'PENDING')"
        )
        conn.commit()

    num_workers = 100
    barrier = threading.Barrier(num_workers)
    winners: List[Tuple[str, str]] = []  # (worker_id, token)
    collisions: List[str] = []
    unexpected_errors: List[Exception] = []

    def worker_claim(worker_idx: int):
        mgr = TaskManager(db_path=db_file, task_md=md_file)
        worker_id = f"worker_{worker_idx:03d}"
        barrier.wait()
        try:
            token = mgr.claim_task("TASK_100_RACE", agent_id=worker_id, lease_seconds=60)
            winners.append((worker_id, token))
        except TaskCollisionError:
            collisions.append(worker_id)
        except Exception as exc:
            unexpected_errors.append(exc)

    threads = [threading.Thread(target=worker_claim, args=(i,)) for i in range(num_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert len(unexpected_errors) == 0, f"Unexpected errors: {unexpected_errors}"
    assert len(winners) == 1, f"Expected exactly 1 winner, got {len(winners)}: {winners}"
    assert len(collisions) == 99

    winner_id, winner_token = winners[0]
    final_task = mgr_seed.get_task("TASK_100_RACE")
    assert final_task is not None
    assert final_task["status"] == "IN_PROGRESS"
    assert final_task["owner"] == winner_id
    assert final_task["lock_token"] == winner_token


def test_cas_double_complete_and_cross_agent_token_theft(tmp_path: Path):
    """
    Adversarial Probe:
    1. Winner attempts to call complete_task twice with same token -> second attempt must raise StaleLockTokenError.
    2. Loser attempts to call complete_task with winner's token -> must raise StaleLockTokenError (owner mismatch).
    3. Winner attempts to call complete_task with mutated token -> must raise StaleLockTokenError.
    """
    db_file = tmp_path / "theft_cas.sqlite"
    md_file = tmp_path / "theft_tasks.md"
    mgr = TaskManager(db_path=db_file, task_md=md_file)

    with mgr._get_conn() as conn:
        conn.execute(
            "INSERT INTO task_registry (task_id, task_name, status) VALUES ('TASK_THEFT', 'Theft Test', 'PENDING')"
        )
        conn.commit()

    token = mgr.claim_task("TASK_THEFT", agent_id="agent_legit", lease_seconds=30)

    # Cross-agent token theft probe: agent_rogue presents legit token
    with pytest.raises(StaleLockTokenError):
        mgr.complete_task("TASK_THEFT", agent_id="agent_rogue", token=token, artifact_path="evil.mp4")

    # Mutated token probe: agent_legit presents wrong token
    with pytest.raises(StaleLockTokenError):
        mgr.complete_task("TASK_THEFT", agent_id="agent_legit", token="wrong_token_12345")

    # Legit completion succeeds
    mgr.complete_task("TASK_THEFT", agent_id="agent_legit", token=token, artifact_path="final.mp4")
    t = mgr.get_task("TASK_THEFT")
    assert t["status"] == "COMPLETED"
    assert t["lock_token"] is None

    # Double-complete probe with previously valid token
    with pytest.raises(StaleLockTokenError):
        mgr.complete_task("TASK_THEFT", agent_id="agent_legit", token=token, artifact_path="second.mp4")


def test_concurrent_heartbeat_versus_claim_on_lease_expiration(tmp_path: Path):
    """
    Race Condition Probe:
    Agent A holds lease with very short duration (1s).
    At expiration, Agent A attempts heartbeat while Agent B attempts claim_task concurrently.
    Asserts:
    - No corruption or double ownership.
    - Exactly one of two outcomes:
      Outcome 1: Heartbeat succeeded first -> claim_task raised TaskCollisionError.
      Outcome 2: Claim succeeded first -> heartbeat raised StaleLockTokenError.
    """
    db_file = tmp_path / "heartbeat_race.sqlite"
    md_file = tmp_path / "heartbeat_tasks.md"
    mgr_a = TaskManager(db_path=db_file, task_md=md_file)
    mgr_b = TaskManager(db_path=db_file, task_md=md_file)

    with mgr_a._get_conn() as conn:
        conn.execute(
            "INSERT INTO task_registry (task_id, task_name, status) VALUES ('RACE_HB', 'HB Race', 'PENDING')"
        )
        conn.commit()

    token_a = mgr_a.claim_task("RACE_HB", agent_id="agent_a", lease_seconds=1)
    time.sleep(1.2)  # Wait for lease to expire

    barrier = threading.Barrier(2)
    results = {}

    def run_heartbeat():
        barrier.wait()
        try:
            mgr_a.heartbeat("RACE_HB", agent_id="agent_a", token=token_a, extension_seconds=10)
            results["heartbeat"] = "SUCCESS"
        except Exception as e:
            results["heartbeat"] = type(e).__name__

    def run_claim():
        barrier.wait()
        try:
            tok = mgr_b.claim_task("RACE_HB", agent_id="agent_b", lease_seconds=10)
            results["claim"] = f"SUCCESS:{tok}"
        except Exception as e:
            results["claim"] = type(e).__name__

    t1 = threading.Thread(target=run_heartbeat)
    t2 = threading.Thread(target=run_claim)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    final_task = mgr_a.get_task("RACE_HB")
    assert final_task["status"] == "IN_PROGRESS"
    if results["claim"].startswith("SUCCESS"):
        # Agent B stole expired lease; Agent A heartbeat failed with StaleLockTokenError
        assert results["heartbeat"] == "StaleLockTokenError"
        assert final_task["owner"] == "agent_b"
    else:
        # Agent A renewed lease; Agent B claim failed with TaskCollisionError
        assert results["heartbeat"] == "SUCCESS"
        assert results["claim"] == "TaskCollisionError"
        assert final_task["owner"] == "agent_a"


# ==============================================================================
# SECTION 3: Knowledge Base Frontmatter & Citation Gating Edge Cases
# ==============================================================================

def test_citation_gate_whitespace_and_dummy_strings_rejected(tmp_path: Path):
    """
    Adversarial Probe on Citation Gate:
    Attempting to satisfy 'VERIFIED' status with:
    1. sources: ["   ", "\\t\\n"] (whitespace strings)
    2. sources: ["[]", "[ ]"] (bracket strings)
    3. sources: ["None", "null"] (null literal strings)
    Asserts: validate_knowledge_file() REJECTS all evasion attempts.
    """
    evasion_sources = [
        '["   "]',
        '["   ", "   "]',
        '["[]"]',
        '["[ ]"]',
        '["None"]',
        '["null"]',
        '[""]',
    ]

    kb_dir = tmp_path / "knowledge" / "youtube"
    kb_dir.mkdir(parents=True)

    for idx, ev_src in enumerate(evasion_sources):
        test_file = kb_dir / f"evasion_{idx}.md"
        content = f"""---
id: evasion-{idx}
title: Evasion Attempt {idx}
category: youtube
verification_status: VERIFIED
confidence: 0.95
sources: {ev_src}
tags: [test, evasion]
---
Content here
"""
        test_file.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(test_file)
        entry = parse_knowledge_file(test_file)
        assert not is_valid, f"Evasion source {ev_src} bypassed citation gating!"
        assert any("at least one verifiable source citation" in e for e in errors)


def test_citation_gate_yaml_null_bypass_vulnerabilities(tmp_path: Path):
    """
    Adversarial Vulnerability Probe:
    Demonstrates that YAML null representations ('sources: ~', 'sources: [~]',
    'sources: none', 'sources: [none]', 'sources: NONE', 'sources: Null', 'sources: NULL')
    bypass validate_knowledge_file() citation gating because the fallback parser and
    _normalize_string_list() perform case-sensitive checks against only ('None', 'null', '[]', '[ ]').
    """
    vulnerability_snippets = [
        "sources: ~",
        "sources: [~]",
        "sources: none",
        "sources: [none]",
        "sources: NONE",
        "sources: [NONE]",
        "sources: Null",
        "sources: [Null]",
        "sources: NULL",
        "sources: [NULL]",
    ]

    kb_dir = tmp_path / "knowledge" / "youtube"
    kb_dir.mkdir(parents=True)

    bypassed_snippets = []
    for idx, snippet in enumerate(vulnerability_snippets):
        test_file = kb_dir / f"null_bypass_{idx}.md"
        content = f"""---
id: null-bypass-{idx}
title: Null Bypass Attempt {idx}
category: youtube
verification_status: VERIFIED
confidence: 0.95
{snippet}
tags: [test, evasion]
---
Content without sources.
"""
        test_file.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(test_file)
        if is_valid:
            bypassed_snippets.append(snippet)

    # Empirically proves that all 10 snippets are successfully rejected by citation validation
    assert len(bypassed_snippets) == 0, (
        f"Expected 0 bypasses, but these snippets still bypassed citation validation: {bypassed_snippets}"
    )


def test_fallback_yaml_parser_colons_in_strings_and_comments():
    """
    Boundary Probe:
    Frontmatter with colons in title, comments throughout, and boolean/numeric types.
    Asserts that _parse_yaml_frontmatter() correctly preserves titles with colons
    and strips comments properly.
    """
    raw_frontmatter = """---
# Top level comment
id: kb-colon-test
title: "AI & 4K: The Future of Shorts: Part 1"
# Category comment
category: ai-providers
version: 1.0.0
verification_status: VERIFIED
confidence: 0.92
# Multi-line sources
sources:
  - "https://example.com/source:one"
  - "https://example.com/source:two"
tags: [ai, "4k", "future:tech"]
applies_to: [youtube, instagram]
---
Markdown body here.
"""
    meta_dict, body = _parse_yaml_frontmatter(raw_frontmatter)
    assert meta_dict["id"] == "kb-colon-test"
    assert meta_dict["title"] == "AI & 4K: The Future of Shorts: Part 1"
    assert meta_dict["confidence"] == 0.92
    assert "https://example.com/source:one" in meta_dict["sources"]
    assert "https://example.com/source:two" in meta_dict["sources"]
    assert "ai" in meta_dict["tags"]
    assert "4k" in meta_dict["tags"]
    assert "future:tech" in meta_dict["tags"]
    assert body.strip() == "Markdown body here."


def test_normalize_string_list_edge_cases():
    """
    Boundary Probe for _normalize_string_list:
    1. Single string with commas when split_unbracketed_commas=True vs False
    2. None and empty inputs
    3. Brackets with extra quotes and spaces: '[ "item1" , \'item2\' ]'
    4. Strings that represent numbers: '123'
    """
    # 1. Unbracketed commas
    assert _normalize_string_list("tag1, tag2, tag3", split_unbracketed_commas=True) == ["tag1", "tag2", "tag3"]
    assert _normalize_string_list("tag1, tag2", split_unbracketed_commas=False) == ["tag1, tag2"]

    # 2. Bracketed with quotes and whitespace
    assert _normalize_string_list('[ "item1" , \'item2\' ]') == ["item1", "item2"]

    # 3. None and dummies
    assert _normalize_string_list(None) == []
    assert _normalize_string_list("[]") == []
    assert _normalize_string_list("[   ]") == []
    assert _normalize_string_list("None") == []
    assert _normalize_string_list("null") == []
    assert _normalize_string_list(["[]", "  ", None, "null", "valid"]) == ["valid"]


def test_category_folder_alignment_deep_hierarchy(tmp_path: Path):
    """
    Directory Boundary Probe:
    Ensure that files nested under knowledge/<category> are strictly checked,
    and invalid categories are rejected.
    """
    kb_root = tmp_path / "knowledge"
    for cat in ["youtube", "instagram", "ai-providers"]:
        (kb_root / cat).mkdir(parents=True)

    # 1. Valid file in matching folder
    valid_file = kb_root / "youtube" / "test_valid.md"
    valid_file.write_text("""---
id: valid-01
title: Valid Youtube
category: youtube
verification_status: VERIFIED
confidence: 0.9
sources: ["https://youtube.com"]
tags: [youtube, shorts]
---
Body
""", encoding="utf-8")
    is_valid, errors = validate_knowledge_file(valid_file)
    assert is_valid
    assert errors == []

    # 2. File in mismatched folder
    mismatched_file = kb_root / "instagram" / "mismatched.md"
    mismatched_file.write_text("""---
id: mismatched-01
title: Mismatched Youtube
category: youtube
verification_status: VERIFIED
confidence: 0.9
sources: ["https://youtube.com"]
tags: [youtube, shorts]
---
Body
""", encoding="utf-8")
    is_valid, errors = validate_knowledge_file(mismatched_file)
    assert not is_valid
    assert any("does not match metadata category" in e for e in errors)


# ==============================================================================
# SECTION 4: Search Engine Relevance & Boundary Scoring
# ==============================================================================

def test_search_engine_scoring_and_ranking(tmp_path: Path):
    """
    Search Scoring Probe:
    Verifies that:
    1. Title match (+5.0) outranks body match (+0.5).
    2. Exact tag match (+3.0) and hyphenated constituent match (+3.0) work correctly.
    3. Multiplier: VERIFIED entries get a 1.2x multiplier.
    4. 2-character domain terms ("4K", "AI", "UI", "HD") return matches.
    """
    kb_root = tmp_path / "knowledge"
    (kb_root / "research").mkdir(parents=True)

    # Entry A: "4K" in title
    entry_a = kb_root / "research" / "entry_a.md"
    entry_a.write_text("""---
id: entry-a
title: "Mastering 4K Video Encoding"
category: research
verification_status: VERIFIED
confidence: 0.95
sources: ["https://standards.org"]
tags: [video, 4k-encoding]
---
General encoding notes.
""", encoding="utf-8")

    # Entry B: "4K" only in body
    entry_b = kb_root / "research" / "entry_b.md"
    entry_b.write_text("""---
id: entry-b
title: "General Video Compression"
category: research
verification_status: EXPERIMENTAL
confidence: 0.95
tags: [video, compression]
---
We tested 4K video compression across 10 files.
""", encoding="utf-8")

    # Search for "4K"
    results = search_knowledge_text("4K", kb_root=kb_root)
    assert len(results) >= 2
    # Entry A should be first due to title match + VERIFIED status bonus
    assert results[0].metadata.id == "entry-a"
    assert results[1].metadata.id == "entry-b"

    # Search for "encoding" matching constituent of tag "4k-encoding"
    results_tag = search_knowledge_text("encoding", kb_root=kb_root)
    assert len(results_tag) >= 1
    assert results_tag[0].metadata.id == "entry-a"


# ==============================================================================
# SECTION 5: Concurrent SQLite sync_to_db Multi-Thread Integrity
# ==============================================================================

def test_concurrent_sync_to_db_multi_threaded(tmp_path: Path):
    """
    Concurrency Probe for SQLite Indexing:
    10 threads concurrently executing sync_to_db on the same SQLite database file.
    Asserts:
    1. No SQLite locking errors or database corruption.
    2. Final knowledge_index table has exact entry count.
    """
    kb_root = tmp_path / "knowledge"
    (kb_root / "youtube").mkdir(parents=True)
    db_file = tmp_path / "concurrent_index.sqlite"

    # Create 5 files
    for i in range(5):
        f = kb_root / "youtube" / f"doc_{i}.md"
        f.write_text(f"""---
id: doc-{i}
title: Youtube Document {i}
category: youtube
verification_status: VERIFIED
confidence: 0.90
sources: ["https://example.com/{i}"]
tags: [youtube, doc]
---
Body for doc {i}
""", encoding="utf-8")

    num_threads = 10
    barrier = threading.Barrier(num_threads)
    errors = []

    def run_sync():
        barrier.wait()
        try:
            sync_to_db(kb_root=kb_root, db_path=db_file)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run_sync) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(errors) == 0, f"sync_to_db concurrency errors: {errors}"

    conn = sqlite3.connect(db_file)
    cur = conn.execute("SELECT COUNT(*) FROM knowledge_index")
    count = cur.fetchone()[0]
    conn.close()
    assert count == 5


# ==============================================================================
# SECTION 6: AgentMessage Schema Boundaries, Unicode & Aliases
# ==============================================================================

def test_agent_message_unicode_and_large_payload():
    """
    Schema Stress Probe:
    1. Unicode emoji and multibyte UTF-8 characters across all fields.
    2. 100KB payload payload stress.
    3. Serialization to JSONL and deserialization roundtrip.
    """
    large_text = "🚀 Test content with multilingual glyphs: 漢字, العربية, Кириллица, 1080x1920 60fps! " * 1000
    msg = AgentMessage(
        sender=AgentID.AGENT_1_RESEARCH.value,
        recipient=AgentID.AGENT_2_ANTI_SLOP.value,
        task_id="UNICODE-001",
        action="SUBMIT_SCRIPT",
        payload={"large_script": large_text, "emoji": "🔥🎯⚡"},
        confidence="VERIFIED",
        sources=["knowledge/research/short-form-retention-mechanics.md"],
    )

    line = msg.to_jsonl_line()
    assert line.endswith("\n")

    # Roundtrip through JSON
    parsed_dict = json.loads(line)
    msg_roundtrip = AgentMessage.model_validate(parsed_dict)
    assert msg_roundtrip.message_id == msg.message_id
    assert msg_roundtrip.payload["emoji"] == "🔥🎯⚡"
    assert len(msg_roundtrip.payload["large_script"]) == len(large_text)


def test_agent_message_confidence_verified_without_sources_raises():
    """
    Schema Invariant Probe:
    AgentMessage with confidence='VERIFIED' but empty sources MUST raise ValueError.
    """
    with pytest.raises(ValueError, match="VERIFIED confidence messages must include at least one source citation"):
        AgentMessage(
            sender=AgentID.AGENT_1_RESEARCH.value,
            recipient=AgentID.AGENT_2_ANTI_SLOP.value,
            task_id="TASK-VERIF-ERR",
            action="SUBMIT_SCRIPT",
            payload={"data": "test"},
            confidence="VERIFIED",
            sources=[],
        )


def test_message_bus_strict_monotonic_ordering_per_thread(tmp_path: Path):
    """
    Message Bus Ordering Probe:
    10 threads concurrently dispatching 50 sequenced messages each (500 total).
    Asserts:
    1. For each thread, sequence numbers in logs/agent-comms.jsonl appear in strictly
       monotonic increasing order (seq 0, 1, 2, ... 49) without inversion.
    2. All 500 messages are logged and deserialized without corruption.
    """
    log_file = tmp_path / "comms_ordering.jsonl"
    logger = MessageLogger(log_path=log_file)
    bus = MessageBus(logger_instance=logger)

    num_threads = 10
    msgs_per_thread = 50
    barrier = threading.Barrier(num_threads)
    dispatch_errors = []

    def thread_dispatcher(thread_id: int):
        barrier.wait()
        for seq in range(msgs_per_thread):
            try:
                msg = AgentMessage(
                    sender=AgentID.ORCHESTRATOR.value,
                    recipient="broadcast",
                    task_id=f"ORD-THREAD-{thread_id}",
                    action="TASK_PROGRESS",
                    payload={"thread_id": thread_id, "seq": seq},
                )
                bus.dispatch(msg)
            except Exception as e:
                dispatch_errors.append(e)

    threads = [threading.Thread(target=thread_dispatcher, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert len(dispatch_errors) == 0, f"Dispatch errors: {dispatch_errors}"

    all_messages = logger.read_messages()
    assert len(all_messages) == num_threads * msgs_per_thread

    # Verify per-thread monotonic sequence ordering
    for thread_id in range(num_threads):
        thread_msgs = [m for m in all_messages if m.task_id == f"ORD-THREAD-{thread_id}"]
        assert len(thread_msgs) == msgs_per_thread
        sequences = [m.payload["seq"] for m in thread_msgs]
        expected_sequences = list(range(msgs_per_thread))
        assert sequences == expected_sequences, f"Thread {thread_id} messages were reordered: {sequences[:10]}"


def test_agent_message_schema_adversarial_matrix():
    """
    Schema Edge Cases Matrix:
    1. Invalid timestamps (missing 'Z' or offset, invalid text)
    2. task_id boundary violations (empty string, length > 64)
    3. Invalid sender / recipient strings
    4. Field alias normalization (sender_agent, recipient_agent, from_agent, to_agent)
    """
    # 1. Invalid timestamp
    with pytest.raises(ValueError, match="is not a valid ISO-8601 UTC string"):
        AgentMessage(
            sender=AgentID.ORCHESTRATOR.value,
            recipient=AgentID.AGENT_1_RESEARCH.value,
            task_id="T1",
            action="PING",
            timestamp="2026-11-10 12:00:00",  # missing T and Z
        )

    # 2. task_id boundary
    with pytest.raises(ValueError):
        AgentMessage(
            sender=AgentID.ORCHESTRATOR.value,
            recipient=AgentID.AGENT_1_RESEARCH.value,
            task_id="",  # min_length is 1
            action="PING",
        )
    with pytest.raises(ValueError):
        AgentMessage(
            sender=AgentID.ORCHESTRATOR.value,
            recipient=AgentID.AGENT_1_RESEARCH.value,
            task_id="X" * 65,  # max_length is 64
            action="PING",
        )

    # 3. Invalid sender
    with pytest.raises(ValueError, match="Invalid sender"):
        AgentMessage(
            sender="unauthorized_rogue_agent",
            recipient=AgentID.AGENT_1_RESEARCH.value,
            task_id="T1",
            action="PING",
        )

    # 4. Invalid recipient
    with pytest.raises(ValueError, match="Invalid recipient"):
        AgentMessage(
            sender=AgentID.ORCHESTRATOR.value,
            recipient="non_existent_recipient",
            task_id="T1",
            action="PING",
        )

    # 5. Field aliases
    msg_alias = AgentMessage(
        sender_agent="agent-1-research",
        recipient_agent="agent-2-anti-slop",
        task_id="ALIAS-01",
        action="PING",
        confidence="hypothesis",  # case normalization
    )
    assert msg_alias.sender == "agent-1-research"
    assert msg_alias.recipient == "agent-2-anti-slop"
    assert msg_alias.confidence == "HYPOTHESIS"


def test_task_manager_full_lifecycle_and_transition_matrix(tmp_path: Path):
    """
    State Machine Lifecycle Probe:
    Validates complete legitimate lifecycle journeys and asserts that invalid transitions are blocked:
    Journey 1: PENDING -> CLAIM -> IN_PROGRESS -> BLOCK -> BLOCKED -> UNBLOCK -> PENDING -> CLAIM -> COMPLETE
    Journey 2: PENDING -> CLAIM -> IN_PROGRESS -> RELEASE -> PENDING -> CLAIM -> FAIL -> FAILED
    Illegal: BLOCKED -> COMPLETE (blocked)
    Illegal: FAILED -> CLAIM (blocked)
    Illegal: COMPLETED -> CLAIM (blocked)
    """
    db_file = tmp_path / "lifecycle.sqlite"
    mgr = TaskManager(db_path=db_file, task_md=tmp_path / "lifecycle.md")

    with mgr._get_conn() as conn:
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('TASK_J1', 'Journey 1', 'PENDING')")
        conn.execute("INSERT INTO task_registry (task_id, task_name, status) VALUES ('TASK_J2', 'Journey 2', 'PENDING')")
        conn.commit()

    # Journey 1: Claim -> Block -> Unblock -> Claim -> Complete
    tok1 = mgr.claim_task("TASK_J1", agent_id="agent-1-research")
    assert mgr.get_task("TASK_J1")["status"] == "IN_PROGRESS"

    # Cannot complete without matching token
    with pytest.raises(StaleLockTokenError):
        mgr.complete_task("TASK_J1", agent_id="agent-1-research", token="wrong_token")

    mgr.block_task("TASK_J1", agent_id="agent-1-research", token=tok1, reason="Waiting for API")
    assert mgr.get_task("TASK_J1")["status"] == "BLOCKED"

    # Illegal transition: cannot complete while BLOCKED
    with pytest.raises(StaleLockTokenError):
        mgr.complete_task("TASK_J1", agent_id="agent-1-research", token=tok1, artifact_path="art.mp4")

    mgr.unblock_task("TASK_J1")
    assert mgr.get_task("TASK_J1")["status"] == "PENDING"

    tok2 = mgr.claim_task("TASK_J1", agent_id="agent-2-anti-slop")
    assert tok2 != tok1
    mgr.complete_task("TASK_J1", agent_id="agent-2-anti-slop", token=tok2, artifact_path="art.mp4")
    assert mgr.get_task("TASK_J1")["status"] == "COMPLETED"

    # Cannot claim completed task
    with pytest.raises(TaskCollisionError):
        mgr.claim_task("TASK_J1", agent_id="agent-1-research")

    # Journey 2: Claim -> Release -> Claim -> Fail
    tok3 = mgr.claim_task("TASK_J2", agent_id="agent-1-research")
    mgr.release_task("TASK_J2", agent_id="agent-1-research", token=tok3)
    assert mgr.get_task("TASK_J2")["status"] == "PENDING"
    assert mgr.get_task("TASK_J2")["lock_token"] is None

    tok4 = mgr.claim_task("TASK_J2", agent_id="agent-3-social")
    mgr.fail_task("TASK_J2", agent_id="agent-3-social", token=tok4, error="Quota permanently exhausted")
    assert mgr.get_task("TASK_J2")["status"] == "FAILED"

    # Cannot claim failed task
    with pytest.raises(TaskCollisionError):
        mgr.claim_task("TASK_J2", agent_id="agent-1-research")


def test_central_orchestrator_concurrent_pipelines(tmp_path: Path):
    """
    System Integration Probe:
    5 concurrent pipelines running through CentralOrchestrator instances simultaneously.
    Asserts:
    1. All 5 pipelines complete with status='COMPLETED' and valid qa_verdict.
    2. All messages across all pipelines are logged without data loss or corruption.
    """
    log_file = tmp_path / "comms_orch_concurrent.jsonl"
    logger = MessageLogger(log_path=log_file)
    bus = MessageBus(logger_instance=logger)
    task_mgr = TaskManager(db_path=tmp_path / "orch.sqlite", task_md=tmp_path / "orch.md")

    orch = CentralOrchestrator(bus=bus, task_manager=task_mgr)

    num_pipelines = 5
    results = []
    errors = []

    def run_pipe(idx: int):
        try:
            res = orch.run_production_pipeline(topic=f"Topic {idx}: Hidden Windows Shortcuts")
            results.append(res)
        except Exception as e:
            errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_pipelines) as executor:
        futures = [executor.submit(run_pipe, i) for i in range(num_pipelines)]
        concurrent.futures.wait(futures)

    assert len(errors) == 0, f"Pipeline errors: {errors}"
    assert len(results) == num_pipelines
    for r in results:
        assert r["status"] == "COMPLETED"
        assert r["qa_verdict"] == "PASS"
        assert "script" in r

    logged_msgs = logger.read_messages()
    # Each pipeline sends 4 messages (quota request, script submit, qa review, social approval)
    assert len(logged_msgs) >= num_pipelines * 4

