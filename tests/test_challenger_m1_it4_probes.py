"""
test_challenger_m1_it4_probes.py — Milestone 1 Iteration 4 Adversarial Probe Suite.

Authored by: Milestone 1 Challenger 1 (teamwork_challenger_m1_it4_1)

Exhaustively probes and stress-tests:
1. Deeply nested brackets up to 50 layers with null tokens, whitespace, and empty contents.
2. Mixed nested brackets with whitespace, quotes (single/double), and punctuation.
3. Placeholder dash variations ("-", "–", "—") and their bracketed variations.
4. False-positive checks: ensure authentic citations ([YouTube Creator Playbook], [1], URLs)
   are never falsely rejected.
5. Mixed source normalization (stripping nested dummies while retaining authentic citations).
6. High-concurrency CAS task registry and message logger invariants.
"""
from __future__ import annotations

import concurrent.futures
import sqlite3
import threading
from pathlib import Path
from typing import Any, List, Tuple

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
    AgentAction,
    AgentID,
    AgentMessage,
    BaseAgent,
    ConfidenceLevel,
    MessageBus,
    MessageLogger,
    RecipientID,
    TaskCollisionError,
    TaskManager,
)


# ==============================================================================
# SECTION 1: DEEPLY NESTED BRACKETS (10 TO 50 LAYERS)
# ==============================================================================

DEEP_DEPTHS = [10, 15, 20, 25, 30, 50]

NULL_SCALARS = [
    "",
    "   ",
    "~",
    "null",
    "Null",
    "NULL",
    "none",
    "None",
    "NONE",
    "nil",
    "NIL",
    "undefined",
    "UNDEFINED",
    "n/a",
    "N/A",
    "na",
    "NA",
    "-",
    "–",  # en dash
    "—",  # em dash
]


def make_nested_brackets(token: str, depth: int, quote: str = "", space: bool = False) -> str:
    """Helper to wrap token in `depth` pairs of square brackets with optional inner quote and spacing."""
    s = f"{quote}{token}{quote}"
    if space:
        s = f" {s} "
    for _ in range(depth):
        s = f"[{s}]"
    return s


class TestDeeplyNestedBracketCitations:
    """Stress-tests citation rejection for brackets nested 10 to 50 layers deep."""

    @pytest.mark.parametrize("depth", DEEP_DEPTHS)
    @pytest.mark.parametrize("scalar", ["~", "null", "none", "nil", "-", "–", "—", ""])
    def test_deep_nested_brackets_is_null_or_dummy(self, depth: int, scalar: str):
        """_is_null_or_dummy must correctly identify dummy scalars nested up to 50 levels."""
        vector = make_nested_brackets(scalar, depth)
        assert _is_null_or_dummy(vector) is True, (
            f"_is_null_or_dummy failed for depth {depth} on scalar {scalar!r}: {vector[:20]}..."
        )

    @pytest.mark.parametrize("depth", [10, 20, 50])
    @pytest.mark.parametrize("scalar", ["~", "null", "none", "-", "—"])
    def test_deep_nested_brackets_in_yaml_frontmatter_fails_verified(
        self, tmp_path: Path, depth: int, scalar: str
    ):
        """A VERIFIED document with 10+ nested brackets around null/dash must fail validation."""
        vector = make_nested_brackets(scalar, depth)
        file_path = tmp_path / f"probe_deep_{depth}_{abs(hash(scalar))}.md"
        content = (
            f"---\n"
            f"id: kb-probe-deep-{depth}\n"
            f"title: Deep Nested Probe {depth}\n"
            f"category: youtube\n"
            f"verification_status: VERIFIED\n"
            f"confidence: 0.95\n"
            f"sources: {vector}\n"
            f"tags: [probe, deep, adversarial]\n"
            f"---\n"
            f"Adversarial document with {depth} bracket layers.\n"
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)

        assert is_valid is False, (
            f"VULNERABILITY: Depth {depth} nested bracket {scalar!r} bypassed citation validation!\n"
            f"is_valid: {is_valid}, errors: {errors}"
        )
        assert any("verifiable source citation" in err for err in errors), (
            f"Expected citation error for depth {depth} {scalar!r}, got: {errors}"
        )

    @pytest.mark.parametrize("depth", [10, 25, 50])
    def test_deep_nested_empty_brackets(self, depth: int):
        """Empty bracket nesting [[[[[[[[[[]]]]]]]]]] must evaluate to dummy and normalize to empty."""
        vector = make_nested_brackets("", depth)
        assert _is_null_or_dummy(vector) is True
        assert _normalize_string_list(vector) == []


