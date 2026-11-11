"""
Empirical Adversarial Verification Suite: Milestone 1 Iteration 2
Authored by: Milestone 1 Challenger 2 (Iteration 2)

Rigorously stress-tests the remediated Knowledge Base schema, citation gating,
fallback YAML frontmatter parsing, category-folder alignment, and search engine.
"""

import re
import pytest
from pathlib import Path
from agent.modules.knowledge import (
    _parse_yaml_frontmatter,
    _serialize_yaml_frontmatter,
    _normalize_string_list,
    KnowledgeCategory,
    KnowledgeEntry,
    KnowledgeMetadata,
    VerificationStatus,
    parse_knowledge_file,
    validate_knowledge_file,
    search_knowledge_text,
    query_knowledge,
    VALID_CATEGORIES,
)


# ─── 1. CITATION BYPASS DEFENSE PROBES ───────────────────────────────────────

class TestCitationBypassDefenses:
    """
    Exhaustively probe all variations of empty, whitespace, dummy, or pseudo-empty
    sources to verify that VERIFIED entries cannot bypass citation enforcement.
    """

    @pytest.mark.parametrize(
        "sources_yaml_snippet",
        [
            "sources: []",
            "sources: [ ]",
            "sources: [   ]",
            "sources: ['']",
            'sources: [""]',
            'sources: ["[]"]',
            "sources: ['[]']",
            'sources: ["[ ]"]',
            "sources: [null]",
            "sources: [None]",
            'sources: ["null"]',
            'sources: ["None"]',
            'sources: ""',
            'sources: "   "',
            "sources: null",
            "sources: Null",
            "sources: NULL",
            "sources: none",
            "sources: NONE",
            "sources: ~",
            "sources: [none]",
            "sources: [NONE]",
            "sources: [Null]",
            "sources: [NULL]",
            "sources: [~]",
            "sources:\n",
            "sources:\n  # only comments\n",
            "sources:\n  - ''\n  - '  '\n  - '[]'\n  - null\n",
            "source: ''",
            "source: '[]'",
            "source: null",
        ],
    )
    def test_sources_variations_fail_for_verified_status(self, tmp_path, sources_yaml_snippet):
        """Every variation of missing, dummy, or empty sources must fail validation for VERIFIED."""
        file_path = tmp_path / "test_sources_probe.md"
        content = (
            f"---\n"
            f"id: kb-probe-bypass\n"
            f"title: Adversarial Citation Bypass Probe\n"
            f"category: youtube\n"
            f"verification_status: VERIFIED\n"
            f"confidence: 0.95\n"
            f"{sources_yaml_snippet}\n"
            f"tags: [shorts, test]\n"
            f"---\n"
            f"Body text claiming verified facts without genuine citation."
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)

        assert is_valid is False, f"Bypass succeeded for snippet:\n{sources_yaml_snippet}\nErrors: {errors}"
        assert any("verifiable source citation" in err for err in errors), (
            f"Expected citation error for snippet:\n{sources_yaml_snippet}\nGot: {errors}"
        )

    @pytest.mark.parametrize(
        "raw_sources_input",
        [
            [],
            [""],
            ["   "],
            ["[]"],
            ["[ ]"],
            ["None"],
            ["null"],
            [None],
            "",
            "   ",
            "[]",
            "[ ]",
            "None",
            "null",
        ],
    )
    def test_metadata_instantiation_normalizes_dummy_sources_to_empty(self, raw_sources_input):
        """KnowledgeMetadata must sanitize dummy source representations to an empty list."""
        m = KnowledgeMetadata(
            id="kb-meta-sources",
            title="Metadata Direct Sources Test",
            category="youtube",
            verification_status="VERIFIED",
            confidence=0.90,
            sources=raw_sources_input,
            tags=["test"],
        )
        assert m.sources == [], f"Failed to normalize {raw_sources_input!r} to empty list: got {m.sources}"

    def test_genuine_citation_passes_validation(self, tmp_path):
        """A genuine citation must pass validation when confidence >= 0.80."""
        file_path = tmp_path / "test_valid_verified.md"
        content = (
            "---\n"
            "id: kb-probe-valid\n"
            "title: Valid Verified Entry\n"
            "category: youtube\n"
            "verification_status: VERIFIED\n"
            "confidence: 0.90\n"
            "sources:\n"
            "  - https://developers.google.com/youtube/v3/docs\n"
            "tags: [youtube, api]\n"
            "---\n"
            "Verified body content with authentic documentation link."
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)
        assert is_valid is True, f"Valid entry unexpectedly failed: {errors}"
        assert errors == []

    @pytest.mark.parametrize("status", ["HYPOTHESIS", "EXPERIMENTAL"])
    def test_unverified_statuses_allow_empty_sources(self, tmp_path, status):
        """HYPOTHESIS and EXPERIMENTAL entries do not require sources citations."""
        file_path = tmp_path / f"test_{status.lower()}.md"
        content = (
            f"---\n"
            f"id: kb-probe-{status.lower()}\n"
            f"title: Exploratory {status} Entry\n"
            f"category: research\n"
            f"verification_status: {status}\n"
            f"confidence: 0.50\n"
            f"sources: []\n"
            f"tags: [exploratory, test]\n"
            f"---\n"
            f"Speculative research body."
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)
        assert is_valid is True, f"{status} entry unexpectedly rejected: {errors}"


