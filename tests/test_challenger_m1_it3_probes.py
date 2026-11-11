"""
Empirical Adversarial Verification Suite: Milestone 1 Iteration 3
Authored by: Milestone 1 Challenger 2 (Iteration 3)

Exhaustively stress-tests:
1. Every permutation of null tokens, scalars, and case variations in sources
2. Flow sequences with single and multiple null items
3. Block sequence variations and mixed null/valid sources
4. Category directory boundary enforcement across all 8 canonical categories and invalid folder placements
5. Search ranking algorithms, acronym preservation, and verification multipliers
6. Knowledge base audit and SQLite index integrity
"""

import re
import sqlite3
import pytest
from pathlib import Path
from agent.modules.knowledge import (
    _parse_yaml_frontmatter,
    _serialize_yaml_frontmatter,
    _normalize_string_list,
    _is_null_or_dummy,
    DUMMY_NULL_VALUES,
    KnowledgeCategory,
    KnowledgeEntry,
    KnowledgeMetadata,
    VerificationStatus,
    parse_knowledge_file,
    validate_knowledge_file,
    write_knowledge,
    search_knowledge_text,
    query_knowledge,
    audit_knowledge_base,
    sync_to_db,
    VALID_CATEGORIES,
    VALID_OWNERS,
)


# ─── 1. EXHAUSTIVE NULL SCALARS & STRINGS ────────────────────────────────────

NULL_TOKEN_VARIATIONS = [
    "~",
    "null",
    "Null",
    "NULL",
    "none",
    "None",
    "NONE",
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
    "",
    "   ",
    "[]",
    "[ ]",
    "[   ]",
]


class TestExhaustiveNullScalarCitations:
    """Probes all null scalars and string tokens to ensure VERIFIED entries reject them."""

    @pytest.mark.parametrize("token", NULL_TOKEN_VARIATIONS)
    def test_null_scalar_in_yaml_fails_validation(self, tmp_path, token):
        """Unquoted scalar in frontmatter must fail citation gate for VERIFIED."""
        file_path = tmp_path / "probe_null_scalar.md"
        content = (
            f"---\n"
            f"id: kb-probe-scalar\n"
            f"title: Null Scalar Probe\n"
            f"category: youtube\n"
            f"verification_status: VERIFIED\n"
            f"confidence: 0.95\n"
            f"sources: {token}\n"
            f"tags: [probe, test]\n"
            f"---\n"
            f"Testing unquoted scalar citation rejection."
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)

        assert is_valid is False, f"Null scalar '{token}' bypassed citation validation!"
        assert any("verifiable source citation" in err for err in errors), (
            f"Expected citation error for '{token}', got: {errors}"
        )

    @pytest.mark.parametrize("token", NULL_TOKEN_VARIATIONS)
    def test_quoted_null_scalar_in_yaml_fails_validation(self, tmp_path, token):
        """Single- and double-quoted null token in frontmatter must fail citation gate."""
        for idx, quote in enumerate(("'", '"')):
            quote_tag = "single" if quote == "'" else "double"
            file_path = tmp_path / f"probe_quoted_{quote_tag}_{idx}.md"
            content = (
                f"---\n"
                f"id: kb-probe-quoted\n"
                f"title: Quoted Null Probe\n"
                f"category: youtube\n"
                f"verification_status: VERIFIED\n"
                f"confidence: 0.95\n"
                f"sources: {quote}{token}{quote}\n"
                f"tags: [probe, test]\n"
                f"---\n"
                f"Testing quoted scalar citation rejection."
            )
            file_path.write_text(content, encoding="utf-8")
            is_valid, errors = validate_knowledge_file(file_path)

            assert is_valid is False, f"Quoted token {quote}{token}{quote} bypassed validation!"
            assert any("verifiable source citation" in err for err in errors)

    @pytest.mark.parametrize("token", NULL_TOKEN_VARIATIONS)
    def test_is_null_or_dummy_recognizes_all_tokens(self, token):
        """Internal helper _is_null_or_dummy must identify all null tokens."""
        assert _is_null_or_dummy(token) is True, f"_is_null_or_dummy({token!r}) returned False"
        assert _is_null_or_dummy(f"'{token}'") is True
        assert _is_null_or_dummy(f'"{token}"') is True
        if not (token.startswith("[") and token.endswith("]")):
            assert _is_null_or_dummy(f"[{token}]") is True


