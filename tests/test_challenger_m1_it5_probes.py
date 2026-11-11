"""
test_challenger_m1_it5_probes.py — Milestone 1 Iteration 5 Adversarial Probe Harness.

Authored by: Milestone 1 Iteration 5 Challenger 1 (teamwork_challenger_m1_it5_1)

Exhaustively verifies container and delimiter unwrapping in `shorts-agent/agent/modules/knowledge.py`:
1. Deep nested curly braces:
   - `{{{{}}}}`, `{{{{null}}}}`, `{{{{none}}}}`, `{{{{~}}}}`, `{{{{nil}}}}`, etc.
   - Dynamic fuzzing across arbitrary brace depths (1 to 50).
   - Curly braces containing internal whitespace, escaped quotes, tabs, newlines.
2. Mixed nested delimiters:
   - Nested combinations of {}, [], and (): `{[()]}`, `[({[]})]`, `{[{~}]}`, `({[null]})`, `((({{{[[[()]]]}}})))`.
   - Delimiters with null tokens, whitespace, and mixed quotes.
3. Empty Python native containers:
   - `dict`: `{}`, `{"": ""}`, `{"k": None}`, `{None: None}`, `{"a": {}}`, nested dummy dicts.
   - `set`: `set()`, `{""}`, `{"null"}`, `{None}`, `{"~"}`.
   - `list`: `[]`, `[[]]`, `[{}]`, `[set()]`, `[()]`, `[None]`, `[""]`, `[{}, {}]`.
   - `tuple`: `()`, `(())`, `((),)`, `({},)`, `(set(),)`, `(None,)`, `("",)`.
   - Mixed deep container hierarchies evaluating to empty or dummy values.
4. Stringified mapping and container variations:
   - `"{}"`, `"{ }"`, `"{   }"`, `"{\t}"`, `"{\n}"`, `"{\r\n}"`, `"{\t\n  }"`.
   - `[{}]`, `["{}"]`, `["{ }"]`, `["{\t}"]`, `[["{}"]]`.
5. Authentic citations containing brackets, braces, and parens:
   - Verification that authentic citations with delimiters are NEVER falsely rejected.
   - `"[YouTube Creator Playbook 2024]"`, `"{BibTeX Key 2023}"`, `"(Smith, 2020)"`, `"[Smith (2020)]"`.
   - URLs with bracket/brace query parameters: `"https://example.com/api?filter=[published]&sort={desc}"`.
   - Complex bracketed reference keys: `"[RFC 2119] (https://tools.ietf.org/html/rfc2119)"`, `"{arXiv:2301.00001}"`.
   - Nested authentic references: `"[{(Important Reference)}]"`.
6. End-to-end YAML frontmatter validation and KnowledgeBase audits:
   - `validate_knowledge_file()` rejects dummy nested delimiters and containers for VERIFIED.
   - `validate_knowledge_file()` accepts legitimate delimited citations for VERIFIED.
   - `audit_knowledge_base()` metrics and error accounting.
"""
from __future__ import annotations

import itertools
import string
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
    validate_knowledge_file,
    write_knowledge,
)


# ==============================================================================
# SECTION 1: DEEP NESTED CURLY BRACES
# ==============================================================================

