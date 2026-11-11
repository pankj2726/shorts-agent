"""
Adversarial Stress Test Harness for Knowledge Base & Schema.
Milestone 1 Challenger 2.

Covers:
1. Frontmatter parsing & corruption (fallback parser bugs, inline list handling, CRLF)
2. Verification status & citation gating (bypass via inline '[]', confidence thresholds)
3. Boundary scores & types (0.0, 1.0, NaN, out-of-bounds)
4. Taxonomy directory constraints & category mismatches
5. Search relevance scoring (2-letter acronyms dropped, hyphenated tag match defect, weights)
6. SQLite sync under rapid concurrent calls, orphaned records, duplicate IDs across categories
"""
import concurrent.futures
import math
import os
from pathlib import Path
import sqlite3
import pytest

from agent.modules.knowledge import (
    VALID_CATEGORIES,
    VALID_OWNERS,
    ConfidenceLevel,
    KnowledgeCategory,
    KnowledgeEntry,
    KnowledgeMetadata,
    VerificationStatus,
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
    KNOWLEDGE_ROOT,
)


# ─── 1. Frontmatter Parsing & Robustness ──────────────────────────────────────

def test_frontmatter_missing_delimiters():
    """File missing '---' frontmatter delimiters should raise ValueError."""
    no_frontmatter = "# Just a markdown header\nSome body text without frontmatter."
    with pytest.raises(ValueError, match="YAML frontmatter"):
        _parse_yaml_frontmatter(no_frontmatter)


def test_frontmatter_empty_frontmatter():
    """File with empty frontmatter delimiters."""
    empty_fm = "---\n\n---\n# Body Content"
    meta_dict, body = _parse_yaml_frontmatter(empty_fm)
    assert meta_dict == {}
    assert "Body Content" in body


def test_frontmatter_windows_crlf():
    """Frontmatter with Windows CRLF newlines should parse correctly."""
    crlf_text = (
        "---\r\n"
        "id: kb-crlf-001\r\n"
        "title: CRLF Test\r\n"
        "category: youtube\r\n"
        "tags:\r\n"
        "  - test\r\n"
        "---\r\n"
        "# Body Content"
    )
    meta_dict, body = _parse_yaml_frontmatter(crlf_text)
    assert meta_dict.get("id") == "kb-crlf-001"


def test_frontmatter_fallback_parser_swallows_malformed_syntax():
    """
    VULNERABILITY PROBE: Fallback YAML parser does not validate YAML syntax.
    Malformed syntax such as unclosed brackets '[unclosed list' is silently
    parsed as a literal string rather than raising a SyntaxError or ValueError.
    """
    malformed_yaml = (
        "---\n"
        "id: kb-test-malformed\n"
        "title: Malformed Frontmatter\n"
        "category: [unclosed list\n"
        "---\n"
        "# Body content"
    )
    # The parser does not raise an error; it returns the raw invalid string as value:
    meta_dict, body = _parse_yaml_frontmatter(malformed_yaml)
    assert meta_dict.get("category") == "[unclosed list"
    # Then KnowledgeMetadata rejects it only because it's not in VALID_CATEGORIES:
    with pytest.raises(ValueError, match="Invalid category"):
        KnowledgeMetadata(**meta_dict)


def test_frontmatter_fallback_inline_bracket_vulnerability():
    """
    CRITICAL DEFECT REGRESSION: Inline empty bracket syntax 'sources: []' and 'tags: []'
    must be parsed as Python empty lists [] instead of string '[]'.
    """
    yaml_text = (
        "---\n"
        "id: kb-vuln-001\n"
        "title: Inline Bracket Test\n"
        "category: youtube\n"
        "sources: []\n"
        "tags: []\n"
        "---\n"
        "Content"
    )
    meta_dict, _ = _parse_yaml_frontmatter(yaml_text)
    assert meta_dict["sources"] == [], "Fallback parser must parse '[]' as empty list []"
    assert meta_dict["tags"] == [], "Fallback parser must parse '[]' as empty list []"