# ─── 2. FLOW SEQUENCES WITH SINGLE & MULTIPLE NULL ITEMS ─────────────────────

FLOW_SEQUENCE_VARIATIONS = [
    # Single null item
    "[~]",
    "['~']",
    '["~"]',
    "[none]",
    "['none']",
    '["none"]',
    "[None]",
    "['None']",
    '["None"]',
    "[NONE]",
    "['NONE']",
    '["NONE"]',
    "[null]",
    "['null']",
    '["null"]',
    "[Null]",
    "['Null']",
    '["Null"]',
    "[NULL]",
    "['NULL']",
    '["NULL"]',
    "[nil]",
    "['nil']",
    '["nil"]',
    "[undefined]",
    "['undefined']",
    '["undefined"]',
    "[n/a]",
    "['n/a']",
    '["n/a"]',
    "[na]",
    "['na']",
    '["na"]',
    "[]",
    "[ ]",
    "[   ]",
    "['']",
    '[""]',
    "['   ']",
    '["   "]',
    "['[]']",
    '["[]"]',
    "['[ ]']",
    '["[ ]"]',
    # Multiple null items
    "[~, none]",
    "[null, ~]",
    "['~', \"none\", null]",
    "[nil, undefined, n/a, na]",
    '["", "   ", "[]", None, ~]',
    '["None", "null", "undefined"]',
    "[~, '']",
    '["~", "~"]',
    "[none, Null, NULL, NONE]",
    "['na', 'n/a', 'nil', '~']",
    "['[]', '[ ]', '', '  ', null]",
    "[undefined, None, 'null', ~]",
]


class TestFlowSequenceNullProbes:
    """Probes bracketed flow sequences containing one or multiple dummy/null items."""

    @pytest.mark.parametrize("flow_seq", FLOW_SEQUENCE_VARIATIONS)
    def test_flow_sequence_fails_validation_for_verified(self, tmp_path, flow_seq):
        """Every flow sequence permutation of null items must be rejected for VERIFIED."""
        file_path = tmp_path / "probe_flow.md"
        content = (
            f"---\n"
            f"id: kb-probe-flow\n"
            f"title: Flow Sequence Null Probe\n"
            f"category: research\n"
            f"verification_status: VERIFIED\n"
            f"confidence: 0.88\n"
            f"sources: {flow_seq}\n"
            f"tags: [flow, test]\n"
            f"---\n"
            f"Testing flow sequence dummy citation rejection."
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)

        assert is_valid is False, f"Flow sequence '{flow_seq}' bypassed validation!"
        assert any("verifiable source citation" in err for err in errors), (
            f"Expected citation error for '{flow_seq}', got: {errors}"
        )

    @pytest.mark.parametrize("flow_seq", FLOW_SEQUENCE_VARIATIONS)
    def test_metadata_normalizes_flow_sequence_to_empty_list(self, flow_seq):
        """KnowledgeMetadata must sanitize all null flow sequence representations to []."""
        meta = KnowledgeMetadata(
            id="kb-meta-flow",
            title="Flow Meta Test",
            category="research",
            verification_status="VERIFIED",
            confidence=0.90,
            sources=flow_seq,
            tags=["test"],
        )
        assert meta.sources == [], f"Failed to normalize {flow_seq!r} to empty list: got {meta.sources}"


# ─── 2B. DEEPLY NESTED BRACKET DUMMY CITATION BYPASS PROBES ────────────────

NESTED_BRACKET_BYPASS_VECTORS = [
    "[[[~]]]",
    "[[[[~]]]]",
    "[[[[]]]]",
    "[[[[[]]]]]",
    "[[[none]]]",
    "[[[[none]]]]",
    "[[[null]]]",
    "[[[[null]]]]",
    "[[[nil]]]",
    "[[[undefined]]]",
    "[[[n/a]]]",
    "[[[na]]]",
]