# ==============================================================================
# SECTION 2: MIXED NESTED BRACKETS, WHITESPACE & QUOTE COMBINATIONS
# ==============================================================================

class TestMixedNestedBracketsAndQuotes:
    """Stress-tests complex alternating quotes, spacing, and bracket permutations."""

    @pytest.mark.parametrize(
        "vector",
        [
            # Alternating quotes with brackets
            "['['['~']']']",
            '["["["~"]"]"]',
            '["[\'["~"]\']"]',
            "['[\"[\'~\']\"]']",
            "['['['['['null']']']']']",
            '["["["["["none"]"]"]"]"]',
            # Mixed whitespace inside brackets
            "[   [   [   ~   ]   ]   ]",
            "[\t[\n[  null  ]\n]\t]",
            "[ \u00a0 [ \u00a0 ~ \u00a0 ] \u00a0 ]",
            "[\t\r\n  [\t\r\n  none  \t\r\n]  \t\r\n]",
            # Quoted brackets within brackets
            "[[ '[[ ~ ]]' ]]",
            '[[ "[[ null ]]" ]]',
            "[[ '[[ \"-\" ]]' ]]",
            '[[ "[[ \'—\' ]]" ]]',
            # Multiple empty spaces and brackets
            "[[[[[[[[[[          ]]]]]]]]]]",
            "[[[[[[[[[[   ''   ]]]]]]]]]]",
            '[[[[[[[[[[   ""   ]]]]]]]]]]',
            "[[[[[[[[[[   ~    ]]]]]]]]]]",
        ],
    )
    def test_mixed_nested_bracket_combinations_are_dummy(self, vector: str):
        """All variations of nested quotes, whitespace, and brackets must evaluate to dummy."""
        assert _is_null_or_dummy(vector) is True, f"Failed for vector: {vector!r}"
        assert _normalize_string_list(vector) == []

    @pytest.mark.parametrize(
        "vector",
        [
            "['['['~']']']",
            "[[ '[[ ~ ]]' ]]",
            "[   [   [   ~   ]   ]   ]",
            "[[[[[[[[[[   ''   ]]]]]]]]]]",
        ],
    )
    def test_mixed_nested_bracket_fails_verified_in_file(self, tmp_path: Path, vector: str):
        """Mixed nested bracket vectors in a file must fail VERIFIED citation gating."""
        file_path = tmp_path / f"probe_mixed_{abs(hash(vector))}.md"
        content = (
            f"---\n"
            f"id: kb-probe-mixed\n"
            f"title: Mixed Bracket Probe\n"
            f"category: research\n"
            f"verification_status: VERIFIED\n"
            f"confidence: 0.90\n"
            f"sources: {vector}\n"
            f"tags: [probe, mixed]\n"
            f"---\n"
            f"Testing mixed bracket dummy rejection.\n"
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)
        assert is_valid is False
        assert any("verifiable source citation" in err for err in errors)


# ==============================================================================
# SECTION 3: DASH VARIATIONS AND TABLE PLACEHOLDER PROBES
# ==============================================================================

DASH_VECTORS = [
    "-",        # hyphen-minus (ASCII 0x2D)
    "–",        # en dash (U+2013)
    "—",        # em dash (U+2014)
    " - ",      # spaced hyphen
    " – ",      # spaced en dash
    " — ",      # spaced em dash
    "'-'",      # single-quoted hyphen
    '"-"',      # double-quoted hyphen
    "'–'",      # single-quoted en dash
    '"–"',      # double-quoted en dash
    "'—'",      # single-quoted em dash
    '"—"',      # double-quoted em dash
    "[-]",      # bracketed hyphen
    "[–]",      # bracketed en dash
    "[—]",      # bracketed em dash
    "[ - ]",    # spaced bracketed hyphen
    "[ – ]",    # spaced bracketed en dash
    "[ — ]",    # spaced bracketed em dash
    "[['-']]",  # nested quoted hyphen
    '[["–"]]',  # nested quoted en dash
    "[['—']]",  # nested quoted em dash
    "[[[[[[[[[[-]]]]]]]]]]",  # 10-layer bracketed hyphen
    "[[[[[[[[[[–]]]]]]]]]]",  # 10-layer bracketed en dash
    "[[[[[[[[[[—]]]]]]]]]]",  # 10-layer bracketed em dash
]