class TestDeepNestedCurlyBraces:
    """Stress tests deep nested curly braces and null tokens wrapped in braces."""

    @pytest.mark.parametrize("depth", [1, 2, 3, 4, 5, 10, 20, 35, 50])
    @pytest.mark.parametrize("null_token", [
        "", " ", "   ", "\t", "\n", "\r\n", "null", "none", "nil", "undefined", "~", "n/a", "na", "-", "–", "—"
    ])
    def test_nested_curly_braces_with_null_tokens(self, depth: int, null_token: str):
        """Curly braces of depth N enclosing null tokens must be recognized as dummy."""
        open_braces = "{" * depth
        close_braces = "}" * depth
        probe = f"{open_braces}{null_token}{close_braces}"

        assert _is_null_or_dummy(probe) is True, (
            f"_is_null_or_dummy({probe!r}) should be True at depth {depth}"
        )
        assert _normalize_string_list(probe) == [], (
            f"_normalize_string_list({probe!r}) should be []"
        )

    @pytest.mark.parametrize("depth", [1, 2, 3, 5, 10, 20])
    @pytest.mark.parametrize("quote", ['"', "'", " \" ", " ' "])
    def test_nested_curly_braces_with_quoted_null_tokens(self, depth: int, quote: str):
        """Curly braces enclosing quoted null tokens must be recognized as dummy."""
        q = quote.strip()
        open_braces = "{" * depth
        close_braces = "}" * depth
        for token in ["null", "none", "~", "", " "]:
            probe = f"{open_braces}{q}{token}{q}{close_braces}"
            assert _is_null_or_dummy(probe) is True, (
                f"_is_null_or_dummy({probe!r}) with quote {quote!r} should be True"
            )
            assert _normalize_string_list(probe) == [], (
                f"_normalize_string_list({probe!r}) should be []"
            )

    def test_nested_curly_braces_in_frontmatter_rejection(self, tmp_path: Path):
        """YAML frontmatter with deep curly brace dummy tokens fails validation for VERIFIED."""
        nested_tokens = [
            "{{{{}}}}",
            "{{{{null}}}}",
            "{{{{none}}}}",
            "{{{{~}}}}",
            "{{{{{{nil}}}}}}",
            "{{{{{{{{undefined}}}}}}}}",
            "{{{{{ 'null' }}}}}",
            "{{{{   '~'   }}}}",
        ]
        for idx, token in enumerate(nested_tokens):
            file_path = tmp_path / f"nested_brace_{idx}.md"
            content = (
                f"---\n"
                f"id: kb-nested-brace-{idx}\n"
                f"title: Nested Brace Probe {idx}\n"
                f"category: research\n"
                f"verification_status: VERIFIED\n"
                f"confidence: 0.90\n"
                f"sources: {token}\n"
                f"tags: [brace, test]\n"
                f"---\n"
                f"Body content for nested brace test {idx}."
            )
            file_path.write_text(content, encoding="utf-8")
            is_valid, errors = validate_knowledge_file(file_path)
            assert is_valid is False, f"Token {token} bypassed citation gating!"
            assert any("verifiable source citation" in err for err in errors)


# ==============================================================================
# SECTION 2: MIXED DELIMITERS
# ==============================================================================

class TestMixedDelimiters:
    """Stress tests mixed nesting of {}, [], and () delimiters."""

    MIXED_PATTERNS = [
        # (open_pattern, close_pattern)
        ("{[(", ")]}"),
        ("[({", "})]"),
        ("([{{[", "]}}])"),
        ("{[({[", "]})]}"),
        ("[({[]})]", ""),  # preformed closed
        ("{[()]}", ""),
        ("((({{{[[[()]]]}}})))", ""),
        ("[{(~)}]", ""),
        ("({[null]})", ""),
        ("[{('none')}]", ""),
        ("{[(undefined)]}", ""),
        ("[({['~']})]", ""),
        ("((({[[{   }]]})))", ""),
        ("{( [ ( { } ) ] )}", ""),
    ]

    def test_preformed_mixed_delimiters_dummy_recognition(self):
        """All preformed mixed delimiter patterns must be recognized as null or dummy."""
        for open_p, close_p in self.MIXED_PATTERNS:
            val = f"{open_p}{close_p}"
            assert _is_null_or_dummy(val) is True, (
                f"_is_null_or_dummy({val!r}) returned False, expected True"
            )
            assert _normalize_string_list(val) == [], (
                f"_normalize_string_list({val!r}) returned non-empty list"
            )

    @pytest.mark.parametrize("null_val", ["", " ", "null", "none", "~", "nil", "undefined"])
    def test_fuzzed_mixed_delimiter_combinations(self, null_val: str):
        """Permutations of nested opening and matching closing delimiters must evaluate to dummy."""
        delims = [("{", "}"), ("[", "]"), ("(", ")")]
        # Generate nested wrapping of 4 levels
        for p in itertools.permutations(delims, 3):
            opener = "".join(d[0] for d in p)
            closer = "".join(d[1] for d in reversed(p))
            probe = f"{opener}{null_val}{closer}"
            assert _is_null_or_dummy(probe) is True, (
                f"Failed for mixed delimiter probe: {probe!r}"
            )
            assert _normalize_string_list(probe) == [], (
                f"Failed normalizer for mixed delimiter probe: {probe!r}"
            )

    def test_mixed_delimiters_in_yaml_frontmatter_rejection(self, tmp_path: Path):
        """YAML frontmatter with mixed delimiter dummy citations fails validation for VERIFIED."""
        tokens = [
            "{[()]}",
            "[({[]})]",
            "{[{~}]}",
            "({[null]})",
            "((({{{[[[()]]]}}})))",
            "[{(~)}]",
        ]
        for idx, token in enumerate(tokens):
            fpath = tmp_path / f"mixed_delim_{idx}.md"
            content = (
                f"---\n"
                f"id: kb-mixed-delim-{idx}\n"
                f"title: Mixed Delim Probe {idx}\n"
                f"category: youtube\n"
                f"verification_status: VERIFIED\n"
                f"confidence: 0.95\n"
                f"sources: {token}\n"
                f"tags: [delim, probe]\n"
                f"---\n"
                f"Body content testing mixed delim bypass."
            )
            fpath.write_text(content, encoding="utf-8")
            is_valid, errors = validate_knowledge_file(fpath)
            assert is_valid is False, f"Token {token} bypassed citation gating!"
            assert any("verifiable source citation" in err for err in errors)