class TestDeeplyNestedBracketCitationBypasses:
    """
    Exhaustively probes deeply nested bracket flow sequences.
    Empirically exposes that _is_null_or_dummy() only strips one layer of brackets,
    allowing 3+ nested bracket dummy tokens to bypass citation enforcement.
    """

    @pytest.mark.parametrize("nested_token", NESTED_BRACKET_BYPASS_VECTORS)
    def test_nested_bracket_dummy_sources_must_fail_for_verified(self, tmp_path, nested_token):
        """Deeply nested brackets around null tokens or empty brackets MUST fail citation gating."""
        file_path = tmp_path / f"probe_nested_{abs(hash(nested_token))}.md"
        content = (
            f"---\n"
            f"id: kb-probe-nested-bypass\n"
            f"title: Nested Bracket Citation Bypass Probe\n"
            f"category: youtube\n"
            f"verification_status: VERIFIED\n"
            f"confidence: 0.95\n"
            f"sources: {nested_token}\n"
            f"tags: [probe, bypass, test]\n"
            f"---\n"
            f"Claiming verified facts with deeply nested dummy brackets {nested_token}."
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)

        assert is_valid is False, (
            f"CRITICAL VULNERABILITY: Nested bracket snippet {nested_token} bypassed citation gating!\n"
            f"is_valid: {is_valid}, errors: {errors}"
        )
        assert any("verifiable source citation" in err for err in errors), (
            f"Expected citation error for {nested_token}, got: {errors}"
        )

    @pytest.mark.parametrize("nested_token", NESTED_BRACKET_BYPASS_VECTORS)
    def test_is_null_or_dummy_must_recognize_nested_brackets(self, nested_token):
        """_is_null_or_dummy must iteratively unwrap nested brackets and identify nulls."""
        assert _is_null_or_dummy(nested_token) is True, (
            f"_is_null_or_dummy({nested_token!r}) failed to recognize nested dummy value!"
        )


# ─── 3. BLOCK SEQUENCES AND MIXED VALID/NULL CITATIONS ───────────────────────

BLOCK_SEQUENCE_NULL_VARIATIONS = [
    "sources:\n  - ~\n",
    "sources:\n  - 'null'\n",
    "sources:\n  - none\n",
    "sources:\n  - Null\n",
    "sources:\n  - NULL\n",
    "sources:\n  - nil\n",
    "sources:\n  - undefined\n",
    "sources:\n  - n/a\n",
    "sources:\n  - na\n",
    "sources:\n  - ~\n  - none\n",
    "sources:\n  - Null\n  - NULL\n  - None\n",
    "sources:\n  - 'nil'\n  - 'undefined'\n  - 'n/a'\n  - 'na'\n",
    "sources:\n  - '[]'\n  - '[ ]'\n  - ''\n  - '  '\n",
    "sources:\n  - ~\n  - undefined\n  - [ ]\n",
    "sources:\n  - \"null\"\n  - \"none\"\n  - \"~\"\n",
]


class TestBlockSequenceAndMixedCitations:
    """Probes YAML block sequence parsing and handling of mixed dummy/authentic sources."""

    @pytest.mark.parametrize("block_seq", BLOCK_SEQUENCE_NULL_VARIATIONS)
    def test_block_sequence_all_nulls_rejected(self, tmp_path, block_seq):
        """Block sequence containing only null items must fail validation."""
        file_path = tmp_path / "probe_block.md"
        content = (
            f"---\n"
            f"id: kb-probe-block\n"
            f"title: Block Sequence Null Probe\n"
            f"category: youtube\n"
            f"verification_status: VERIFIED\n"
            f"confidence: 0.92\n"
            f"{block_seq}"
            f"tags: [block, test]\n"
            f"---\n"
            f"Testing block sequence dummy citation rejection."
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)

        assert is_valid is False, f"Block sequence bypassed validation:\n{block_seq}"
        assert any("verifiable source citation" in err for err in errors)

    @pytest.mark.parametrize(
        "mixed_sources_yaml,expected_valid_source",
        [
            ("sources: [~, https://example.com/api]", "https://example.com/api"),
            ("sources: ['none', 'https://developers.google.com/youtube']", "https://developers.google.com/youtube"),
            ("sources: [null, ~ , 'https://arxiv.org/abs/1234']", "https://arxiv.org/abs/1234"),
            (
                "sources:\n  - ~\n  - https://example.com/guide\n  - none\n",
                "https://example.com/guide",
            ),
            (
                "sources:\n  - '[]'\n  - https://groq.com/pricing\n  - '   '\n",
                "https://groq.com/pricing",
            ),
        ],
    )
    def test_mixed_sources_retains_valid_citation_and_passes(
        self, tmp_path, mixed_sources_yaml, expected_valid_source
    ):
        """When at least one authentic citation accompanies dummy tokens, validation succeeds."""
        file_path = tmp_path / "probe_mixed.md"
        content = (
            f"---\n"
            f"id: kb-probe-mixed\n"
            f"title: Mixed Citation Probe\n"
            f"category: youtube\n"
            f"verification_status: VERIFIED\n"
            f"confidence: 0.85\n"
            f"{mixed_sources_yaml}\n"
            f"tags: [mixed, test]\n"
            f"---\n"
            f"Content with authentic source alongside dummy tokens."
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)

        assert is_valid is True, f"Mixed valid source unexpectedly failed: {errors}"
        entry = parse_knowledge_file(file_path)
        assert expected_valid_source in entry.metadata.sources
        assert not any(_is_null_or_dummy(s) for s in entry.metadata.sources)