class TestDashVariationsAndTablePlaceholders:
    """Stress-tests hyphen, en-dash, and em-dash table placeholders in citation fields."""

    @pytest.mark.parametrize("dash_vector", DASH_VECTORS)
    def test_dash_variations_recognized_as_dummy(self, dash_vector: str):
        """Every dash variation and its bracketed/quoted forms must be detected as dummy."""
        assert _is_null_or_dummy(dash_vector) is True, f"Dash vector {dash_vector!r} failed _is_null_or_dummy!"
        assert _normalize_string_list(dash_vector) == [], f"Dash vector {dash_vector!r} failed _normalize_string_list!"

    @pytest.mark.parametrize("dash_char", ["-", "–", "—"])
    def test_dash_in_yaml_frontmatter_fails_verified(self, tmp_path: Path, dash_char: str):
        """Frontmatter with sources: '-' or '–' or '—' must fail VERIFIED citation gating."""
        file_path = tmp_path / f"probe_dash_{ord(dash_char)}.md"
        content = (
            f"---\n"
            f"id: kb-probe-dash-{ord(dash_char)}\n"
            f"title: Dash Probe {ord(dash_char)}\n"
            f"category: research\n"
            f"verification_status: VERIFIED\n"
            f"confidence: 0.92\n"
            f"sources: '{dash_char}'\n"
            f"tags: [probe, dash]\n"
            f"---\n"
            f"Claiming verified facts with dash placeholder citation.\n"
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)

        assert is_valid is False, f"Dash character {dash_char!r} bypassed citation gating!"
        assert any("verifiable source citation" in err for err in errors)

    @pytest.mark.parametrize("dash_char", ["-", "–", "—"])
    def test_dash_in_block_sequence_fails_verified(self, tmp_path: Path, dash_char: str):
        """Block sequence with only dash placeholders must fail citation gating."""
        file_path = tmp_path / f"probe_block_dash_{ord(dash_char)}.md"
        content = (
            f"---\n"
            f"id: kb-probe-block-dash\n"
            f"title: Block Dash Probe\n"
            f"category: research\n"
            f"verification_status: VERIFIED\n"
            f"confidence: 0.92\n"
            f"sources:\n"
            f"  - '{dash_char}'\n"
            f"  - \"{dash_char}\"\n"
            f"tags: [probe, dash]\n"
            f"---\n"
            f"Block sequence with dash placeholders.\n"
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)
        assert is_valid is False
        assert any("verifiable source citation" in err for err in errors)


# ==============================================================================
# SECTION 4: PRESERVATION OF LEGITIMATE CITATIONS (ZERO FALSE POSITIVES)
# ==============================================================================

LEGITIMATE_CITATIONS = [
    # Book / guide titles with brackets
    "[YouTube Creator Playbook]",
    "[YouTube Creator Playbook 2024]",
    "[1]",
    "[2]",
    "[100]",
    "[IEEE-802.11]",
    "[RFC-822]",
    "[ISO-8601 Standard]",
    # URLs
    "https://www.youtube.com/creators/research",
    "https://support.google.com/youtube/answer/9014376",
    "https://developers.google.com/youtube/v3/docs/playlistItems",
    "https://tiktok.com/@creator_portal",
    "https://instagram.com/creators",
    "https://arxiv.org/abs/2401.0001",
    # URLs with bracketed query parameters or dashes
    "https://example.com/api?filter=[published]&status=active",
    "https://youtube.com/watch?v=dQw4w9WgXcQ&t=0s",
    "https://github.com/openai/whisper#benchmarks-and-accuracy",
    # Strings containing hyphens, en-dashes, and em-dashes inside text
    "YouTube Creator Liaison — Algorithmic Pacing Guide 2024",
    "MrBeast Retention Benchmark – 70% 30s Hold Rule",
    "Platform-Guideline-Revision-v3.2",
    "Section 4 — Viral Hooks & Curiosity Gaps",
    "Survey-Report-Q3-2024",
    # Complex citation references
    "YouTube Creator Liaison (2024) [Official Video]",
    "VidIQ Short-Form Retention Study (N=1,000,000)",
]