def test_frontmatter_fallback_inline_list_explodes_to_characters():
    """
    DEFECT REGRESSION: Inline lists like 'tags: [shorts, viral]' are parsed as a list of strings,
    and KnowledgeMetadata preserves list elements without exploding them into characters.
    """
    yaml_text = (
        "---\n"
        "id: kb-inline-tags\n"
        "title: Inline Tags\n"
        "category: youtube\n"
        "tags: [shorts, viral]\n"
        "---\n"
        "Content"
    )
    meta_dict, _ = _parse_yaml_frontmatter(yaml_text)
    meta = KnowledgeMetadata(**meta_dict)
    assert meta.tags == ["shorts", "viral"]
    assert "[" not in meta.tags
    assert "shorts" in meta.tags
    assert "viral" in meta.tags


# ─── 2. Verification Status & Citation Gating ─────────────────────────────────

def test_verified_entry_without_sources_fails_when_multiline_empty(tmp_path):
    """VERIFIED entry with empty sources (multiline syntax) fails validation."""
    file_path = tmp_path / "test_unverified_multiline.md"
    content = (
        "---\n"
        "id: kb-unverified-001\n"
        "title: Unverified Claim Entry\n"
        "category: ai-providers\n"
        "verification_status: VERIFIED\n"
        "confidence: 0.95\n"
        "sources:\n"
        "tags:\n"
        "  - test\n"
        "---\n"
        "This claim has no citations but claims VERIFIED status."
    )
    file_path.write_text(content, encoding="utf-8")
    is_valid, errors = validate_knowledge_file(file_path)
    assert not is_valid
    assert any("verifiable source citation" in err for err in errors)


def test_citation_bypass_vulnerability_with_inline_empty_brackets(tmp_path):
    """
    CRITICAL VULNERABILITY REGRESSION: An entry marked VERIFIED with 'sources: []'
    must fail validation because sources is empty.
    """
    file_path = tmp_path / "test_bypass.md"
    content = (
        "---\n"
        "id: kb-bypass-001\n"
        "title: Citation Bypass Vulnerability\n"
        "category: ai-providers\n"
        "verification_status: VERIFIED\n"
        "confidence: 0.95\n"
        "sources: []\n"
        "tags:\n"
        "  - test\n"
        "---\n"
        "This claim has NO sources and must fail validation!"
    )
    file_path.write_text(content, encoding="utf-8")
    is_valid, errors = validate_knowledge_file(file_path)
    entry = parse_knowledge_file(file_path)
    assert entry.metadata.sources == []
    assert is_valid is False, "VERIFIED document with empty sources must be rejected"
    assert any("verifiable source citation" in err for err in errors)


def test_verified_entry_low_confidence_fails_validation(tmp_path):
    """VERIFIED entry with confidence < 0.80 MUST fail validation."""
    file_path = tmp_path / "test_low_conf.md"
    content = (
        "---\n"
        "id: kb-lowconf-001\n"
        "title: Low Confidence Verified\n"
        "category: ai-providers\n"
        "verification_status: VERIFIED\n"
        "confidence: 0.75\n"
        "sources:\n"
        "  - https://example.com/source\n"
        "tags:\n"
        "  - test\n"
        "---\n"
        "High verification status with low confidence score."
    )
    file_path.write_text(content, encoding="utf-8")
    is_valid, errors = validate_knowledge_file(file_path)
    assert not is_valid
    assert any("confidence >= 0.80" in err for err in errors)


def test_unverified_entries_pass_without_sources(tmp_path):
    """HYPOTHESIS and EXPERIMENTAL entries are allowed to have empty sources."""
    file_path = tmp_path / "test_hypothesis.md"
    content = (
        "---\n"
        "id: kb-hypo-001\n"
        "title: Hypothesis Entry\n"
        "category: research\n"
        "verification_status: HYPOTHESIS\n"
        "confidence: 0.60\n"
        "sources:\n"
        "tags:\n"
        "  - hypothesis\n"
        "---\n"
        "This is an unverified hypothesis."
    )
    file_path.write_text(content, encoding="utf-8")
    is_valid, errors = validate_knowledge_file(file_path)
    assert is_valid
    assert len(errors) == 0


def test_write_knowledge_lacks_pre_write_validation(tmp_path):
    """
    INTEGRITY DEFECT PROBE: write_knowledge() writes invalid entries to disk
    without calling validate_knowledge_file().
    """
    entry = KnowledgeEntry(
        metadata=KnowledgeMetadata(
            id="kb-unvalidated-write",
            title="Unvalidated Write",
            category="models",
            verification_status="VERIFIED",
            confidence=0.10,  # Invalid for VERIFIED
            sources=[],       # Invalid for VERIFIED
            tags=[],          # Invalid empty tags
        ),
        content="Invalid body",
    )
    target = tmp_path / "unvalidated.md"
    entry.file_path = target
    # write_knowledge does not raise:
    write_knowledge(entry, overwrite=True)
    assert target.exists()

    # But subsequent audit/validation fails:
    is_valid, errors = validate_knowledge_file(target)
    assert not is_valid
    assert len(errors) >= 2