# ==============================================================================
# SECTION 3: PYTHON NATIVE CONTAINERS
# ==============================================================================

class TestNativePythonContainers:
    """Stress tests Python native collection types: dict, set, list, tuple."""

    @pytest.mark.parametrize("empty_container", [
        {},
        dict(),
        set(),
        [],
        list(),
        (),
        tuple(),
    ])
    def test_empty_containers_recognized_as_null(self, empty_container: Any):
        """Empty native containers must return True for _is_null_or_dummy."""
        assert _is_null_or_dummy(empty_container) is True, (
            f"Failed for container: {empty_container!r}"
        )
        assert _normalize_string_list(empty_container) == [], (
            f"Failed normalizer for container: {empty_container!r}"
        )

    def test_dicts_with_dummy_keys_and_values(self):
        """Dicts containing null/dummy keys and values must evaluate to dummy."""
        dummy_dicts = [
            {"": ""},
            {"null": "null"},
            {"none": None},
            {None: None},
            {"~": "~"},
            {"": None},
            {"null": ""},
            {"~": "none"},
            {"nil": "undefined"},
        ]
        for d in dummy_dicts:
            assert _is_null_or_dummy(d) is True, f"_is_null_or_dummy({d!r}) expected True"
            assert _normalize_string_list(d) == [], f"_normalize_string_list({d!r}) expected []"

    def test_any_dict_normalizes_to_empty_list(self):
        """Any dictionary object passed as sources must normalize to [] to prevent bypasses."""
        sample_dicts = [
            {},
            {"title": "Paper"},
            {"k": None},
            {"k": "null"},
            {"k": []},
            {"author": "Unknown", "year": 2024},
            {"sources": ["https://example.com"]},
        ]
        for d in sample_dicts:
            assert _normalize_string_list(d) == [], f"_normalize_string_list({d!r}) expected []"

    def test_sets_with_dummy_elements(self):
        """Sets containing only dummy elements must evaluate to dummy."""
        dummy_sets = [
            set(),
            {""},
            {" "},
            {"null"},
            {"none"},
            {"~"},
            {"nil"},
            {"undefined"},
            (("", "null")),  # tuple in set-like context
        ]
        for s in dummy_sets:
            assert _is_null_or_dummy(s) is True, f"_is_null_or_dummy({s!r}) expected True"
            assert _normalize_string_list(s) == [], f"_normalize_string_list({s!r}) expected []"

    def test_nested_container_hierarchies(self):
        """Complex recursive container structures evaluating to dummy."""
        dummy_hierarchies = [
            [{}],
            [[{}]],
            [[[{}]]],
            [set()],
            [()],
            [([], {})],
            [{"": ""}],
            [{"null": None}],
            [{"~": "~"}],
            (({},),),
            ((set(),),),
            [([{}],)],
            [None, "", "   ", {}, [], (), set()],
        ]
        for h in dummy_hierarchies:
            assert _is_null_or_dummy(h) is True, f"_is_null_or_dummy({h!r}) expected True"
            assert _normalize_string_list(h) == [], f"_normalize_string_list({h!r}) expected []"

    def test_metadata_instantiation_with_containers(self):
        """KnowledgeMetadata must safely handle native container types in sources and tags."""
        # sources with dummy dict
        meta_dict = KnowledgeMetadata(
            id="kb-meta-dict",
            title="Meta Dict Test",
            category=KnowledgeCategory.RESEARCH,
            verification_status=VerificationStatus.VERIFIED,
            confidence=0.9,
            sources={},
            tags=["tag1", "tag2"],
        )
        assert meta_dict.sources == []

        # sources with dummy set
        meta_set = KnowledgeMetadata(
            id="kb-meta-set",
            title="Meta Set Test",
            category=KnowledgeCategory.RESEARCH,
            verification_status=VerificationStatus.VERIFIED,
            confidence=0.9,
            sources=set(),
            tags=["tag1"],
        )
        assert meta_set.sources == []

        # sources with nested list of dicts
        meta_nested = KnowledgeMetadata(
            id="kb-meta-nested",
            title="Meta Nested Test",
            category=KnowledgeCategory.RESEARCH,
            verification_status=VerificationStatus.VERIFIED,
            confidence=0.9,
            sources=[{}, {"": ""}],
            tags=["tag1"],
        )
        assert meta_nested.sources == []

        # set of valid string sources converts properly
        meta_valid_set = KnowledgeMetadata(
            id="kb-meta-valid-set",
            title="Meta Valid Set Test",
            category=KnowledgeCategory.RESEARCH,
            verification_status=VerificationStatus.VERIFIED,
            confidence=0.9,
            sources={"https://example.com/source-a", "https://example.com/source-b"},
            tags={"tag1", "tag2"},
        )
        assert len(meta_valid_set.sources) == 2
        assert len(meta_valid_set.tags) == 2


