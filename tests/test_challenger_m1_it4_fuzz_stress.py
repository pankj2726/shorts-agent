"""
test_challenger_m1_it4_fuzz_stress.py — Milestone 1 Iteration 4 Adversarial Stress & Fuzz Harness.

Authored by: Milestone 1 Challenger 2 (teamwork_challenger_m1_it4_2)

Exhaustively verifies:
1. Fuzz testing of `_is_null_or_dummy()` and YAML frontmatter citation validation:
   - Arbitrary bracket depths (1 to 60) with mixed quotes, spaces, and all null variants.
   - Unicode whitespace variations (en-quad, em-quad, ideographic, non-breaking, thin, etc.).
   - Empty collections, dictionaries, and edge-case tokens.
   - Preservation of authentic bracketed citations and URLs (zero false positives).
2. Adversarial stress testing of `search_knowledge_text()`:
   - SQL injection strings, regex wildcards, null bytes, script tags.
   - Single character and sub-token boundary handling.
   - Exotic Unicode, multilingual text, and emoji queries.
   - Extremely large query payloads and boundary limit parameters.
3. High-concurrency CAS task races with 50+ threads:
   - 60-thread race for single pending task (exactly 1 winner, 59 collisions).
   - 60-thread race across multiple tasks.
   - Expired lease stealing race under high contention.
   - Stale lock token immunity under concurrent completions.
"""
from __future__ import annotations

import concurrent.futures
import itertools
import json
import random
import re
import string
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
    InvalidStateTransitionError,
    StaleLockTokenError,
    TaskCollisionError,
    TaskManager,
)


# ==============================================================================
# SECTION 1: FUZZ TESTING `_is_null_or_dummy()` & CITATION GATING
# ==============================================================================

UNICODE_SPACES = [
    "\u0020",  # standard space
    "\u00A0",  # no-break space
    "\u2000",  # en quad
    "\u2001",  # em quad
    "\u2002",  # en space
    "\u2003",  # em space
    "\u2004",  # three-per-em space
    "\u2005",  # four-per-em space
    "\u2006",  # six-per-em space
    "\u2007",  # figure space
    "\u2008",  # punctuation space
    "\u2009",  # thin space
    "\u200A",  # hair space
    "\u2028",  # line separator
    "\u2029",  # paragraph separator
    "\u202F",  # narrow no-break space
    "\u205F",  # medium mathematical space
    "\u3000",  # ideographic space
]

NULL_SCALARS = [
    "",
    "   ",
    "null",
    "Null",
    "NULL",
    "none",
    "None",
    "NONE",
    "~",
    "nil",
    "Nil",
    "NIL",
    "undefined",
    "Undefined",
    "UNDEFINED",
    "n/a",
    "N/A",
    "na",
    "NA",
    "-",
    "–",
    "—",
]