# ─── 2. INLINE LIST PARSING & CHARACTER EXPLOSION PROBES ─────────────────────

class TestInlineListParsingAndCharacterExplosion:
    """
    Rigorously test frontmatter flow sequences and metadata list conversions
    to guarantee no character explosions or corrupted list representations occur.
    """

    @pytest.mark.parametrize(
        "yaml_line,expected_tags",
        [
            ("tags: [a, b]", ["a", "b"]),
            ("tags: [ alpha ,  beta , gamma ]", ["alpha", "beta", "gamma"]),
            ('tags: ["foo", "bar"]', ["foo", "bar"]),
            ("tags: ['single']", ["single"]),
            ("tags: [alone]", ["alone"]),
            ("tags: []", []),
            ("tags: [   ]", []),
            ("tags: [a, 'b, c']", ["a", "b", "c"]),  # split inner items
        ],
    )
    def test_fallback_yaml_parser_inline_lists(self, yaml_line, expected_tags):
        """Fallback parser correctly parses bracketed flow sequences into list of strings."""
        text = (
            f"---\n"
            f"id: kb-inline-test\n"
            f"title: Inline List Test\n"
            f"category: youtube\n"
            f"{yaml_line}\n"
            f"---\n"
            f"Body"
        )
        meta_dict, _ = _parse_yaml_frontmatter(text)
        assert meta_dict["tags"] == expected_tags

    def test_metadata_avoids_character_explosion_on_raw_strings(self):
        """Passing a raw string to KnowledgeMetadata must NOT explode into characters."""
        # Single string tag
        m1 = KnowledgeMetadata(
            id="kb-str-1",
            title="Single String Tag",
            category="youtube",
            tags="shorts",
        )
        assert m1.tags == ["shorts"]
        assert "[" not in m1.tags
        assert len(m1.tags) == 1

        # Comma-separated string tag
        m2 = KnowledgeMetadata(
            id="kb-str-2",
            title="Comma String Tag",
            category="youtube",
            tags="shorts, viral, algorithm",
        )
        assert m2.tags == ["shorts", "viral", "algorithm"]

        # Single string applies_to
        m3 = KnowledgeMetadata(
            id="kb-str-3",
            title="Single String applies_to",
            category="youtube",
            tags=["test"],
            applies_to="youtube",
        )
        assert m3.applies_to == ["youtube"]
        assert len(m3.applies_to) == 1

        # Comma-separated applies_to
        m4 = KnowledgeMetadata(
            id="kb-str-4",
            title="Comma applies_to",
            category="youtube",
            tags=["test"],
            applies_to="youtube, instagram, tiktok",
        )
        assert m4.applies_to == ["youtube", "instagram", "tiktok"]

    def test_roundtrip_serialization_of_inline_lists(self):
        """Serializing and re-parsing entries with lists preserves element structure."""
        entry = KnowledgeEntry(
            metadata=KnowledgeMetadata(
                id="kb-roundtrip",
                title="Roundtrip Test",
                category="youtube",
                verification_status="VERIFIED",
                confidence=0.90,
                sources=["https://example.com/api"],
                tags=["safe-zones", "resolution-1080p"],
                applies_to=["youtube", "instagram"],
            ),
            content="Content for roundtrip test.",
        )
        serialized = _serialize_yaml_frontmatter(entry.metadata, entry.content)
        parsed_meta_dict, parsed_body = _parse_yaml_frontmatter(serialized)
        reloaded = KnowledgeMetadata(**parsed_meta_dict)

        assert reloaded.sources == ["https://example.com/api"]
        assert reloaded.tags == ["safe-zones", "resolution-1080p"]
        assert reloaded.applies_to == ["youtube", "instagram"]
        assert parsed_body.strip() == "Content for roundtrip test."