# ─── 4. CATEGORY DIRECTORY BOUNDARY ENFORCEMENT ──────────────────────────────

class TestCategoryDirectoryBoundaries:
    """
    Exhaustively tests boundary alignment between filesystem directory and frontmatter category.
    Covers all 8 canonical categories, root placement, and invalid category directories.
    """

    @pytest.mark.parametrize("category", sorted(VALID_CATEGORIES))
    def test_category_in_matching_folder_passes(self, tmp_path, category):
        """File placed in knowledge/<category>/ with matching category passes."""
        kb_root = tmp_path / "knowledge"
        cat_dir = kb_root / category
        cat_dir.mkdir(parents=True)

        file_path = cat_dir / "valid_category.md"
        content = (
            f"---\n"
            f"id: kb-cat-valid-{category}\n"
            f"title: Valid Category Test {category}\n"
            f"category: {category}\n"
            f"verification_status: EXPERIMENTAL\n"
            f"confidence: 0.70\n"
            f"tags: [test]\n"
            f"---\n"
            f"Content."
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)
        assert is_valid is True, f"Valid placement in {category} failed: {errors}"

    @pytest.mark.parametrize("category", sorted(VALID_CATEGORIES))
    def test_category_in_wrong_folder_fails(self, tmp_path, category):
        """File placed in wrong category folder fails directory alignment check."""
        kb_root = tmp_path / "knowledge"
        # Pick another category that does not match
        all_cats = sorted(VALID_CATEGORIES)
        wrong_cat = all_cats[(all_cats.index(category) + 1) % len(all_cats)]

        wrong_dir = kb_root / wrong_cat
        wrong_dir.mkdir(parents=True)

        file_path = wrong_dir / f"mismatched_{category}.md"
        content = (
            f"---\n"
            f"id: kb-cat-wrong-{category}\n"
            f"title: Mismatched Category Test {category}\n"
            f"category: {category}\n"
            f"verification_status: EXPERIMENTAL\n"
            f"confidence: 0.70\n"
            f"tags: [test]\n"
            f"---\n"
            f"Content."
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)

        assert is_valid is False
        assert any(
            f"File directory '{wrong_cat}' does not match metadata category '{category}'" in err
            for err in errors
        )

    def test_file_directly_in_knowledge_root_fails(self, tmp_path):
        """File placed directly under knowledge/ (not inside a category subfolder) must fail."""
        kb_root = tmp_path / "knowledge"
        kb_root.mkdir(parents=True)

        file_path = kb_root / "root_misplaced.md"
        content = (
            f"---\n"
            f"id: kb-cat-root-fail\n"
            f"title: Root Misplaced File\n"
            f"category: youtube\n"
            f"verification_status: EXPERIMENTAL\n"
            f"confidence: 0.70\n"
            f"tags: [test]\n"
            f"---\n"
            f"Content directly under knowledge/ root."
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)

        assert is_valid is False
        assert any(
            "File directory 'knowledge' does not match metadata category 'youtube'" in err
            for err in errors
        )

    def test_file_in_nonexistent_category_dir_fails(self, tmp_path):
        """File placed under knowledge/bogus-subfolder/ with valid category must fail."""
        kb_root = tmp_path / "knowledge"
        bogus_dir = kb_root / "bogus-folder"
        bogus_dir.mkdir(parents=True)

        file_path = bogus_dir / "bogus_placement.md"
        content = (
            f"---\n"
            f"id: kb-cat-bogus\n"
            f"title: Bogus Folder File\n"
            f"category: youtube\n"
            f"verification_status: EXPERIMENTAL\n"
            f"confidence: 0.70\n"
            f"tags: [test]\n"
            f"---\n"
            f"Content."
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)

        assert is_valid is False
        assert any("does not match metadata category 'youtube'" in err for err in errors)