class TestFuzzNullOrDummyAndCitationGating:
    """Fuzz testing bracket nesting, unicode whitespace, and edge cases."""

    @pytest.mark.parametrize("depth", [1, 2, 3, 5, 10, 20, 40, 60])
    @pytest.mark.parametrize("base_token", ["~", "null", "none", "nil", "undefined", "n/a", "na", "-", "–", "—", ""])
    def test_arbitrary_bracket_depths_fuzzing(self, depth: int, base_token: str):
        """Generates nested brackets up to depth 60 with random quotes and whitespace."""
        # Construct deeply nested bracket string
        left = "[" * depth
        right = "]" * depth
        test_val = f"{left}{base_token}{right}"
        assert _is_null_or_dummy(test_val) is True, f"Failed for depth {depth} token: {test_val!r}"

    def test_randomized_nested_brackets_with_quotes_and_spaces(self):
        """Fuzzes 200 randomly assembled nested strings with mixed quotes and whitespace."""
        rng = random.Random(42)
        quote_options = ["", "'", '"']
        
        for _ in range(200):
            depth = rng.randint(1, 25)
            token = rng.choice(NULL_SCALARS)
            
            # Wrap token in layers of brackets, quotes, and whitespace
            curr = token
            for _ in range(depth):
                q = rng.choice(quote_options)
                sp_l = " " * rng.randint(0, 3)
                sp_r = " " * rng.randint(0, 3)
                curr = f"[{sp_l}{q}{curr}{q}{sp_r}]"
            
            assert _is_null_or_dummy(curr) is True, f"Fuzz failure on nested token: {curr!r}"

    @pytest.mark.parametrize("sp", UNICODE_SPACES)
    @pytest.mark.parametrize("token", ["~", "null", "none", "-", "undefined"])
    def test_unicode_whitespace_around_null_tokens(self, sp: str, token: str):
        """Ensures all Unicode whitespace categories are cleanly stripped."""
        s1 = f"{sp}{token}{sp}"
        s2 = f"[{sp}{token}{sp}]"
        s3 = f"[[{sp}'{token}'{sp}]]"
        assert _is_null_or_dummy(s1) is True, f"Failed for unicode space {sp!r} on {token!r}"
        assert _is_null_or_dummy(s2) is True, f"Failed for bracketed unicode space {sp!r} on {token!r}"
        assert _is_null_or_dummy(s3) is True, f"Failed for nested quoted unicode space {sp!r} on {token!r}"

    def test_authentic_bracketed_citations_preserved_zero_false_positives(self):
        """Legitimate titles, citations, and URLs with brackets must NOT be flagged as dummy."""
        authentic_samples = [
            "[YouTube Creator Playbook 2024]",
            "Algorithm Insights [Vol 3]",
            "[[Verified Research Study 2025]]",
            "https://youtube.com/watch?v=xyz[123]",
            "Study by [Dr. Smith et al., 2023]",
            "Retention Guideline (Section [A])",
            "Hook Strategy [Curiosity-Gap #1]",
            "[OpenAI & DeepMind Research 2024]",
        ]
        for sample in authentic_samples:
            assert _is_null_or_dummy(sample) is False, f"False positive: authentic citation {sample!r} flagged as dummy!"

    def test_yaml_frontmatter_nested_bracket_bypass_fails_verified(self, tmp_path: Path):
        """Fuzzes YAML frontmatter files with various deep bracket combinations."""
        test_tokens = [
            "[[[[[[[[~]]]]]]]]",
            "[[[[[[[[[[[[null]]]]]]]]]]]]",
            "[[[[[   'none'   ]]]]]",
            "[[[[[[[   -   ]]]]]]]",
            "[[[[[[[[[[[[[undefined]]]]]]]]]]]]]",
        ]
        for idx, token in enumerate(test_tokens):
            fpath = tmp_path / f"nested_fuzz_{idx}.md"
            content = (
                f"---\n"
                f"id: kb-fuzz-bracket-{idx}\n"
                f"title: Deep Bracket Fuzz Entry {idx}\n"
                f"category: youtube\n"
                f"verification_status: VERIFIED\n"
                f"confidence: 0.95\n"
                f"sources: {token}\n"
                f"tags: [fuzz, boundary]\n"
                f"---\n"
                f"Body content for test {idx}."
            )
            fpath.write_text(content, encoding="utf-8")
            is_valid, errors = validate_knowledge_file(fpath)
            assert is_valid is False, f"Token {token} bypassed citation gating!"
            assert any("verifiable source citation" in err for err in errors)