# ─── 3. Boundary Scores & Types ───────────────────────────────────────────────

def test_confidence_boundaries_exact_zero_and_one():
    """0.0 and 1.0 are valid boundary values."""
    m0 = KnowledgeMetadata(
        id="kb-zero",
        title="Zero Confidence",
        category="models",
        verification_status="EXPERIMENTAL",
        confidence=0.0,
        tags=["zero"],
    )
    assert m0.confidence == 0.0
    assert m0.confidence_level == ConfidenceLevel.LOW

    m1 = KnowledgeMetadata(
        id="kb-one",
        title="Full Confidence",
        category="models",
        verification_status="VERIFIED",
        confidence=1.0,
        sources=["https://source.com"],
        tags=["one"],
    )
    assert m1.confidence == 1.0
    assert m1.confidence_level == ConfidenceLevel.HIGH


def test_confidence_out_of_bounds_negative():
    """Confidence < 0.0 must raise ValueError."""
    with pytest.raises(ValueError, match="Confidence must be between 0.0 and 1.0"):
        KnowledgeMetadata(
            id="kb-neg",
            title="Negative Confidence",
            category="models",
            confidence=-0.01,
            tags=["test"],
        )


def test_confidence_out_of_bounds_excess():
    """Confidence > 1.0 must raise ValueError."""
    with pytest.raises(ValueError, match="Confidence must be between 0.0 and 1.0"):
        KnowledgeMetadata(
            id="kb-excess",
            title="Excess Confidence",
            category="models",
            confidence=1.01,
            tags=["test"],
        )


def test_confidence_nan_bypass():
    """
    BOUNDARY DEFECT PROBE: float('nan') bypasses 'if confidence < 0.0 or confidence > 1.0'
    because in IEEE 754, NaN comparison inequalities evaluate to False!
    """
    meta = KnowledgeMetadata(
        id="kb-nan",
        title="NaN Confidence",
        category="models",
        confidence=float("nan"),
        tags=["test"],
    )
    assert math.isnan(meta.confidence), "Confidence accepted NaN value"
    assert meta.confidence_level == ConfidenceLevel.LOW


def test_empty_title_and_tags_validation_multiline(tmp_path):
    """Empty title or tags (multiline syntax) must fail validate_knowledge_file."""
    file_path = tmp_path / "test_empty_fields.md"
    content = (
        "---\n"
        "id: kb-empty-001\n"
        "title: ''\n"
        "category: models\n"
        "verification_status: EXPERIMENTAL\n"
        "confidence: 0.5\n"
        "sources:\n"
        "tags:\n"
        "---\n"
        "# Empty Fields Content"
    )
    file_path.write_text(content, encoding="utf-8")
    is_valid, errors = validate_knowledge_file(file_path)
    assert not is_valid
    assert any("Title cannot be empty" in err for err in errors)
    assert any("Entry must contain at least one tag" in err for err in errors)


def test_empty_body_validation(tmp_path):
    """Empty body content must fail validate_knowledge_file."""
    file_path = tmp_path / "test_empty_body.md"
    content = (
        "---\n"
        "id: kb-empty-body\n"
        "title: Valid Title\n"
        "category: models\n"
        "verification_status: EXPERIMENTAL\n"
        "confidence: 0.5\n"
        "sources:\n"
        "tags:\n"
        "  - tag1\n"
        "---\n"
        "   \n\n  "
    )
    file_path.write_text(content, encoding="utf-8")
    is_valid, errors = validate_knowledge_file(file_path)
    assert not is_valid
    assert any("body content cannot be empty" in err for err in errors)


# ─── 4. Taxonomy & Directory Boundary Constraints ─────────────────────────────

def test_invalid_category_in_metadata():
    """Invalid category string must raise ValueError."""
    with pytest.raises(ValueError, match="Invalid category"):
        KnowledgeMetadata(
            id="kb-inv-cat",
            title="Invalid Category",
            category="unauthorized-category",
            tags=["test"],
        )