# ─── 5. SEARCH RELEVANCE, RANKING & ACRONYM PRESERVATION ─────────────────────

class TestSearchRankingAndAcronymPreservation:
    """
    Stress-tests search ranking algorithm:
    - Weight ordering: Title (5.0) > Tags (3.0) > Body (0.5)
    - VERIFIED multiplier: 1.2x boost
    - Acronym preservation (2-letter words like AI, QA, UI, 4K, HD, DB)
    - Word boundary safety: no substring false matches
    """

    @pytest.fixture
    def ranking_kb(self, tmp_path):
        kb_root = tmp_path / "knowledge"
        (kb_root / "research").mkdir(parents=True)
        (kb_root / "creative-production").mkdir(parents=True)
        (kb_root / "ai-providers").mkdir(parents=True)

        # Doc 1: "pipeline" in TITLE (Weight 5.0)
        f1 = kb_root / "research" / "doc_title.md"
        f1.write_text(
            "---\n"
            "id: kb-rank-title\n"
            "title: Complete Video Pipeline Architecture\n"
            "category: research\n"
            "verification_status: EXPERIMENTAL\n"
            "confidence: 1.0\n"
            "tags: [video, architecture]\n"
            "---\n"
            "General text body without keyword.",
            encoding="utf-8",
        )

        # Doc 2: "pipeline" in TAGS (Weight 3.0)
        f2 = kb_root / "creative-production" / "doc_tag.md"
        f2.write_text(
            "---\n"
            "id: kb-rank-tag\n"
            "title: Creative Workflow Design\n"
            "category: creative-production\n"
            "verification_status: EXPERIMENTAL\n"
            "confidence: 1.0\n"
            "tags: [pipeline, workflow]\n"
            "---\n"
            "General text body without keyword.",
            encoding="utf-8",
        )

        # Doc 3: "pipeline" in BODY ONLY (Weight 0.5)
        f3 = kb_root / "ai-providers" / "doc_body.md"
        f3.write_text(
            "---\n"
            "id: kb-rank-body\n"
            "title: Model Provider Endpoints\n"
            "category: ai-providers\n"
            "verification_status: EXPERIMENTAL\n"
            "confidence: 1.0\n"
            "tags: [models, endpoints]\n"
            "---\n"
            "Using this provider pipeline for inference.",
            encoding="utf-8",
        )

        return kb_root

    def test_search_weight_hierarchy_title_over_tag_over_body(self, ranking_kb):
        """Results must rank Title match > Tag match > Body match when confidence is equal."""
        results = search_knowledge_text("pipeline", kb_root=ranking_kb)
        assert len(results) == 3
        assert results[0].metadata.id == "kb-rank-title"
        assert results[1].metadata.id == "kb-rank-tag"
        assert results[2].metadata.id == "kb-rank-body"

    def test_verified_status_multiplier_boosts_ranking(self, tmp_path):
        """VERIFIED entry (1.2x boost) ranks above EXPERIMENTAL entry with equal weights."""
        kb_root = tmp_path / "knowledge"
        (kb_root / "youtube").mkdir(parents=True)

        # Doc A: EXPERIMENTAL with title match -> score 5.0 * 1.0 * 1.0 = 5.0
        fa = kb_root / "youtube" / "exp.md"
        fa.write_text(
            "---\n"
            "id: kb-boost-exp\n"
            "title: Retention Optimization Strategy\n"
            "category: youtube\n"
            "verification_status: EXPERIMENTAL\n"
            "confidence: 1.0\n"
            "tags: [strategy]\n"
            "---\n"
            "Body.",
            encoding="utf-8",
        )

        # Doc B: VERIFIED with title match -> score 5.0 * 1.2 * 1.0 = 6.0
        fb = kb_root / "youtube" / "ver.md"
        fb.write_text(
            "---\n"
            "id: kb-boost-ver\n"
            "title: Retention Optimization Benchmarks\n"
            "category: youtube\n"
            "verification_status: VERIFIED\n"
            "confidence: 1.0\n"
            "sources: [https://youtube.com/creators]\n"
            "tags: [benchmarks]\n"
            "---\n"
            "Body.",
            encoding="utf-8",
        )

        results = search_knowledge_text("retention", kb_root=kb_root)
        assert len(results) == 2
        assert results[0].metadata.id == "kb-boost-ver", (
            f"Expected VERIFIED doc to rank first due to 1.2x multiplier, got: {results[0].metadata.id}"
        )
        assert results[1].metadata.id == "kb-boost-exp"

    @pytest.mark.parametrize("acronym", ["AI", "QA", "UI", "DB"])
    def test_acronym_preservation_in_seed_knowledge(self, acronym):
        """All critical 2-letter domain acronyms present in seed KB are queryable."""
        results = search_knowledge_text(acronym)
        assert len(results) > 0, f"Acronym query '{acronym}' returned 0 results from seed KB"

    @pytest.mark.parametrize("acronym", ["AI", "QA", "UI", "4K", "HD", "DB"])
    def test_all_domain_acronyms_queryable_in_custom_kb(self, tmp_path, acronym):
        """2-letter domain acronyms (AI, QA, UI, 4K, HD, DB) are indexed and queryable."""
        kb_root = tmp_path / "knowledge"
        (kb_root / "creative-production").mkdir(parents=True)
        file_path = kb_root / "creative-production" / "specs.md"
        file_path.write_text(
            "---\n"
            "id: kb-custom-acronyms\n"
            "title: Production Guide for 4K and HD Video with AI QA UI and DB\n"
            "category: creative-production\n"
            "verification_status: VERIFIED\n"
            "confidence: 0.90\n"
            "sources: [https://example.com/specs]\n"
            "tags: [4k, hd, ui, ai, qa, db]\n"
            "---\n"
            "Body mentioning 4K resolution, HD audio, AI pipelines, QA reviews, UI safe zones, and DB caching.",
            encoding="utf-8",
        )
        results = search_knowledge_text(acronym, kb_root=kb_root)
        assert len(results) > 0, f"Acronym query '{acronym}' failed in custom KB"
        assert results[0].metadata.id == "kb-custom-acronyms"

    def test_single_letter_queries_suppressed(self):
        """Single character noise queries ('a', 'x', '1') return 0 results."""
        assert len(search_knowledge_text("a")) == 0
        assert len(search_knowledge_text("x")) == 0
        assert len(search_knowledge_text("1")) == 0