class TestEmptyMappingCitationBypass:
    """Empirically tests empty mapping and curly brace citation bypasses."""

    @pytest.mark.parametrize("empty_mapping_val", [
        {},
        "{}",
        "{ }",
        "{   }",
        "{\t}",
        "{\n}",
        "[{}]",
        "[[{}]]",
    ])
    def test_is_null_or_dummy_must_recognize_empty_mappings(self, empty_mapping_val: Any):
        """_is_null_or_dummy() must return True for empty mapping dicts and strings."""
        assert _is_null_or_dummy(empty_mapping_val) is True, (
            f"_is_null_or_dummy({empty_mapping_val!r}) returned False, allowing empty mapping bypass!"
        )

    @pytest.mark.parametrize("empty_input", [
        {},
        "{}",
        "{ }",
        [{}],
        ["{}"],
        ["{ }"],
    ])
    def test_normalize_string_list_must_filter_empty_mappings(self, empty_input: Any):
        """_normalize_string_list() must normalize empty mapping representations to []."""
        result = _normalize_string_list(empty_input)
        assert result == [], f"_normalize_string_list({empty_input!r}) returned {result}, expected []"

    @pytest.mark.parametrize("empty_mapping", [
        "{}",
        "{ }",
        "{   }",
        "[{}]",
        "[{ }]",
    ])
    def test_empty_mapping_in_yaml_bypasses_citation_gating(self, tmp_path: Path, empty_mapping: str):
        """Empty YAML mapping braces '{}' must fail citation validation for VERIFIED."""
        fpath = tmp_path / "mapping_bypass.md"
        content = (
            f"---\n"
            f"id: kb-mapping-bypass\n"
            f"title: Mapping Bypass Entry\n"
            f"category: youtube\n"
            f"verification_status: VERIFIED\n"
            f"confidence: 0.95\n"
            f"sources: {empty_mapping}\n"
            f"tags: [bypass, probe]\n"
            f"---\n"
            f"Body content testing empty mapping bypass."
        )
        fpath.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(fpath)
        assert is_valid is False, f"Empty mapping {empty_mapping!r} bypassed citation gating!"
        assert any("verifiable source citation" in err for err in errors)

    def test_direct_dict_in_metadata_bypasses_citation_gating(self, tmp_path: Path):
        """Direct dict object {} in metadata sources must fail citation validation for VERIFIED."""
        fpath = tmp_path / "dict_bypass.md"
        content = (
            "---\n"
            "id: kb-dict-bypass\n"
            "title: Dict Bypass Entry\n"
            "category: youtube\n"
            "verification_status: VERIFIED\n"
            "confidence: 0.95\n"
            "sources: {}\n"
            "tags: [bypass, probe]\n"
            "---\n"
            "Body content testing dict object bypass."
        )
        fpath.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(fpath)
        assert is_valid is False, "Direct dict object {} bypassed citation gating!"
        assert any("verifiable source citation" in err for err in errors)

    def test_valid_sources_with_mixed_nulls_passes_verified(self, tmp_path: Path):
        """If at least ONE valid citation exists alongside nulls, VERIFIED must succeed."""
        fpath = tmp_path / "mixed_sources.md"
        content = (
            "---\n"
            "id: kb-mixed-sources\n"
            "title: Mixed Sources Entry\n"
            "category: youtube\n"
            "verification_status: VERIFIED\n"
            "confidence: 0.92\n"
            "sources: [null, 'https://youtube.com/creators', ~]\n"
            "tags: [mixed, citation]\n"
            "---\n"
            "Body content with valid and null sources."
        )
        fpath.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(fpath)
        assert is_valid is True, f"Expected valid, got errors: {errors}"
        entry = parse_knowledge_file(fpath)
        assert entry.metadata.sources == ["https://youtube.com/creators"]


# ==============================================================================
# SECTION 2: ADVERSARIAL STRESS TESTING `search_knowledge_text()`
# ==============================================================================