# ─── 3. CATEGORY DIRECTORY ALIGNMENT PROBES ──────────────────────────────────

class TestCategoryDirectoryAlignment:
    """
    Stress-test boundary alignment between file directory path and frontmatter category.
    """

    def test_aligned_file_in_knowledge_root_passes(self, tmp_path):
        """File in knowledge/youtube/ with category: youtube passes alignment check."""
        kb_root = tmp_path / "knowledge"
        cat_dir = kb_root / "youtube"
        cat_dir.mkdir(parents=True)

        file_path = cat_dir / "valid_alignment.md"
        content = (
            "---\n"
            "id: kb-align-001\n"
            "title: Valid Aligned File\n"
            "category: youtube\n"
            "verification_status: EXPERIMENTAL\n"
            "confidence: 0.70\n"
            "tags: [test]\n"
            "---\n"
            "Aligned content."
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)
        assert is_valid is True, f"Aligned file failed: {errors}"

    def test_mismatched_file_in_category_folder_fails(self, tmp_path):
        """File with category: youtube stored inside instagram/ folder must fail."""
        cat_dir = tmp_path / "instagram"
        cat_dir.mkdir(parents=True)

        mismatched_file = cat_dir / "mismatched.md"
        content = (
            "---\n"
            "id: kb-align-002\n"
            "title: Mismatched Category File\n"
            "category: youtube\n"
            "verification_status: EXPERIMENTAL\n"
            "confidence: 0.70\n"
            "tags: [test]\n"
            "---\n"
            "Mismatched content."
        )
        mismatched_file.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(mismatched_file)
        assert is_valid is False
        assert any("does not match metadata category" in err for err in errors)

    def test_mismatched_file_in_knowledge_subfolder_fails(self, tmp_path):
        """File inside knowledge/foo/ with category: research must fail."""
        kb_root = tmp_path / "knowledge"
        wrong_dir = kb_root / "wrong_folder"
        wrong_dir.mkdir(parents=True)

        file_path = wrong_dir / "wrong_subfolder.md"
        content = (
            "---\n"
            "id: kb-align-003\n"
            "title: Wrong Subfolder File\n"
            "category: research\n"
            "verification_status: EXPERIMENTAL\n"
            "confidence: 0.70\n"
            "tags: [test]\n"
            "---\n"
            "Content."
        )
        file_path.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(file_path)
        assert is_valid is False
        assert any("does not match metadata category" in err for err in errors)

    def test_isolated_scratch_path_bypasses_folder_check(self, tmp_path):
        """Unit tests using arbitrary scratch folders outside knowledge/ don't fail on directory."""
        scratch_file = tmp_path / "scratch_test.md"
        content = (
            "---\n"
            "id: kb-align-004\n"
            "title: Scratch Isolated Test\n"
            "category: research\n"
            "verification_status: EXPERIMENTAL\n"
            "confidence: 0.70\n"
            "tags: [test]\n"
            "---\n"
            "Scratch test content."
        )
        scratch_file.write_text(content, encoding="utf-8")
        is_valid, errors = validate_knowledge_file(scratch_file)
        assert is_valid is True, f"Scratch test should not fail directory check: {errors}"


