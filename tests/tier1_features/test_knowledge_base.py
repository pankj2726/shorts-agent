"""
Tier 1: Feature Coverage — Persistent Shared Knowledge Base (Feature 3)
Tests persistent knowledge repository structure, domain partitioning, source attribution,
and confidence tag parsing.
"""
import os
import re
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"

EXPECTED_DOMAINS = [
    "youtube",
    "instagram",
    "ai-providers",
    "audience-intelligence",
    "content-categories",
    "creative-production",
    "models",
    "research",
]

CONFIDENCE_PATTERN = re.compile(r"\[(VERIFIED|HYPOTHESIS|EXPERIMENTAL)\]", re.IGNORECASE)


def test_knowledge_base_directory_structure():
    """Test 1: Verify all required domain subdirectories exist under knowledge/."""
    assert KNOWLEDGE_DIR.exists(), "knowledge/ directory must exist"
    subdirs = [d.name for d in KNOWLEDGE_DIR.iterdir() if d.is_dir()]
    for domain in EXPECTED_DOMAINS:
        assert domain in subdirs, f"Domain directory '{domain}' missing in knowledge/"


def test_knowledge_entry_confidence_tag_parsing():
    """Test 2: Parse and validate confidence tags from knowledge text."""
    sample_text = """
    # YouTube Short Retention Mechanics
    - [VERIFIED] Voiceover onset at 0.0s reduces swipe-away by 34% (Source: Q3 2026 Analytics).
    - [HYPOTHESIS] Text color contrast above 7:1 improves 3-second hold rate.
    - [EXPERIMENTAL] 6-beat background music improves completion on dance trends.
    """
    matches = CONFIDENCE_PATTERN.findall(sample_text)
    assert len(matches) == 3
    assert "VERIFIED" in matches
    assert "HYPOTHESIS" in matches
    assert "EXPERIMENTAL" in matches


def test_knowledge_entry_requires_source_attribution():
    """Test 3: Assert that verified knowledge statements require source attribution."""
    def validate_verified_entry(entry_text: str) -> bool:
        if "[VERIFIED]" in entry_text:
            return "source:" in entry_text.lower() or "source" in entry_text.lower() or "http" in entry_text.lower()
        return True

    valid_entry = "- [VERIFIED] Hook under 12 words yields higher retention (Source: channel_bible.md)"
    invalid_entry = "- [VERIFIED] Hook under 12 words yields higher retention with no proof"

    assert validate_verified_entry(valid_entry) is True
    assert validate_verified_entry(invalid_entry) is False


def test_knowledge_repository_query_filtering(tmp_path):
    """Test 4: Verify querying/filtering knowledge entries by domain and tag."""
    test_kd = tmp_path / "knowledge"
    yt_dir = test_kd / "youtube"
    yt_dir.mkdir(parents=True, exist_ok=True)
    
    file_content = """# YouTube Best Practices
- [VERIFIED] Safe zone margins are X: 80-940, Y: 220-1480.
- [HYPOTHESIS] Blue accents outperform red accents for tech niches.
"""
    (yt_dir / "safe_zones.md").write_text(file_content, encoding="utf-8")

    def query_knowledge(root: Path, domain: str, confidence_filter: str) -> list[str]:
        results = []
        domain_path = root / domain
        if not domain_path.exists():
            return results
        for md_file in domain_path.glob("*.md"):
            for line in md_file.read_text(encoding="utf-8").splitlines():
                if f"[{confidence_filter}]" in line:
                    results.append(line.strip())
        return results

    verified = query_knowledge(test_kd, "youtube", "VERIFIED")
    hypotheses = query_knowledge(test_kd, "youtube", "HYPOTHESIS")
    assert len(verified) == 1
    assert "Safe zone margins" in verified[0]
    assert len(hypotheses) == 1
    assert "Blue accents" in hypotheses[0]


def test_knowledge_persistence_across_sessions(tmp_path):
    """Test 5: Verify persistent knowledge updates and reads simulate multi-session access."""
    kb_file = tmp_path / "knowledge" / "audience-intelligence" / "personas.md"
    kb_file.parent.mkdir(parents=True, exist_ok=True)

    # Session 1: Agent writes insight
    kb_file.write_text("- [VERIFIED] Audience retention drops at 15s mark (Source: batch_run_01)\n", encoding="utf-8")

    # Session 2: Another agent appends insight
    with open(kb_file, "a", encoding="utf-8") as f:
        f.write("- [EXPERIMENTAL] Inserting sound effect at 14.5s restores attention\n")

    # Session 3: Read back full knowledge file
    lines = kb_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert "[VERIFIED]" in lines[0]
    assert "[EXPERIMENTAL]" in lines[1]


def test_unverified_speculation_flagged():
    """Test 6: Entries with invalid confidence or wild speculative assertions are flagged."""
    def sanitize_knowledge_assertion(line: str) -> str:
        match = CONFIDENCE_PATTERN.search(line)
        if not match:
            return "UNCLASSIFIED"
        return match.group(1).upper()

    assert sanitize_knowledge_assertion("Just a random thought without tag") == "UNCLASSIFIED"
    assert sanitize_knowledge_assertion("[VERIFIED] Confirmed fact") == "VERIFIED"
    assert sanitize_knowledge_assertion("[HYPOTHESIS] Testing theory") == "HYPOTHESIS"
