"""
knowledge.py — Programmatic interface for the persistent knowledge base.
Milestone 1 / Requirement R1.

Manages knowledge parsing, validation, querying, full-text search, and SQLite caching
across the 8 canonical categories in shorts-agent/knowledge/.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
KNOWLEDGE_ROOT = PROJECT_ROOT / "knowledge"
STATE_DIR = PROJECT_ROOT / "agent" / "state"
DB_PATH = STATE_DIR / "db.sqlite"


# ─── Taxonomy Enums ────────────────────────────────────────────────────────────

class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    HYPOTHESIS = "HYPOTHESIS"
    EXPERIMENTAL = "EXPERIMENTAL"
    CREATIVE_REC = "CREATIVE_REC"


class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class KnowledgeCategory(str, Enum):
    AI_PROVIDERS = "ai-providers"
    AUDIENCE_INTELLIGENCE = "audience-intelligence"
    CONTENT_CATEGORIES = "content-categories"
    CREATIVE_PRODUCTION = "creative-production"
    INSTAGRAM = "instagram"
    MODELS = "models"
    RESEARCH = "research"
    YOUTUBE = "youtube"


VALID_CATEGORIES = {c.value for c in KnowledgeCategory}

VALID_OWNERS = {
    "agent-1-research",
    "agent-2-anti-slop",
    "agent-3-social",
    "agent-4-providers",
    "agent-5-innovation",
    "orchestrator",
}

DUMMY_NULL_VALUES = {
    "",
    "[]",
    "[ ]",
    "{}",
    "{ }",
    "()",
    "( )",
    "set()",
    "none",
    "null",
    "nil",
    "undefined",
    "~",
    "n/a",
    "na",
    "-",
    "–",
    "—",
}


def _is_null_or_dummy(val: Any) -> bool:
    """Checks if a string, scalar, or container element represents an empty or YAML null value."""
    if val is None:
        return True
    if isinstance(val, (dict, list, tuple, set)):
        if len(val) == 0:
            return True
        if isinstance(val, dict):
            return all(_is_null_or_dummy(k) and _is_null_or_dummy(v) for k, v in val.items())
        return all(_is_null_or_dummy(item) for item in val)

    s = str(val).strip().strip("\"'").strip()
    while (
        (s.startswith("[") and s.endswith("]"))
        or (s.startswith("{") and s.endswith("}"))
        or (s.startswith("(") and s.endswith(")"))
    ):
        s = s[1:-1].strip().strip("\"'").strip()
    if not s or s.lower() in DUMMY_NULL_VALUES or s == "~":
        return True
    return False


def _normalize_string_list(
    val: Optional[Union[List[Any], Tuple[Any, ...], Dict[Any, Any], Set[Any], str]],
    split_unbracketed_commas: bool = False,
) -> List[str]:
    """
    Safely normalizes list/string inputs into a List[str].
    Handles:
    - None -> []
    - Dict/set: empty or dummy -> [], set converted to list
    - List/tuple: strips whitespace/quotes, removes empty elements and dummy brackets ('[]', '[ ]', '{}', '{ }')
    - String '[]', '{}', or '' -> []
    - String '[a, b]' -> ['a', 'b']
    - Comma-separated strings (when split_unbracketed_commas=True) -> ['a', 'b']
    - Single unbracketed string -> [string] (avoids character explosion from list(str))
    """
    if val is None:
        return []
    if isinstance(val, (dict, set)):
        if not val or _is_null_or_dummy(val):
            return []
        if isinstance(val, set):
            return _normalize_string_list(list(val), split_unbracketed_commas=split_unbracketed_commas)
        return []
    if isinstance(val, (list, tuple)):
        result: List[str] = []
        for item in val:
            if _is_null_or_dummy(item):
                continue
            item_str = str(item).strip().strip("\"'").strip()
            if item_str and not _is_null_or_dummy(item_str):
                result.append(item_str)
        return result
    if isinstance(val, str):
        val_clean = val.strip().strip("\"'").strip()
        if _is_null_or_dummy(val_clean):
            return []
        if (val_clean.startswith("[") and val_clean.endswith("]")) or (
            val_clean.startswith("{") and val_clean.endswith("}")
        ):
            inner = val_clean[1:-1].strip()
            if not inner or _is_null_or_dummy(inner):
                return []
            result = []
            for item in inner.split(","):
                if _is_null_or_dummy(item):
                    continue
                item_str = item.strip().strip("\"'").strip()
                if item_str and not _is_null_or_dummy(item_str):
                    result.append(item_str)
            return result
        if split_unbracketed_commas and "," in val_clean:
            result = []
            for item in val_clean.split(","):
                if _is_null_or_dummy(item):
                    continue
                item_str = item.strip().strip("\"'").strip()
                if item_str and not _is_null_or_dummy(item_str):
                    result.append(item_str)
            return result
        return [val_clean]
    item_str = str(val).strip().strip("\"'").strip()
    return [] if _is_null_or_dummy(item_str) else [item_str]


class KnowledgeMetadata:
    """Metadata schema representing YAML frontmatter of a knowledge markdown file."""

    def __init__(
        self,
        id: str,
        title: str,
        category: Union[KnowledgeCategory, str],
        version: str = "1.0.0",
        verification_status: Union[VerificationStatus, str] = VerificationStatus.VERIFIED,
        confidence: float = 1.0,
        confidence_level: Optional[Union[ConfidenceLevel, str]] = None,
        owner_agent: str = "orchestrator",
        sources: Optional[Union[List[str], str]] = None,
        source: Optional[Union[List[str], str]] = None,
        tags: Optional[List[str]] = None,
        applies_to: Optional[List[str]] = None,
        created_at: Optional[str] = None,
        last_verified: Optional[str] = None,
        **kwargs: Any,
    ):
        self.id = str(id).strip()
        self.title = str(title).strip()

        # Category normalization & validation
        cat_str = category.value if isinstance(category, KnowledgeCategory) else str(category).strip()
        if cat_str not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category '{cat_str}'. Must be one of: {sorted(VALID_CATEGORIES)}")
        self.category = KnowledgeCategory(cat_str)

        self.version = str(version).strip()

        # Status normalization
        raw_status = verification_status.value if isinstance(verification_status, VerificationStatus) else str(verification_status).strip()
        status_clean = raw_status.strip("[]").replace("-", "_").upper()
        if status_clean not in {s.value for s in VerificationStatus}:
            raise ValueError(f"Invalid verification_status '{raw_status}'. Must be one of: {[s.value for s in VerificationStatus]}")
        self.verification_status = VerificationStatus(status_clean)

        self.confidence = float(confidence)
        if self.confidence < 0.0 or self.confidence > 1.0:
            raise ValueError(f"Confidence must be between 0.0 and 1.0, got {self.confidence}")

        # Compute or validate confidence level
        if confidence_level:
            level_clean = (
                confidence_level.value
                if isinstance(confidence_level, ConfidenceLevel)
                else str(confidence_level).strip().upper()
            )
            self.confidence_level = ConfidenceLevel(level_clean)
        else:
            self.confidence_level = (
                ConfidenceLevel.HIGH
                if self.confidence >= 0.80
                else (ConfidenceLevel.MEDIUM if self.confidence >= 0.50 else ConfidenceLevel.LOW)
            )

        # Owner agent validation
        owner_clean = str(owner_agent).strip()
        if owner_clean not in VALID_OWNERS:
            raise ValueError(f"Invalid owner_agent '{owner_clean}'. Must be one of: {sorted(VALID_OWNERS)}")
        self.owner_agent = owner_clean

        # Normalize sources (accept 'sources' or 'source')
        raw_sources = sources if sources is not None else source
        self.sources = _normalize_string_list(raw_sources, split_unbracketed_commas=False)

        # Normalize tags and applies_to safely without character explosion
        self.tags = _normalize_string_list(tags, split_unbracketed_commas=True)
        self.applies_to = _normalize_string_list(applies_to, split_unbracketed_commas=True)

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.created_at = created_at or now_iso
        self.last_verified = last_verified or now_iso

    @property
    def source(self) -> str:
        """Backwards compatibility alias for single source string."""
        return self.sources[0] if self.sources else ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category.value,
            "version": self.version,
            "verification_status": self.verification_status.value,
            "confidence": round(self.confidence, 2),
            "confidence_level": self.confidence_level.value,
            "owner_agent": self.owner_agent,
            "sources": self.sources,
            "tags": self.tags,
            "applies_to": self.applies_to,
            "created_at": self.created_at,
            "last_verified": self.last_verified,
        }


class KnowledgeEntry:
    """Represents a fully parsed markdown knowledge document."""

    def __init__(self, metadata: KnowledgeMetadata, content: str, file_path: Optional[Path] = None):
        self.metadata = metadata
        self.content = content
        self.file_path = file_path

    def __repr__(self) -> str:
        return f"<KnowledgeEntry id='{self.metadata.id}' category='{self.metadata.category.value}' title='{self.metadata.title}'>"


# ─── YAML Frontmatter Parser & Serializer ─────────────────────────────────────

def _parse_yaml_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """
    Parses frontmatter delimited by '---'.
    Attempts PyYAML first; falls back to an internal pure-Python parser.
    """
    pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(pattern, text, re.DOTALL)
    if not match:
        raise ValueError("File does not contain valid '---' YAML frontmatter")

    raw_yaml, body = match.group(1), match.group(2)
    metadata_dict: Dict[str, Any] = {}

    try:
        import yaml
        loaded = yaml.safe_load(raw_yaml)
        if isinstance(loaded, dict):
            metadata_dict = loaded
    except ImportError:
        pass

    if not metadata_dict:
        # Zero-dependency flat & simple list YAML parser
        current_list_key: Optional[str] = None
        for line in raw_yaml.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue

            if line_str.startswith("- ") and current_list_key:
                val = line_str[2:].strip().strip('"').strip("'")
                metadata_dict[current_list_key].append(val)
            elif ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip()
                if not v:
                    metadata_dict[k] = []
                    current_list_key = k
                else:
                    current_list_key = None
                    v_clean = v.strip('"').strip("'")
                    is_bracketed = (v.startswith("[") and v.endswith("]")) or (
                        v_clean.startswith("[") and v_clean.endswith("]")
                    )
                    is_braced = (v.startswith("{") and v.endswith("}")) or (
                        v_clean.startswith("{") and v_clean.endswith("}")
                    )
                    if is_bracketed:
                        inner = v_clean[1:-1].strip()
                        if not inner:
                            metadata_dict[k] = []
                        else:
                            metadata_dict[k] = [
                                item.strip().strip('"\'')
                                for item in inner.split(",")
                                if item.strip().strip('"\'')
                            ]
                    elif is_braced:
                        inner = v_clean[1:-1].strip()
                        if not inner:
                            metadata_dict[k] = {}
                        else:
                            metadata_dict[k] = v_clean
                    elif v_clean.lower() in ("null", "none", "nil", "undefined", "~") or v_clean == "~":
                        metadata_dict[k] = None
                    elif v_clean.lower() == "true":
                        metadata_dict[k] = True
                    elif v_clean.lower() == "false":
                        metadata_dict[k] = False
                    else:
                        try:
                            metadata_dict[k] = float(v_clean) if "." in v_clean else int(v_clean)
                        except ValueError:
                            metadata_dict[k] = v_clean

    return metadata_dict, body


def _serialize_yaml_frontmatter(metadata: KnowledgeMetadata, body: str) -> str:
    """Serializes KnowledgeMetadata to YAML frontmatter and joins with markdown body."""
    lines = ["---"]
    lines.append(f"id: {metadata.id}")
    lines.append(f"title: \"{metadata.title}\"")
    lines.append(f"category: {metadata.category.value}")
    lines.append(f"version: {metadata.version}")
    lines.append(f"verification_status: {metadata.verification_status.value}")
    lines.append(f"confidence: {metadata.confidence}")
    lines.append(f"confidence_level: {metadata.confidence_level.value}")
    lines.append(f"owner_agent: {metadata.owner_agent}")

    lines.append("sources:")
    for s in metadata.sources:
        lines.append(f"  - \"{s}\"")

    lines.append("tags:")
    for t in metadata.tags:
        lines.append(f"  - {t}")

    lines.append("applies_to:")
    for a in metadata.applies_to:
        lines.append(f"  - \"{a}\"")

    lines.append(f"created_at: \"{metadata.created_at}\"")
    lines.append(f"last_verified: \"{metadata.last_verified}\"")
    lines.append("---")
    lines.append("")
    lines.append(body.strip())
    lines.append("")
    return "\n".join(lines)


# ─── Query, Retrieval & Search Engine ─────────────────────────────────────────

def parse_knowledge_file(file_path: Union[str, Path]) -> KnowledgeEntry:
    """Reads and parses a single knowledge markdown file."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Knowledge file not found: {path}")

    content = path.read_text(encoding="utf-8")
    meta_dict, body = _parse_yaml_frontmatter(content)
    meta = KnowledgeMetadata(**meta_dict)
    return KnowledgeEntry(metadata=meta, content=body, file_path=path)


