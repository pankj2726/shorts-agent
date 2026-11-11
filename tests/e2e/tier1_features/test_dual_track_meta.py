"""
test_dual_track_meta.py — Feature 18: Dual-Track E2E Test Suite & Master Runner Meta-Tests.

Validates:
  1. CLI argument parsing options for tiers (1, 2, 3, 4, all) and flags (-v, -x, --collect-only).
  2. Master runner FEATURE_MAP covering all 20 architectural features.
  3. Seamless dry-run test collection execution.
  4. Integrity of the test directory layout.
  5. conftest fixture hub exports and availability.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import pytest

from run_e2e_tests import build_parser, FEATURE_MAP, TIER_MAP


def test_runner_argument_parser_tiers():
    """Verify build_parser accepts all defined test tier options and flags."""
    parser = build_parser()
    for tier in ["1", "2", "3", "4", "all"]:
        args = parser.parse_args(["--tier", tier])
        assert args.tier == tier

    args_v = parser.parse_args(["-v", "--collect-only", "-x"])
    assert args_v.verbose is True
    assert args_v.collect_only is True
    assert args_v.exitfirst is True


def test_runner_feature_map_contains_all_20_features():
    """Verify FEATURE_MAP defines mappings for all 20 features without gaps."""
    for feat_id in range(1, 21):
        assert feat_id in FEATURE_MAP
        assert isinstance(FEATURE_MAP[feat_id], str)
        assert len(FEATURE_MAP[feat_id]) > 0


def test_runner_collect_only_execution():
    """Verify master runner dry-run collection executes cleanly with returncode 0."""
    script_path = Path(__file__).resolve().parent.parent.parent.parent / "run_e2e_tests.py"
    proc = subprocess.run(
        [sys.executable, str(script_path), "--collect-only", "--tier", "1"],
        capture_output=True,
        text=True
    )
    assert proc.returncode == 0
    assert "Status: [OK] Test collection dry-run completed successfully" in proc.stdout or "collected" in proc.stdout


def test_tier1_directory_structure_exists():
    """Verify tests/e2e/tier1_features directory exists and contains test files."""
    t1_dir = TIER_MAP["1"]
    assert t1_dir.exists()
    assert t1_dir.is_dir()
    py_files = list(t1_dir.glob("test_*.py"))
    assert len(py_files) >= 5


def test_conftest_fixtures_available():
    """Verify conftest.py defines required fixtures and SSE parsing helpers."""
    import tests.e2e.conftest as conftest

    assert hasattr(conftest, "zero_cost_guard")
    assert hasattr(conftest, "e2e_db")
    assert hasattr(conftest, "mock_gateway_server")
    assert hasattr(conftest, "e2e_client")
    assert hasattr(conftest, "parse_anthropic_sse")
    assert hasattr(conftest, "parse_openai_sse")
    assert hasattr(conftest, "extract_sse_text")