# ─── 4. SEARCH ACRONYMS & HYPHENATED TAG MATCHING PROBES ─────────────────────

class TestSearchAcronymsAndHyphenatedTags:
    """
    Rigorously test search tokenization, acronym preservation, hyphenated tag expansion,
    and anti-false-positive boundaries.
    """

    @pytest.fixture
    def test_kb(self, tmp_path):
        kb_root = tmp_path / "knowledge"
        (kb_root / "ai-providers").mkdir(parents=True)
        (kb_root / "youtube").mkdir(parents=True)
        (kb_root / "creative-production").mkdir(parents=True)

        # File 1: AI & QA in title and body
        f1 = kb_root / "ai-providers" / "ai_model_guide.md"
        f1.write_text(
            "---\n"
            "id: kb-search-ai\n"
            "title: AI Model Selection and QA Evaluation Guide\n"
            "category: ai-providers\n"
            "verification_status: VERIFIED\n"
            "confidence: 0.95\n"
            "sources: [https://example.com/ai]\n"
            "tags: [ai, model-routing, qa-benchmarks]\n"
            "---\n"
            "Evaluating AI providers with automated QA protocols and DB caching.",
            encoding="utf-8",
        )

        # File 2: 4K, HD, UI in production standards
        f2 = kb_root / "creative-production" / "video_specs.md"
        f2.write_text(
            "---\n"
            "id: kb-search-specs\n"
            "title: Video Standards 4K and HD Resolution\n"
            "category: creative-production\n"
            "verification_status: VERIFIED\n"
            "confidence: 0.90\n"
            "sources: [https://example.com/specs]\n"
            "tags: [4k, hd, ui-safe-zones]\n"
            "---\n"
            "Rendering shorts in 4K or HD with UI safe margins.",
            encoding="utf-8",
        )

        # File 3: Potential false positive decoy
        f3 = kb_root / "youtube" / "decoy.md"
        f3.write_text(
            "---\n"
            "id: kb-search-decoy\n"
            "title: Decoy File With Email and Failover Words\n"
            "category: youtube\n"
            "verification_status: EXPERIMENTAL\n"
            "confidence: 0.50\n"
            "tags: [email-routing, failover-queue, maintainability]\n"
            "---\n"
            "This file contains words that have substrings 'ai' inside 'email' or 'failover'.",
            encoding="utf-8",
        )

        return kb_root

    @pytest.mark.parametrize("acronym", ["AI", "QA", "4K", "HD", "UI", "DB"])
    def test_acronym_search_returns_matches(self, test_kb, acronym):
        """2-letter domain acronyms (AI, QA, 4K, HD, UI, DB) are successfully queried."""
        results = search_knowledge_text(acronym, kb_root=test_kb)
        assert len(results) > 0, f"Query '{acronym}' unexpectedly returned 0 results"
        # Confirm matching entry
        matched_ids = [r.metadata.id for r in results]
        assert any(mid in ("kb-search-ai", "kb-search-specs") for mid in matched_ids)

    def test_single_letter_queries_are_safely_ignored(self, test_kb):
        """Single letter queries ('a', 'x') yield 0 results (noise filtering preserved)."""
        assert len(search_knowledge_text("a", kb_root=test_kb)) == 0
        assert len(search_knowledge_text("z", kb_root=test_kb)) == 0

    def test_hyphenated_tag_constituent_matching(self, test_kb):
        """
        Searching for constituent words in hyphenated tags (e.g. 'safe', 'zones' in 'ui-safe-zones')
        awards the tag score (+3.0) and retrieves the document.
        """
        res_safe = search_knowledge_text("safe", kb_root=test_kb)
        assert len(res_safe) > 0
        assert res_safe[0].metadata.id == "kb-search-specs"

        res_zones = search_knowledge_text("zones", kb_root=test_kb)
        assert len(res_zones) > 0
        assert res_zones[0].metadata.id == "kb-search-specs"

    def test_no_false_positive_tag_matches_on_partial_substrings(self, test_kb):
        """
        Querying 'ai' must NOT award tag bonus for decoy tags 'failover-queue' or 'email-routing'.
        Decoy entry must not rank above genuine AI entry.
        """
        results = search_knowledge_text("ai", kb_root=test_kb)
        assert len(results) > 0
        top_match = results[0]
        assert top_match.metadata.id == "kb-search-ai", (
            f"Expected genuine AI entry as top match, got: {top_match.metadata.id}"
        )