# ─── 6. AUDIT & SQLITE SYNC INTEGRITY ────────────────────────────────────────

class TestAuditAndSqliteSync:
    """Verifies that audit_knowledge_base() and sync_to_db() operate with zero errors."""

    def test_seed_knowledge_audit_has_zero_errors(self):
        """Auditing current knowledge base must yield 8 entries and 0 validation errors."""
        summary = audit_knowledge_base()
        assert summary["total_entries"] >= 8
        assert summary["errors"] == [], f"Validation errors found in seed KB: {summary['errors']}"
        assert summary["unverified_count"] == 0

    def test_sqlite_sync_populates_all_columns_accurately(self, tmp_path):
        """sync_to_db stores JSON tags and sources without corrupting strings."""
        db_file = tmp_path / "test_knowledge.sqlite"
        synced_count = sync_to_db(db_path=db_file)
        assert synced_count >= 8

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        rows = cursor.execute("SELECT id, category, sources, tags, verification_status FROM knowledge_index").fetchall()
        assert len(rows) == synced_count

        for row in rows:
            entry_id, cat, sources_json, tags_json, v_status = row
            assert cat in VALID_CATEGORIES
            assert v_status in {s.value for s in VerificationStatus}
            # Verify JSON deserializability
            import json
            sources = json.loads(sources_json)
            tags = json.loads(tags_json)
            assert isinstance(sources, list)
            assert isinstance(tags, list)
            if v_status == "VERIFIED":
                assert len(sources) >= 1
                assert not any(_is_null_or_dummy(s) for s in sources)
        conn.close()