class TestLegitimateCitationsPreserved:
    """Ensures legitimate citations with brackets, dashes, and URLs are NEVER rejected."""

    @pytest.mark.parametrize("citation", LEGITIMATE_CITATIONS)
    def test_legitimate_citation_is_not_null_or_dummy(self, citation: str):
        """_is_null_or_dummy MUST return False for all authentic citation strings."""
        assert _is_null_or_dummy(citation) is False, (
            f"FALSE POSITIVE: Authentic citation {citation!r} was falsely classified as dummy!"
        )

    @pytest.mark.parametrize("citation", LEGITIMATE_CITATIONS)
    def test_legitimate_citation_preserved_in_metadata(self, citation: str):
        """KnowledgeMetadata must retain authentic citations without mangling."""
        meta = KnowledgeMetadata(
            id="kb-legit-citation",
            title="Legitimate Citation Test",
            category="youtube",
            verification_status="VERIFIED",
            confidence=0.95,
            sources=[citation],
            tags=["legit", "test"],
        )
        assert len(meta.sources) >= 1, f"Authentic citation {citation!r} was dropped from sources!"

    @pytest.mark.parametrize("citation", LEGITIMATE_CITATIONS)
    def test_legitimate_citation_passes_validation_in_file(self, tmp_path: Path, citation: str):
        """A VERIFIED document citing an authentic reference MUST pass validation."""
        file_path = tmp_path / f"probe_legit_{abs(hash(citation))}.md"
        # Escape quotes for YAML frontmatter if necessary
        safe_citation = citation.replace('"', '\\"')
        content = (
            f"---\n"
            f"id: kb-legit-{abs(hash(citation))}\n"
            f"title: Legitimate Reference Probe\n"
            f"category: youtube\n"
            f"verification_status: VERIFIED\n"
            f"confidence: 0.95\n"
            f"sources:\n"
            f"  - \"{safe_citation}\"\n"
            f"tags: [legit, test]\n"
            f"---\n"
            f"Document citing authentic authority {citation}.\n"
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)

        assert is_valid is True, (
            f"FALSE POSITIVE: Legitimate citation {citation!r} failed validation!\nErrors: {errors}"
        )
        assert errors == []


# ==============================================================================
# SECTION 5: MIXED SOURCES NORMALIZATION (DUMMY STRIPPING + CITATION RETENTION)
# ==============================================================================

class TestMixedSourcesNormalization:
    """Verifies that dummy values are stripped while genuine citations are preserved."""

    def test_mixed_list_strips_nested_dummies_and_keeps_authentic(self):
        """A list containing 50-layer brackets, nulls, dashes, and authentic URLs."""
        deep_dummy_1 = make_nested_brackets("~", 50)
        deep_dummy_2 = make_nested_brackets("null", 30)
        deep_dummy_3 = make_nested_brackets("-", 20)
        authentic_url = "https://youtube.com/creators/retention"
        authentic_guide = "[YouTube Creator Playbook]"

        raw_list = [
            deep_dummy_1,
            authentic_url,
            deep_dummy_2,
            "-",
            "—",
            deep_dummy_3,
            authentic_guide,
            "~",
            "",
        ]

        normalized = _normalize_string_list(raw_list)

        assert authentic_url in normalized
        assert any("YouTube Creator Playbook" in item for item in normalized)
        assert deep_dummy_1 not in normalized
        assert deep_dummy_2 not in normalized
        assert deep_dummy_3 not in normalized
        assert "-" not in normalized
        assert "—" not in normalized

    def test_mixed_sources_in_verified_file_passes_validation(self, tmp_path: Path):
        """VERIFIED entry with mixed dummy and legitimate sources must pass validation."""
        file_path = tmp_path / "mixed_sources_doc.md"
        content = (
            "---\n"
            "id: kb-mixed-valid\n"
            "title: Mixed Sources Valid Doc\n"
            "category: research\n"
            "verification_status: VERIFIED\n"
            "confidence: 0.90\n"
            "sources:\n"
            "  - '[[[[[[[[[[~]]]]]]]]]]'\n"
            "  - 'https://youtube.com/creators'\n"
            "  - '—'\n"
            "  - '[YouTube Creator Playbook]'\n"
            "tags: [mixed, valid]\n"
            "---\n"
            "Authentic document with mixed citations.\n"
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)

        assert is_valid is True, f"Failed mixed sources validation: {errors}"
        entry = parse_knowledge_file(file_path)
        assert len(entry.metadata.sources) >= 1
        assert "https://youtube.com/creators" in entry.metadata.sources