def get_knowledge(entry_id: str, kb_root: Optional[Path] = None) -> Optional[KnowledgeEntry]:
    """Finds a knowledge entry across all categories by ID."""
    root = kb_root or KNOWLEDGE_ROOT
    entry_id = entry_id.strip()
    if not root.exists():
        return None

    for cat_dir in root.iterdir():
        if cat_dir.is_dir():
            for md_file in cat_dir.glob("*.md"):
                try:
                    entry = parse_knowledge_file(md_file)
                    if entry.metadata.id == entry_id:
                        return entry
                except Exception as exc:
                    logger.debug("Skipping unparseable %s: %s", md_file, exc)
    return None


def query_knowledge(
    category: Optional[Union[KnowledgeCategory, str]] = None,
    tags: Optional[List[str]] = None,
    min_confidence: float = 0.0,
    verification_status: Optional[Union[VerificationStatus, str]] = None,
    keyword: Optional[str] = None,
    kb_root: Optional[Path] = None,
) -> List[KnowledgeEntry]:
    """
    Queries knowledge entries with multi-attribute filtering.
    Results are sorted by confidence descending.
    """
    root = kb_root or KNOWLEDGE_ROOT
    if not root.exists():
        return []

    cat_str = category.value if isinstance(category, KnowledgeCategory) else (str(category).strip() if category else None)
    status_str = (
        verification_status.value
        if isinstance(verification_status, VerificationStatus)
        else (str(verification_status).strip("[]").replace("-", "_").upper() if verification_status else None)
    )
    tag_set = {t.lower() for t in (tags or [])}
    kw = keyword.lower() if keyword else None

    results: List[KnowledgeEntry] = []
    target_dirs = [root / cat_str] if cat_str else [d for d in root.iterdir() if d.is_dir()]

    for cat_dir in target_dirs:
        if not cat_dir.exists():
            continue
        for md_file in cat_dir.glob("*.md"):
            try:
                entry = parse_knowledge_file(md_file)
            except Exception as exc:
                logger.warning("Skipping invalid knowledge file %s: %s", md_file, exc)
                continue

            meta = entry.metadata
            if meta.confidence < min_confidence:
                continue
            if status_str and meta.verification_status.value != status_str:
                continue
            if tag_set and not tag_set.intersection({t.lower() for t in meta.tags}):
                continue
            if kw and (kw not in meta.title.lower() and kw not in entry.content.lower()):
                continue

            results.append(entry)

    results.sort(key=lambda x: x.metadata.confidence, reverse=True)
    return results


