"""
Unit test suite for Persistent Knowledge Base taxonomy, schema validation,
citation gates, search scoring, and SQLite index synchronization.
Milestone 1 / Requirement R1.
"""
from pathlib import Path
import pytest
import sqlite3

from agent.modules.knowledge import (
    VALID_CATEGORIES,
    KnowledgeCategory,
    VerificationStatus,
    ConfidenceLevel,
    KnowledgeMetadata,
    KnowledgeEntry,
    parse_knowledge_file,
    get_knowledge,
    query_knowledge,
    search_knowledge_text,
    validate_knowledge_file,
    audit_knowledge_base,
    sync_to_db,
    KNOWLEDGE_ROOT,
)


def test_taxonomy_strictly_8_categories():
    assert KNOWLEDGE_ROOT.exists(), "knowledge directory must exist"
    md_files = list(KNOWLEDGE_ROOT.rglob("*.md"))
    assert len(md_files) >= 8, f"Expected at least 8 seed files, got {len(md_files)}"

    for md_file in md_files:
        rel_path = md_file.relative_to(KNOWLEDGE_ROOT)
        category_dir = rel_path.parts[0]
        assert category_dir in VALID_CATEGORIES, (
            f"File '{md_file}' is in invalid category directory '{category_dir}'. "
            f"Must be one of: {sorted(VALID_CATEGORIES)}"
        )


def test_document_frontmatter_presence():
    md_files = list(KNOWLEDGE_ROOT.rglob("*.md"))
    for md_file in md_files:
        is_valid, errors = validate_knowledge_file(md_file)
        assert is_valid, f"Validation failed for {md_file}: {errors}"

        entry = parse_knowledge_file(md_file)
        assert entry.metadata.id.startswith("kb-")
        assert len(entry.metadata.title) > 5
        assert len(entry.metadata.tags) > 0
        assert len(entry.content.strip()) > 50


def test_verified_documents_require_valid_source(tmp_path):
    # 1. Existing seeded docs must all be VERIFIED with valid sources
    md_files = list(KNOWLEDGE_ROOT.rglob("*.md"))
    for md_file in md_files:
        entry = parse_knowledge_file(md_file)
        if entry.metadata.verification_status == VerificationStatus.VERIFIED:
            assert entry.metadata.confidence >= 0.80
            assert len(entry.metadata.sources) >= 1
            for src in entry.metadata.sources:
                assert len(src.strip()) > 3

    # 2. Negative test: Metadata with VERIFIED and empty sources
    entry = KnowledgeEntry(
        metadata=KnowledgeMetadata(
            id="kb-test-001",
            title="Unverified claim test",
            category="ai-providers",
            verification_status="VERIFIED",
            confidence=0.95,
            sources=[],
            tags=["test"],
        ),
        content="# Test Content",
    )
    tmp_file = tmp_path / "temp_invalid.md"
    entry.file_path = tmp_file
    from agent.modules.knowledge import write_knowledge
    write_knowledge(entry, overwrite=True)
    is_valid, errors = validate_knowledge_file(tmp_file)
    assert not is_valid
    assert any("verifiable source citation" in err for err in errors)


def test_experimental_status_validation():
    meta = KnowledgeMetadata(
        id="kb-exp-001",
        title="Experimental Wan 2.1 Video Test",
        category="models",
        verification_status="EXPERIMENTAL",
        confidence=0.45,
        sources=["agents/agent-5-innovation/experiments/wan21/"],
        tags=["experimental", "wan2.1"],
    )
    assert meta.verification_status == VerificationStatus.EXPERIMENTAL
    assert meta.confidence_level == ConfidenceLevel.LOW


def test_search_knowledge_text_scoring():
    results = search_knowledge_text("vertical safe zone loudness", limit=3)
    assert len(results) > 0
    top = results[0]
    assert top.metadata.id == "kb-cp-001"
    assert "vertical" in top.metadata.title.lower() or "safe" in top.metadata.title.lower()


def test_sqlite_sync_and_query(tmp_path):
    test_db = tmp_path / "test_db.sqlite"
    synced = sync_to_db(db_path=test_db)
    assert synced >= 8

    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, title, category, verification_status FROM knowledge_index WHERE verification_status = 'VERIFIED'").fetchall()
    assert len(rows) >= 8
    categories = {r["category"] for r in rows}
    assert len(categories) == 8
    conn.close()


def test_audit_knowledge_base_clean():
    audit = audit_knowledge_base()
    assert audit["total_entries"] >= 8
    assert len(audit["errors"]) == 0
    assert audit["unverified_count"] == 0
    for cat in VALID_CATEGORIES:
        assert audit["by_category"].get(cat, 0) >= 1