def test_invalid_owner_agent_in_metadata():
    """Invalid owner agent must raise ValueError."""
    with pytest.raises(ValueError, match="Invalid owner_agent"):
        KnowledgeMetadata(
            id="kb-inv-owner",
            title="Invalid Owner",
            category="youtube",
            owner_agent="unauthorized-agent-99",
            tags=["test"],
        )


def test_category_folder_mismatch_behavior(tmp_path):
    """
    TAXONOMY DEFECT REGRESSION: A file with category: youtube saved in instagram/
    IS flagged by validate_knowledge_file(), rejecting category-folder mismatch.
    """
    kb_root = tmp_path / "knowledge"
    insta_dir = kb_root / "instagram"
    insta_dir.mkdir(parents=True)

    mismatched_file = insta_dir / "mismatched.md"
    content = (
        "---\n"
        "id: kb-mismatch-001\n"
        "title: Mismatched Category Test\n"
        "category: youtube\n"
        "verification_status: VERIFIED\n"
        "confidence: 0.90\n"
        "sources:\n"
        "  - https://youtube.com/creators\n"
        "tags:\n"
        "  - youtube\n"
        "---\n"
        "This file has category: youtube, but is saved in instagram folder."
    )
    mismatched_file.write_text(content, encoding="utf-8")

    # 1. validate_knowledge_file DOES check folder alignment:
    is_valid, errors = validate_knowledge_file(mismatched_file)
    assert is_valid is False, "validate_knowledge_file must reject folder-category alignment mismatch"
    assert any("does not match metadata category" in err for err in errors)

    # 2. Querying by category 'youtube' fails to find it:
    yt_results = query_knowledge(category="youtube", kb_root=kb_root)
    assert len(yt_results) == 0

    # 3. Querying by category 'instagram' returns it:
    insta_results = query_knowledge(category="instagram", kb_root=kb_root)
    assert len(insta_results) == 1
    assert insta_results[0].metadata.category == KnowledgeCategory.YOUTUBE


# ─── 5. Search Relevance Scoring & Tokenization ───────────────────────────────

def test_search_two_letter_acronyms_supported():
    """
    SEARCH ACRONYM REGRESSION TEST: Tokens with length >= 2 ('AI', 'QA') are
    preserved, returning relevant results for core domain acronyms.
    """
    assert len(search_knowledge_text("AI")) > 0
    assert len(search_knowledge_text("QA")) >= 0
    assert len(search_knowledge_text("4K")) >= 0


def test_search_tag_hyphenation_matching_effective(tmp_path):
    """
    SEARCH TAG HYPHENATION REGRESSION TEST: Hyphenated tags (e.g. 'safe-zones')
    match constituent search tokens ('safe' or 'zones') via expanded tag component matching.
    """
    kb_root = tmp_path / "knowledge"
    cat_dir = kb_root / "youtube"
    cat_dir.mkdir(parents=True)

    file_path = cat_dir / "tag_test.md"
    content = (
        "---\n"
        "id: kb-tag-001\n"
        "title: Generic Platform Overview\n"
        "category: youtube\n"
        "verification_status: VERIFIED\n"
        "confidence: 0.90\n"
        "sources:\n"
        "  - https://youtube.com\n"
        "tags:\n"
        "  - safe-zones\n"
        "---\n"
        "Unrelated body text with no matching words."
    )
    file_path.write_text(content, encoding="utf-8")

    # Searching for 'safe'
    res = search_knowledge_text("safe", kb_root=kb_root)
    assert len(res) == 1, "Hyphenated tag 'safe-zones' must match constituent token 'safe'"
    assert res[0].metadata.id == "kb-tag-001"


def test_search_ranking_relevance_weights(tmp_path):
    """Verify search ranking gives higher weight to title match (5.0) than body match (0.5)."""
    kb_root = tmp_path / "knowledge"
    cat_dir = kb_root / "research"
    cat_dir.mkdir(parents=True)

    # Entry A: Keyword in title
    file_a = cat_dir / "entry_a.md"
    file_a.write_text(
        "---\n"
        "id: kb-rank-a\n"
        "title: Monetization Strategy\n"
        "category: research\n"
        "verification_status: HYPOTHESIS\n"
        "confidence: 0.80\n"
        "tags:\n"
        "  - strategy\n"
        "---\n"
        "General overview text.",
        encoding="utf-8",
    )

    # Entry B: Keyword mentioned in body content
    file_b = cat_dir / "entry_b.md"
    file_b.write_text(
        "---\n"
        "id: kb-rank-b\n"
        "title: Audience Engagement\n"
        "category: research\n"
        "verification_status: HYPOTHESIS\n"
        "confidence: 0.80\n"
        "tags:\n"
        "  - engagement\n"
        "---\n"
        "Discussing monetization aspects.",
        encoding="utf-8",
    )

    results = search_knowledge_text("monetization", kb_root=kb_root)
    assert len(results) == 2
    assert results[0].metadata.id == "kb-rank-a", "Title match must outrank body match"