def search_knowledge_text(
    query: str,
    category: Optional[Union[KnowledgeCategory, str]] = None,
    limit: int = 5,
    kb_root: Optional[Path] = None,
) -> List[KnowledgeEntry]:
    """
    Performs relevance-scored keyword search across knowledge entries.
    Matches across title (weight 5.0), tags (weight 3.0), and body content (weight 0.5).
    """
    query_tokens = [q.lower() for q in re.findall(r"\w+", query) if len(q) >= 2]
    if not query_tokens:
        return []

    candidates = query_knowledge(category=category, kb_root=kb_root)
    scored: List[Tuple[float, KnowledgeEntry]] = []

    for entry in candidates:
        score = 0.0
        title_lower = entry.metadata.title.lower()
        content_lower = entry.content.lower()

        # Build expanded tag token set for word and component matching
        tag_tokens = set()
        for t in entry.metadata.tags:
            t_clean = str(t).lower()
            tag_tokens.add(t_clean)
            for part in t_clean.replace("-", " ").split():
                if part:
                    tag_tokens.add(part)

        for token in query_tokens:
            if token in title_lower:
                score += 5.0
            if token in tag_tokens:
                score += 3.0
            count = content_lower.count(token)
            score += min(count * 0.5, 4.0)

        if score > 0:
            status_multiplier = 1.2 if entry.metadata.verification_status == VerificationStatus.VERIFIED else 1.0
            final_score = score * status_multiplier * entry.metadata.confidence
            scored.append((final_score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in scored[:limit]]


# ─── Write, Validate & Audit ──────────────────────────────────────────────────

def write_knowledge(
    entry: KnowledgeEntry,
    overwrite: bool = False,
    kb_root: Optional[Path] = None,
) -> Path:
    """
    Writes or updates a knowledge markdown entry on disk.
    Enforces category folder routing and valid YAML frontmatter.
    """
    root = kb_root or KNOWLEDGE_ROOT
    cat_dir = root / entry.metadata.category.value
    cat_dir.mkdir(parents=True, exist_ok=True)

    slug = re.sub(r"[^a-z0-9\-]+", "-", entry.metadata.title.lower()).strip("-")
    target_path = entry.file_path or (cat_dir / f"{slug}.md")

    if target_path.exists() and not overwrite:
        raise FileExistsError(f"Knowledge file already exists: {target_path}. Set overwrite=True to update.")

    serialized = _serialize_yaml_frontmatter(entry.metadata, entry.content)
    target_path.write_text(serialized, encoding="utf-8")
    entry.file_path = target_path
    logger.info("Saved knowledge entry [%s] to %s", entry.metadata.id, target_path)
    return target_path


def validate_knowledge_file(file_path: Union[str, Path]) -> Tuple[bool, List[str]]:
    """
    Validates that a file adheres to all Knowledge Base schema and integrity rules.
    Returns (is_valid, errors_list).
    """
    errors: List[str] = []
    path = Path(file_path)
    if not path.is_file():
        return False, [f"File does not exist: {path}"]

    try:
        entry = parse_knowledge_file(path)
    except Exception as exc:
        return False, [f"Failed to parse frontmatter: {exc}"]

    meta = entry.metadata

    # Verification rules
    if meta.verification_status == VerificationStatus.VERIFIED:
        if meta.confidence < 0.80:
            errors.append(f"VERIFIED entry must have confidence >= 0.80, got {meta.confidence}")
        valid_sources = [
            s for s in meta.sources
            if isinstance(s, str) and not _is_null_or_dummy(s)
        ]
        if not valid_sources:
            errors.append("VERIFIED entry must contain at least one verifiable source citation in sources")

    # Category directory alignment check
    is_under_knowledge = any(p.name == "knowledge" for p in path.parents)
    is_in_category_dir = path.parent.name in VALID_CATEGORIES
    if is_under_knowledge or is_in_category_dir:
        if meta.category.value != path.parent.name:
            errors.append(
                f"File directory '{path.parent.name}' does not match metadata category '{meta.category.value}'"
            )

    if not meta.title:
        errors.append("Title cannot be empty")
    if not meta.tags:
        errors.append("Entry must contain at least one tag")
    if not entry.content.strip():
        errors.append("Markdown body content cannot be empty")

    return len(errors) == 0, errors


def audit_knowledge_base(kb_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Audits the complete knowledge base across all 8 categories.
    Returns summary metrics, counts by category/status, and any schema defects.
    """
    root = kb_root or KNOWLEDGE_ROOT
    summary: Dict[str, Any] = {
        "total_entries": 0,
        "by_category": {cat.value: 0 for cat in KnowledgeCategory},
        "by_status": {status.value: 0 for status in VerificationStatus},
        "errors": [],
        "unverified_count": 0,
    }

    if not root.exists():
        return summary

    for cat_dir in root.iterdir():
        if not cat_dir.is_dir():
            continue
        for md_file in cat_dir.glob("*.md"):
            summary["total_entries"] += 1
            is_valid, errs = validate_knowledge_file(md_file)
            if not is_valid:
                summary["errors"].append({"file": str(md_file), "errors": errs})
            else:
                entry = parse_knowledge_file(md_file)
                cat_val = entry.metadata.category.value
                summary["by_category"][cat_val] = summary["by_category"].get(cat_val, 0) + 1
                status_val = entry.metadata.verification_status.value
                summary["by_status"][status_val] = summary["by_status"].get(status_val, 0) + 1
                if entry.metadata.verification_status != VerificationStatus.VERIFIED:
                    summary["unverified_count"] += 1

    return summary


# ─── SQLite Index Synchronization ─────────────────────────────────────────────

def sync_to_db(
    conn: Optional[sqlite3.Connection] = None,
    kb_root: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> int:
    """
    Syncs all knowledge files into the SQLite `knowledge_index` table.
    Enables instant SQL querying and joins across agent pipelines.
    """
    root = kb_root or KNOWLEDGE_ROOT
    target_db = db_path or DB_PATH

    close_at_end = False
    if conn is None:
        target_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target_db)
        close_at_end = True

    conn.execute("""
    CREATE TABLE IF NOT EXISTS knowledge_index (
        id                  TEXT PRIMARY KEY,
        category            TEXT NOT NULL,
        title               TEXT NOT NULL,
        verification_status TEXT NOT NULL,
        confidence          REAL NOT NULL,
        confidence_level    TEXT NOT NULL,
        owner_agent         TEXT NOT NULL,
        file_path           TEXT NOT NULL,
        tags                TEXT,
        sources             TEXT,
        applies_to          TEXT,
        last_verified       TEXT,
        updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    synced_count = 0
    if root.exists():
        for cat_dir in root.iterdir():
            if not cat_dir.is_dir():
                continue
            for md_file in cat_dir.glob("*.md"):
                try:
                    entry = parse_knowledge_file(md_file)
                    m = entry.metadata
                    conn.execute("""
                    INSERT INTO knowledge_index 
                    (id, category, title, verification_status, confidence, confidence_level, owner_agent, file_path, tags, sources, applies_to, last_verified, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        category=excluded.category,
                        title=excluded.title,
                        verification_status=excluded.verification_status,
                        confidence=excluded.confidence,
                        confidence_level=excluded.confidence_level,
                        owner_agent=excluded.owner_agent,
                        file_path=excluded.file_path,
                        tags=excluded.tags,
                        sources=excluded.sources,
                        applies_to=excluded.applies_to,
                        last_verified=excluded.last_verified,
                        updated_at=CURRENT_TIMESTAMP
                    """, (
                        m.id,
                        m.category.value,
                        m.title,
                        m.verification_status.value,
                        m.confidence,
                        m.confidence_level.value,
                        m.owner_agent,
                        str(md_file),
                        json.dumps(m.tags),
                        json.dumps(m.sources),
                        json.dumps(m.applies_to),
                        m.last_verified,
                    ))
                    synced_count += 1
                except Exception as exc:
                    logger.error("Failed to sync %s to SQLite: %s", md_file, exc)

    conn.commit()
    if close_at_end:
        conn.close()

    logger.info("Synced %d knowledge entries to SQLite index", synced_count)
    return synced_count