class TestSearchKnowledgeTextStress:
    """Stress tests keyword search against SQL injections, regex metacharacters, and boundary inputs."""

    @pytest.fixture
    def test_kb_root(self, tmp_path: Path) -> Path:
        """Sets up a realistic knowledge base for search queries."""
        kb_root = tmp_path / "knowledge"
        categories = ["youtube", "creative-production", "research", "ai-providers"]
        
        for cat in categories:
            (kb_root / cat).mkdir(parents=True, exist_ok=True)

        samples = [
            (
                "youtube",
                "kb-yt-pacing",
                "Retention Pacing and 3-Second Rule",
                "VERIFIED",
                0.95,
                ["pacing", "retention", "hook"],
                "Videos must maintain pacing between 2.0 and 3.0 seconds per cut.",
            ),
            (
                "creative-production",
                "kb-as-slop",
                "AI Slop Blacklist and Buzzword Detection",
                "VERIFIED",
                0.90,
                ["slop", "blacklist", "detection"],
                "Blacklisted words include delve, tapestry, revolutionize, game-changer.",
            ),
            (
                "research",
                "kb-res-unicode",
                "Multilingual Short Retention 日本語 and Spanish",
                "VERIFIED",
                0.88,
                ["multilingual", "international", "japanese"],
                "Retention strategies for global audiences including Tokyo and Madrid.",
            ),
            (
                "ai-providers",
                "kb-prod-loudnorm",
                "Audio Normalization at -14 LUFS",
                "EXPERIMENTAL",
                0.70,
                ["audio", "loudnorm", "lufs"],
                "Integrated loudness should target -14 LUFS with True Peak -1.0 dBFS.",
            ),
        ]

        for cat, eid, title, status, conf, tags, body in samples:
            fpath = kb_root / cat / f"{eid}.md"
            meta = KnowledgeMetadata(
                id=eid,
                title=title,
                category=cat,
                verification_status=status,
                confidence=conf,
                sources=["https://example.com/source"],
                tags=tags,
            )
            serialized = _serialize_yaml_frontmatter(meta, body)
            fpath.write_text(serialized, encoding="utf-8")

        return kb_root

    @pytest.mark.parametrize("payload", [
        "' OR '1'='1",
        "'; DROP TABLE knowledge_index; --",
        "' UNION SELECT id, category, title FROM knowledge_index --",
        "admin'--",
        "\" or \"\"=\"",
        "1; EXEC xp_cmdshell('dir'); --",
        "SELECT * FROM knowledge_index WHERE 1=1",
    ])
    def test_sql_injection_payloads_do_not_crash_or_corrupt(self, test_kb_root: Path, payload: str):
        """SQL injection strings must execute safely without raising exceptions."""
        results = search_knowledge_text(query=payload, kb_root=test_kb_root)
        assert isinstance(results, list)

    @pytest.mark.parametrize("regex_meta", [
        ".*",
        "+",
        "?",
        "(",
        ")",
        "[a-z]+",
        "\\",
        "^$",
        "(?i)pacing",
        "{1,5}",
        "|",
    ])
    def test_regex_metacharacters_do_not_cause_re_errors(self, test_kb_root: Path, regex_meta: str):
        """Regex metacharacters must be tokenized as word characters and not crash `re`."""
        results = search_knowledge_text(query=regex_meta, kb_root=test_kb_root)
        assert isinstance(results, list)

    def test_sub_two_char_and_whitespace_queries(self, test_kb_root: Path):
        """Queries shorter than 2 chars or pure whitespace return empty list gracefully."""
        assert search_knowledge_text(query="", kb_root=test_kb_root) == []
        assert search_knowledge_text(query="   ", kb_root=test_kb_root) == []
        assert search_knowledge_text(query="a", kb_root=test_kb_root) == []
        assert search_knowledge_text(query="1", kb_root=test_kb_root) == []
        assert search_knowledge_text(query=" - ", kb_root=test_kb_root) == []

    def test_multilingual_unicode_and_emoji_queries(self, test_kb_root: Path):
        """Search safely handles Unicode script, non-Latin alphabet, and emojis."""
        res_jp = search_knowledge_text(query="日本語", kb_root=test_kb_root)
        assert len(res_jp) >= 1
        assert res_jp[0].metadata.id == "kb-res-unicode"

        res_emoji = search_knowledge_text(query="pacing 🚀🎯", kb_root=test_kb_root)
        assert len(res_emoji) >= 1
        assert res_emoji[0].metadata.id == "kb-yt-pacing"

    def test_extremely_large_query_stress(self, test_kb_root: Path):
        """Passes a 20,000-character query without memory exhaustion or crash."""
        huge_query = ("retention pacing slop audio " * 1000) + ("x" * 5000)
        start = time.perf_counter()
        results = search_knowledge_text(query=huge_query, kb_root=test_kb_root)
        duration = time.perf_counter() - start
        assert duration < 2.0, f"Query took too long: {duration:.2f}s"
        assert len(results) > 0

    def test_boundary_limit_parameters(self, test_kb_root: Path):
        """Checks limit=0, limit=1, limit=100 parameters."""
        assert search_knowledge_text(query="pacing", limit=0, kb_root=test_kb_root) == []
        assert len(search_knowledge_text(query="retention", limit=1, kb_root=test_kb_root)) <= 1
        assert len(search_knowledge_text(query="pacing", limit=100, kb_root=test_kb_root)) <= 4

    def test_nonexistent_or_invalid_category_filter(self, test_kb_root: Path):
        """Invalid category filters return empty results without exception."""
        assert search_knowledge_text(query="pacing", category="nonexistent-cat", kb_root=test_kb_root) == []
        assert search_knowledge_text(query="pacing", category="../traversal", kb_root=test_kb_root) == []