# ==============================================================================
# SECTION 4: STRINGIFIED MAPPING VARIATIONS
# ==============================================================================

class TestStringifiedMappingVariations:
    """Stress tests stringified mapping representations with various whitespace and nesting."""

    @pytest.mark.parametrize("val", [
        "{}",
        "{ }",
        "{   }",
        "{\t}",
        "{\n}",
        "{\r\n}",
        "{\t\n  }",
        "[{}]",
        "[{ }]",
        "[{   }]",
        "['{}']",
        '["{}"]',
        "[['{}']]",
        "{ \t }",
        "{ \n }",
        "[ { } ]",
        "['{ }']",
        '["{ }"]',
        "[ [ { } ] ]",
    ])
    def test_stringified_mappings_recognized_as_null(self, val: str):
        """Stringified mappings must return True for _is_null_or_dummy."""
        assert _is_null_or_dummy(val) is True, f"_is_null_or_dummy({val!r}) expected True"
        assert _normalize_string_list(val) == [], f"_normalize_string_list({val!r}) expected []"

    def test_stringified_mapping_in_list_normalizer(self):
        """Lists containing stringified mappings must have them stripped out."""
        raw_list = ["{}", "{ }", "{\t}", "[{}]", "valid citation"]
        result = _normalize_string_list(raw_list)
        assert result == ["valid citation"], f"Expected ['valid citation'], got {result}"


# ==============================================================================
# SECTION 5: AUTHENTIC CITATIONS PRESERVATION (ZERO FALSE POSITIVES)
# ==============================================================================

class TestAuthenticCitationPreservation:
    """
    Empirically verifies that authentic citations containing braces, parens, brackets,
    and special characters are NEVER falsely rejected as null or dummy.
    """

    AUTHENTIC_CITATIONS = [
        "[1]",
        "[42]",
        "[YouTube Creator Playbook 2024]",
        "[MrBeast Retention Breakdown #14]",
        "{BibTeX Key 2023}",
        "{Vaswani et al. 2017: Attention Is All You Need}",
        "(Smith, 2020)",
        "(Johnson & Brown, 2021, p. 45)",
        "[Smith (2020)]",
        "(Smith [2020])",
        "https://example.com/api?filter=[published]&sort={desc}",
        "https://youtube.com/watch?v=1234567890#t=1m30s",
        "https://arxiv.org/abs/2301.00001v2",
        "[RFC 2119] (https://tools.ietf.org/html/rfc2119)",
        "{arXiv:2301.00001}",
        "Dataset: {name: 'v1', year: 2024}",
        "[Section 4.2] (Figure 3)",
        "Algorithm (v2.1) [Optimized]",
        "[{(Important Reference)}]",
        "{[(Valid Citation 2024)]}",
        "((Valid Citation Wrapped Twice))",
        "[[Standard Reference with Double Brackets]]",
        "{{Standard Reference with Double Braces}}",
        "YouTube Analytics API v3: https://developers.google.com/youtube/v3",
        "Internal Memo (Q3-2024-Review)",
    ]

    @pytest.mark.parametrize("citation", AUTHENTIC_CITATIONS)
    def test_authentic_citation_is_not_dummy(self, citation: str):
        """Authentic citations must evaluate _is_null_or_dummy() == False."""
        assert _is_null_or_dummy(citation) is False, (
            f"False positive: authentic citation {citation!r} was falsely flagged as dummy!"
        )

    @pytest.mark.parametrize("citation", AUTHENTIC_CITATIONS)
    def test_authentic_citation_normalizes_cleanly(self, citation: str):
        """Authentic citations must not be stripped to empty by _normalize_string_list."""
        result = _normalize_string_list(citation)
        assert len(result) >= 1, (
            f"False positive: authentic citation {citation!r} normalized to empty list!"
        )

    def test_authentic_citations_pass_knowledge_file_validation(self, tmp_path: Path):
        """VERIFIED knowledge files with authentic bracketed/braced citations pass validation."""
        for idx, citation in enumerate(self.AUTHENTIC_CITATIONS):
            fpath = tmp_path / f"authentic_{idx}.md"
            # Format safely for YAML frontmatter
            escaped_citation = citation.replace('"', '\\"')
            content = (
                f"---\n"
                f"id: kb-authentic-{idx}\n"
                f"title: Authentic Citation Test {idx}\n"
                f"category: youtube\n"
                f"verification_status: VERIFIED\n"
                f"confidence: 0.95\n"
                f"sources:\n"
                f"  - \"{escaped_citation}\"\n"
                f"tags: [authentic, test]\n"
                f"---\n"
                f"Body content testing authentic citation: {citation}."
            )
            fpath.write_text(content, encoding="utf-8")
            is_valid, errors = validate_knowledge_file(fpath)
            assert is_valid is True, (
                f"False positive: authentic citation {citation!r} failed validation! Errors: {errors}"
            )

    def test_mixed_valid_and_dummy_sources_preserves_valid(self):
        """When given a mix of valid citations and dummy values, only valid citations survive."""
        mixed = [
            "{{{{}}}}",
            "Valid Source A",
            "[({[]})]",
            "Valid Source B (2024)",
            "{   }",
            "set()",
            None,
        ]
        result = _normalize_string_list(mixed)
        assert result == ["Valid Source A", "Valid Source B (2024)"]


