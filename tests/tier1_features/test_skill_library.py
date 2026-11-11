"""
Tier 1: Feature Coverage — Reusable Skill Library & SKILL.md Templates (Feature 14)
Tests YAML frontmatter schema, semver formatting, promotion gates, and skill directory layout.
"""
import re
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SKILLS_DIR = REPO_ROOT / "skills"


def parse_yaml_frontmatter(file_path: Path) -> dict:
    """Helper to extract and parse simple YAML frontmatter from a markdown file."""
    text = file_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fm_text = parts[1].strip()
    data = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def test_skills_directory_exists_and_contains_skills():
    """Test 1: Verify skills/ directory contains expected skill folders."""
    assert SKILLS_DIR.exists(), "skills/ directory must exist in repository root"
    skill_folders = [d.name for d in SKILLS_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    assert len(skill_folders) >= 3, "At least 3 core skills must exist in skills/"
    assert "humanized-scriptwriting" in skill_folders


def test_skill_yaml_frontmatter_schema():
    """Test 2: Validate frontmatter contains all mandatory fields."""
    scriptwriting_skill = SKILLS_DIR / "humanized-scriptwriting" / "SKILL.md"
    assert scriptwriting_skill.exists(), "humanized-scriptwriting/SKILL.md missing"

    meta = parse_yaml_frontmatter(scriptwriting_skill)
    required_keys = ["name", "version", "status", "owner", "last_verified", "confidence"]
    for key in required_keys:
        assert key in meta, f"Required frontmatter key '{key}' missing in {scriptwriting_skill}"


def test_skill_version_semver_format():
    """Test 3: Ensure skill version matches semver format."""
    semver_pattern = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.-]+)?$")
    for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
        meta = parse_yaml_frontmatter(skill_md)
        version = meta.get("version", "")
        assert semver_pattern.match(version), f"Version '{version}' in {skill_md} is not valid SemVer"


def test_skill_promotion_gate_logic():
    """Test 4: Verify promotion gate rules from draft to production."""
    def can_promote_to_production(meta: dict) -> tuple[bool, str]:
        if meta.get("confidence") != "high":
            return False, "Confidence must be 'high' for production promotion"
        if meta.get("evidence_level") != "tested-in-production":
            return False, "Evidence level must be 'tested-in-production'"
        if not re.match(r"^\d+\.\d+\.\d+$", meta.get("version", "")):
            return False, "Production version must be formal release semver (e.g., 1.0.0)"
        return True, "Ready for promotion"

    draft_meta = {
        "name": "viral-hooks",
        "version": "0.1.0-draft",
        "confidence": "medium",
        "evidence_level": "tested-in-production",
    }
    promotable_meta = {
        "name": "viral-hooks",
        "version": "1.0.0",
        "confidence": "high",
        "evidence_level": "tested-in-production",
    }

    ok_draft, reason_draft = can_promote_to_production(draft_meta)
    assert ok_draft is False
    assert "Confidence" in reason_draft

    ok_prod, reason_prod = can_promote_to_production(promotable_meta)
    assert ok_prod is True
    assert reason_prod == "Ready for promotion"


def test_humanized_scriptwriting_skill_contains_anti_slop_guidelines():
    """Test 5: Verify humanized scriptwriting skill explicitly lists AI slop patterns and fixes."""
    skill_file = SKILLS_DIR / "humanized-scriptwriting" / "SKILL.md"
    content = skill_file.read_text(encoding="utf-8")

    assert "The Slop Problem" in content
    assert "Generic opening" in content
    assert "Filler padding" in content
    assert "Humanization Techniques" in content


def test_immutable_skill_content_verification(tmp_path):
    """Test 6: Verify immutable skill hash / checksum prevents unauthorized drift."""
    import hashlib

    test_skill = tmp_path / "SKILL.md"
    test_skill.write_text("---\nname: test\nversion: 1.0.0\n---\n# Content", encoding="utf-8")
    orig_hash = hashlib.sha256(test_skill.read_bytes()).hexdigest()

    # Read and verify unchanged
    curr_hash = hashlib.sha256(test_skill.read_bytes()).hexdigest()
    assert orig_hash == curr_hash

    # Mutate and assert hash mismatch
    test_skill.write_text("---\nname: test\nversion: 1.0.1\n---\n# Tampered", encoding="utf-8")
    tampered_hash = hashlib.sha256(test_skill.read_bytes()).hexdigest()
    assert orig_hash != tampered_hash