# ─── 5. ROBUSTNESS & EDGE CASE STRESS PROBES ─────────────────────────────────

class TestRobustnessAndEdgeCases:
    """
    Stress-test additional YAML syntax variations, boundary values, and integrity rules.
    """

    def test_colon_in_quoted_title_and_source_url(self, tmp_path):
        """Titles with colons and URLs with ports must be correctly parsed."""
        file_path = tmp_path / "colon_test.md"
        content = (
            "---\n"
            "id: kb-colon-001\n"
            "title: 'Guide: Advanced Workflows: Part 1'\n"
            "category: research\n"
            "verification_status: VERIFIED\n"
            "confidence: 0.85\n"
            "sources:\n"
            "  - 'https://example.com:8443/api/v1/resource'\n"
            "tags: [colons, test]\n"
            "---\n"
            "Body with colon-rich content."
        )
        file_path.write_text(content, encoding="utf-8")
        entry = parse_knowledge_file(file_path)
        assert entry.metadata.title == "Guide: Advanced Workflows: Part 1"
        assert entry.metadata.sources == ["https://example.com:8443/api/v1/resource"]

    def test_confidence_boundary_precision(self, tmp_path):
        """Confidence boundary: 0.80 passes VERIFIED check; 0.799 fails."""
        f_pass = tmp_path / "pass_080.md"
        f_pass.write_text(
            "---\n"
            "id: kb-conf-pass\n"
            "title: Pass Boundary\n"
            "category: youtube\n"
            "verification_status: VERIFIED\n"
            "confidence: 0.80\n"
            "sources: [https://example.com]\n"
            "tags: [test]\n"
            "---\n"
            "Content.",
            encoding="utf-8",
        )
        is_valid, errors = validate_knowledge_file(f_pass)
        assert is_valid is True, f"Confidence 0.80 unexpectedly failed: {errors}"

        f_fail = tmp_path / "fail_0799.md"
        f_fail.write_text(
            "---\n"
            "id: kb-conf-fail\n"
            "title: Fail Boundary\n"
            "category: youtube\n"
            "verification_status: VERIFIED\n"
            "confidence: 0.799\n"
            "sources: [https://example.com]\n"
            "tags: [test]\n"
            "---\n"
            "Content.",
            encoding="utf-8",
        )
        is_valid, errors = validate_knowledge_file(f_fail)
        assert is_valid is False
        assert any("confidence >= 0.80" in err for err in errors)

    def test_whitespace_and_empty_body_rejection(self, tmp_path):
        """Files with empty or whitespace-only bodies are rejected."""
        file_path = tmp_path / "empty_body.md"
        file_path.write_text(
            "---\n"
            "id: kb-empty-body\n"
            "title: Empty Body File\n"
            "category: youtube\n"
            "verification_status: EXPERIMENTAL\n"
            "confidence: 0.60\n"
            "tags: [test]\n"
            "---\n"
            "    \n\n   \t  \n",
            encoding="utf-8",
        )
        is_valid, errors = validate_knowledge_file(file_path)
        assert is_valid is False
        assert any("body content cannot be empty" in err for err in errors)