# ==============================================================================
# SECTION 6: HIGH-CONCURRENCY CAS TASK REGISTRY & LOGGER INVARIANTS
# ==============================================================================

class TestHighConcurrencyCASAndLogger:
    """Stress-tests TaskManager CAS concurrency and MessageLogger streaming under high load."""

    def test_concurrent_cas_contention_100_workers(self, tmp_path: Path):
        """
        Adversarial Scenario:
        100 threads race to claim a single task concurrently.
        Invariant:
        Exactly 1 succeeds, exactly 99 raise TaskCollisionError, zero database corruption.
        """
        db_path = tmp_path / "cas_stress_it4.sqlite"
        task_md = tmp_path / "task-ownership.md"
        manager = TaskManager(db_path=db_path, task_md=task_md)

        with manager._get_conn() as conn:
            conn.execute(
                "INSERT INTO task_registry (task_id, task_name, owner, status) VALUES (?, ?, NULL, 'PENDING')",
                ("TASK-CONCURRENCY-IT4", "Iteration 4 Concurrency Probe"),
            )
            conn.commit()

        start_gate = threading.Barrier(100)
        winners: List[Tuple[str, str]] = []
        collisions = 0
        collision_lock = threading.Lock()
        unexpected_errors: List[Exception] = []

        def worker_task(worker_id: int):
            agent_id = f"agent-it4-{worker_id:03d}"
            try:
                start_gate.wait(timeout=10.0)
                tok = manager.claim_task("TASK-CONCURRENCY-IT4", agent_id=agent_id, lease_seconds=60)
                with collision_lock:
                    winners.append((agent_id, tok))
            except TaskCollisionError:
                with collision_lock:
                    nonlocal collisions
                    collisions += 1
            except Exception as exc:
                with collision_lock:
                    unexpected_errors.append(exc)

        threads = [threading.Thread(target=worker_task, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)

        assert len(unexpected_errors) == 0, f"Unexpected errors: {unexpected_errors}"
        assert len(winners) == 1, f"Expected 1 winner, got {len(winners)}"
        assert collisions == 99, f"Expected 99 collisions, got {collisions}"

        # Verify database state
        task = manager.get_task("TASK-CONCURRENCY-IT4")
        assert task is not None
        assert task["status"] == "IN_PROGRESS"
        assert task["owner"] == winners[0][0]
        assert task["lock_token"] == winners[0][1]

    def test_concurrent_message_logger_streaming_zero_corruption(self, tmp_path: Path):
        """
        Adversarial Scenario:
        40 concurrent worker threads stream 25 messages each (1,000 total messages)
        into MessageLogger concurrently.
        Invariant:
        Exactly 1,000 JSONL lines written, zero json decode errors, zero data corruption.
        """
        log_file = tmp_path / "agent-comms-it4.jsonl"
        logger = MessageLogger(log_path=log_file)
        start_gate = threading.Barrier(40)

        def sender_task(worker_id: int):
            start_gate.wait(timeout=10.0)
            for msg_idx in range(25):
                msg = AgentMessage(
                    sender="agent-1-research",
                    recipient="orchestrator",
                    task_id=f"TASK-IT4-{worker_id:02d}",
                    action=AgentAction.HEARTBEAT.value,
                    payload={"worker_id": worker_id, "seq": msg_idx, "load": "adversarial_payload"},
                    confidence=ConfidenceLevel.VERIFIED.value,
                    sources=["https://internal.benchmarks/it4"],
                )
                logger.log(msg)

        threads = [threading.Thread(target=sender_task, args=(i,)) for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)

        # Read back and verify all lines
        import json
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1000, f"Expected 1000 logged lines, got {len(lines)}"

        for line_num, line in enumerate(lines, start=1):
            try:
                data = json.loads(line)
                assert "message_id" in data
                assert "sender" in data
                assert "timestamp" in data
            except json.JSONDecodeError as exc:
                pytest.fail(f"Corrupted JSONL at line {line_num}: {line!r} — {exc}")