# ==============================================================================
# SECTION 6: END-TO-END AUDIT & LIFECYCLE TESTS
# ==============================================================================

class TestKnowledgeLifecycleAndAudit:
    """Verifies that knowledge audits and write operations enforce gating seamlessly."""

    def test_audit_identifies_dummy_sources_in_verified_entries(self, tmp_path: Path):
        """audit_knowledge_base reports error on VERIFIED file with dummy delimiters."""
        kb_root = tmp_path / "knowledge"
        cat_dir = kb_root / "youtube"
        cat_dir.mkdir(parents=True, exist_ok=True)

        # Invalid entry with deep curly braces
        bad_file = cat_dir / "bad-entry.md"
        bad_file.write_text(
            "---\n"
            "id: kb-bad-audit\n"
            "title: Bad Audit Entry\n"
            "category: youtube\n"
            "verification_status: VERIFIED\n"
            "confidence: 0.95\n"
            "sources: '{{{{}}}}'\n"
            "tags: [test]\n"
            "---\n"
            "Body for bad audit.",
            encoding="utf-8"
        )

        # Valid entry with authentic citation
        good_file = cat_dir / "good-entry.md"
        good_file.write_text(
            "---\n"
            "id: kb-good-audit\n"
            "title: Good Audit Entry\n"
            "category: youtube\n"
            "verification_status: VERIFIED\n"
            "confidence: 0.95\n"
            "sources: '[YouTube Playbook 2024]'\n"
            "tags: [test]\n"
            "---\n"
            "Body for good audit.",
            encoding="utf-8"
        )

        audit = audit_knowledge_base(kb_root=kb_root)
        assert audit["total_entries"] == 2
        assert len(audit["errors"]) == 1
        assert "bad-entry.md" in audit["errors"][0]["file"]
        assert any("verifiable source citation" in err for err in audit["errors"][0]["errors"])

    def test_write_and_parse_roundtrip_with_braced_citations(self, tmp_path: Path):
        """write_knowledge and parse_knowledge_file roundtrip preserves complex braced citations."""
        kb_root = tmp_path / "knowledge"
        entry = KnowledgeEntry(
            metadata=KnowledgeMetadata(
                id="kb-roundtrip-braced",
                title="Roundtrip Braced Citation Entry",
                category=KnowledgeCategory.RESEARCH,
                verification_status=VerificationStatus.VERIFIED,
                confidence=0.95,
                sources=["{BibTeX: Vaswani 2017}", "[Attention Mechanism Paper]"],
                tags=["roundtrip", "citation"],
            ),
            content="This is the body content of the roundtrip entry.",
        )

        written_path = write_knowledge(entry, kb_root=kb_root)
        assert written_path.exists()

        is_valid, errors = validate_knowledge_file(written_path)
        assert is_valid is True, f"Validation failed with errors: {errors}"

        re_parsed = parse_knowledge_file(written_path)
        assert re_parsed.metadata.id == "kb-roundtrip-braced"
        assert len(re_parsed.metadata.sources) == 2
        assert "{BibTeX: Vaswani 2017}" in re_parsed.metadata.sources
        assert "[Attention Mechanism Paper]" in re_parsed.metadata.sources