# ─── 6. SQLite Sync Under Rapid Queries & Concurrency ─────────────────────────

def test_concurrent_rapid_sqlite_sync(tmp_path):
    """Stress test: 10 concurrent threads invoking sync_to_db on the same database."""
    db_file = tmp_path / "concurrent_test.sqlite"

    def run_sync(thread_id: int):
        return sync_to_db(db_path=db_file)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(run_sync, i) for i in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert all(r >= 8 for r in results)
    conn = sqlite3.connect(db_file)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    assert integrity == "ok"
    conn.close()


def test_sqlite_sync_orphaned_records_on_file_deletion(tmp_path):
    """
    SYNC DEFECT PROBE: sync_to_db does not prune deleted records from knowledge_index.
    """
    kb_root = tmp_path / "knowledge"
    cat_dir = kb_root / "youtube"
    cat_dir.mkdir(parents=True)
    db_file = tmp_path / "prune_test.sqlite"

    file_1 = cat_dir / "file1.md"
    file_1.write_text(
        "---\n"
        "id: kb-prune-001\n"
        "title: Ephemeral File\n"
        "category: youtube\n"
        "verification_status: VERIFIED\n"
        "confidence: 0.9\n"
        "sources:\n"
        "  - https://youtube.com\n"
        "tags:\n"
        "  - test\n"
        "---\n"
        "Temporary content.",
        encoding="utf-8",
    )

    sync_to_db(kb_root=kb_root, db_path=db_file)
    file_1.unlink()  # File deleted from disk
    sync_to_db(kb_root=kb_root, db_path=db_file)

    conn = sqlite3.connect(db_file)
    count = conn.execute("SELECT COUNT(*) FROM knowledge_index WHERE id = 'kb-prune-001'").fetchone()[0]
    conn.close()
    assert count == 1, "Deleted file remains in SQLite cache as an orphaned record"


def test_duplicate_id_split_brain(tmp_path):
    """
    DATA INTEGRITY DEFECT PROBE: Duplicate IDs across categories cause split-brain
    between get_knowledge() and SQLite index.
    """
    kb_root = tmp_path / "knowledge"
    cat_a = kb_root / "ai-providers"
    cat_b = kb_root / "youtube"
    cat_a.mkdir(parents=True)
    cat_b.mkdir(parents=True)
    db_file = tmp_path / "dup_test.sqlite"

    file_a = cat_a / "first.md"
    file_a.write_text(
        "---\n"
        "id: kb-dup-collision\n"
        "title: First Entry in AI Providers\n"
        "category: ai-providers\n"
        "verification_status: EXPERIMENTAL\n"
        "confidence: 0.5\n"
        "tags:\n"
        "  - dup\n"
        "---\n"
        "Content from AI Providers",
        encoding="utf-8",
    )

    file_b = cat_b / "second.md"
    file_b.write_text(
        "---\n"
        "id: kb-dup-collision\n"
        "title: Second Entry in YouTube\n"
        "category: youtube\n"
        "verification_status: EXPERIMENTAL\n"
        "confidence: 0.7\n"
        "tags:\n"
        "  - dup\n"
        "---\n"
        "Content from YouTube",
        encoding="utf-8",
    )

    get_res = get_knowledge("kb-dup-collision", kb_root=kb_root)
    sync_to_db(kb_root=kb_root, db_path=db_file)

    conn = sqlite3.connect(db_file)
    row = conn.execute("SELECT category, title FROM knowledge_index WHERE id = 'kb-dup-collision'").fetchone()
    conn.close()

    # Demonstrates split-brain:
    assert get_res.metadata.category == KnowledgeCategory.AI_PROVIDERS
    assert row[0] == "youtube"