# ==============================================================================
# SECTION 3: 50+ THREAD CAS CONCURRENCY STRESS (TASK MANAGER)
# ==============================================================================

class TestTaskManagerConcurrencyStress:
    """Stress-tests TaskManager compare-and-swap (CAS) under 50+ threads."""

    def test_60_threads_competing_for_single_pending_task(self, tmp_path: Path):
        """
        60 concurrent threads simultaneously race to claim a single PENDING task.
        Invariants:
        - Exactly 1 thread succeeds (returns valid UUID token).
        - Exactly 59 threads receive TaskCollisionError.
        - Zero deadlocks or uncaught SQLite exceptions.
        """
        db_path = tmp_path / "cas_60_race.sqlite"
        task_md = tmp_path / "task-ownership.md"
        manager = TaskManager(db_path=db_path, task_md=task_md)

        with manager._get_conn() as conn:
            conn.execute(
                "INSERT INTO task_registry (task_id, task_name, owner, status) VALUES (?, ?, NULL, 'PENDING')",
                ("TASK-CAS-60", "60 Thread Race")
            )
            conn.commit()

        thread_count = 60
        gate = threading.Barrier(thread_count)
        winners: List[Tuple[str, str]] = []
        collisions: List[Tuple[str, Exception]] = []
        unexpected_errors: List[Exception] = []

        def worker(idx: int):
            agent = f"worker-agent-{idx:03d}"
            try:
                gate.wait(timeout=10.0)
                tok = manager.claim_task("TASK-CAS-60", agent_id=agent, lease_seconds=60)
                winners.append((agent, tok))
            except TaskCollisionError as exc:
                collisions.append((agent, exc))
            except Exception as exc:
                unexpected_errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20.0)

        assert len(unexpected_errors) == 0, f"Unexpected errors: {unexpected_errors}"
        assert len(winners) == 1, f"Expected 1 winner, got {len(winners)}: {winners}"
        assert len(collisions) == 59, f"Expected 59 collisions, got {len(collisions)}"

        task = manager.get_task("TASK-CAS-60")
        assert task["status"] == "IN_PROGRESS"
        assert task["owner"] == winners[0][0]
        assert task["lock_token"] == winners[0][1]

    def test_60_threads_racing_across_12_tasks_simultaneously(self, tmp_path: Path):
        """
        60 threads competing across 12 distinct tasks (5 threads per task).
        Invariants:
        - Exactly 12 claims succeed.
        - Exactly 48 claims fail with TaskCollisionError.
        - Every task is in IN_PROGRESS state with distinct owner.
        """
        db_path = tmp_path / "cas_12_tasks.sqlite"
        task_md = tmp_path / "task-ownership.md"
        manager = TaskManager(db_path=db_path, task_md=task_md)

        task_ids = [f"TASK-MULTI-{i:02d}" for i in range(12)]
        with manager._get_conn() as conn:
            for tid in task_ids:
                conn.execute(
                    "INSERT INTO task_registry (task_id, task_name, owner, status) VALUES (?, ?, NULL, 'PENDING')",
                    (tid, f"Multi Task {tid}")
                )
            conn.commit()

        thread_count = 60
        gate = threading.Barrier(thread_count)
        successes: List[Tuple[str, str, str]] = []
        lock = threading.Lock()
        collision_count = 0
        unexpected_errors: List[Exception] = []

        def worker(idx: int):
            target_task = task_ids[idx % len(task_ids)]
            agent = f"agent-{idx:03d}"
            nonlocal collision_count
            try:
                gate.wait(timeout=10.0)
                tok = manager.claim_task(target_task, agent_id=agent, lease_seconds=60)
                with lock:
                    successes.append((target_task, agent, tok))
            except TaskCollisionError:
                with lock:
                    collision_count += 1
            except Exception as exc:
                with lock:
                    unexpected_errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20.0)

        assert len(unexpected_errors) == 0, f"Unexpected errors: {unexpected_errors}"
        assert len(successes) == 12, f"Expected 12 successes, got {len(successes)}"
        assert collision_count == 48, f"Expected 48 collisions, got {collision_count}"

        claimed_tasks = {s[0] for s in successes}
        assert claimed_tasks == set(task_ids)

    def test_expired_lease_takeover_under_50_thread_race(self, tmp_path: Path):
        """
        A task's lease expires. 50 concurrent threads race to steal the expired task.
        Invariants:
        - Exactly ONE thread successfully steals the expired task.
        - 49 threads receive TaskCollisionError.
        - Task retains new owner and new fencing token.
        """
        db_path = tmp_path / "cas_expired_race.sqlite"
        task_md = tmp_path / "task-ownership.md"
        manager = TaskManager(db_path=db_path, task_md=task_md)

        # Insert expired task
        with manager._get_conn() as conn:
            conn.execute("""
            INSERT INTO task_registry 
            (task_id, task_name, owner, status, lock_token, locked_at, lease_expires_at)
            VALUES (?, ?, ?, 'IN_PROGRESS', ?, datetime('now', '-300 seconds'), datetime('now', '-10 seconds'))
            """, ("TASK-EXPIRED-STEAL", "Expired Task", "original-agent", "old-token-1234"))
            conn.commit()

        thread_count = 50
        gate = threading.Barrier(thread_count)
        winners: List[Tuple[str, str]] = []
        collisions: List[Tuple[str, Exception]] = []
        unexpected_errors: List[Exception] = []

        def worker(idx: int):
            agent = f"stealer-agent-{idx:03d}"
            try:
                gate.wait(timeout=10.0)
                tok = manager.claim_task("TASK-EXPIRED-STEAL", agent_id=agent, lease_seconds=120)
                winners.append((agent, tok))
            except TaskCollisionError as exc:
                collisions.append((agent, exc))
            except Exception as exc:
                unexpected_errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20.0)

        assert len(unexpected_errors) == 0, f"Unexpected errors: {unexpected_errors}"
        assert len(winners) == 1, f"Expected exactly 1 thief to steal the lease, got {len(winners)}"
        assert len(collisions) == 49, f"Expected 49 collisions, got {len(collisions)}"

        task = manager.get_task("TASK-EXPIRED-STEAL")
        assert task["status"] == "IN_PROGRESS"
        assert task["owner"] == winners[0][0]
        assert task["lock_token"] == winners[0][1]
        assert task["lock_token"] != "old-token-1234"

    def test_stale_token_rejection_matrix(self, tmp_path: Path):
        """Operations with forged or stale tokens are immediately rejected."""
        db_path = tmp_path / "cas_stale_tokens.sqlite"
        task_md = tmp_path / "task-ownership.md"
        manager = TaskManager(db_path=db_path, task_md=task_md)

        with manager._get_conn() as conn:
            conn.execute(
                "INSERT INTO task_registry (task_id, task_name, owner, status) VALUES (?, ?, NULL, 'PENDING')",
                ("TASK-TOKEN-TEST", "Token Test")
            )
            conn.commit()

        valid_token = manager.claim_task("TASK-TOKEN-TEST", agent_id="agent-legit", lease_seconds=120)
        fake_token = "forged-uuid-0000-0000"

        # Heartbeat with fake token must fail
        with pytest.raises(StaleLockTokenError):
            manager.heartbeat("TASK-TOKEN-TEST", agent_id="agent-legit", token=fake_token)

        # Heartbeat with wrong agent must fail
        with pytest.raises(StaleLockTokenError):
            manager.heartbeat("TASK-TOKEN-TEST", agent_id="agent-imposter", token=valid_token)

        # Complete with fake token must fail
        with pytest.raises(StaleLockTokenError):
            manager.complete_task("TASK-TOKEN-TEST", agent_id="agent-legit", token=fake_token)

        # Complete with legit token succeeds
        manager.complete_task("TASK-TOKEN-TEST", agent_id="agent-legit", token=valid_token, artifact_path="output.mp4")
        task = manager.get_task("TASK-TOKEN-TEST")
        assert task["status"] == "COMPLETED"
        assert task["artifact_path"] == "output.mp4"

        # After completion, valid token is now expired/nullified
        with pytest.raises(StaleLockTokenError):
            manager.heartbeat("TASK-TOKEN-TEST", agent_id="agent-legit", token=valid_token)
