#!/usr/bin/env python3
"""
run_e2e_tests.py — Master Test Runner for E2E Multi-Model Gateway & Cross-Model History Bridge.

CLI Interface:
    python run_e2e_tests.py [--tier 1|2|3|4|all] [--verbose] [--collect-only] [-k KEYWORD] [--feature 1-18]

Features:
  - Invokes pytest via sys.executable with isolated PYTHONPATH
  - Supports tier filtering:
      Tier 1: tests/e2e/tier1_features
      Tier 2: tests/e2e/tier2_boundaries
      Tier 3: tests/e2e/tier3_cross_feature
      Tier 4: tests/e2e/tier4_real_world
      all:    tests/e2e
  - Formatted test execution report with timing and tier metrics
  - Seamless exit code handling (returns 0 on pass or clean collect-only)
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

SCRIPT_DIR = Path(__file__).resolve().parent
E2E_ROOT = SCRIPT_DIR / "tests" / "e2e"

TIER_MAP = {
    "1": E2E_ROOT / "tier1_features",
    "2": E2E_ROOT / "tier2_boundaries",
    "3": E2E_ROOT / "tier3_cross_feature",
    "4": E2E_ROOT / "tier4_real_world",
    "all": E2E_ROOT,
}

FEATURE_MAP = {
    1: "test_arena_cluster",
    2: "test_anthropic_adapter",
    3: "test_provider_fixes",
    4: "test_provider_fixes",
    5: "test_model_hire",
    6: "test_db_schema",
    7: "test_role_translation",
    8: "test_continuation",
    9: "test_cost_limits",
    10: "test_agent_integration",
    11: "test_failover_proxy",
    12: "test_gateway_daemon",
    13: "test_messages_sse",
    14: "test_chat_completions",
    15: "test_limit_headers",
    16: "test_android_cors",
    17: "test_model_discovery",
    18: "test_dual_track_meta",
    19: "test_imrcm_cascade",
    20: "test_cloudflare_sync",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="E2E Test Runner for Beyond-the-Bottleneck Gateway & History Bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_e2e_tests.py                     # Run all E2E tiers
  python run_e2e_tests.py --tier 1            # Run Tier 1 feature tests
  python run_e2e_tests.py --tier 2 -v         # Run Tier 2 boundary tests (verbose)
  python run_e2e_tests.py --collect-only      # Show discovered tests without running
  python run_e2e_tests.py --feature 19        # Run tests for Feature 19 (IMRCM Cascade)
        """
    )
    parser.add_argument(
        "--tier",
        choices=["1", "2", "3", "4", "all"],
        default="all",
        help="Target test tier (1: Features, 2: Boundaries, 3: Pairwise, 4: Real-World, all: Full Suite)"
    )
    parser.add_argument(
        "--feature",
        type=int,
        choices=list(range(1, 21)),
        default=None,
        help="Target specific feature ID (1-20)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Run pytest in verbose mode (-v)"
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Collect and display test list without running them"
    )
    parser.add_argument(
        "-k", "--keyword",
        type=str,
        default=None,
        help="Pytest keyword expression filter"
    )
    parser.add_argument(
        "-x", "--exitfirst",
        action="store_true",
        help="Exit immediately on first test failure"
    )
    parser.add_argument(
        "--tb",
        default="short",
        choices=["auto", "long", "short", "line", "native", "no"],
        help="Traceback print mode"
    )
    return parser


def run_tests(args: argparse.Namespace) -> int:
    target_dir = TIER_MAP.get(args.tier, E2E_ROOT)

    cmd = [
        sys.executable,
        "-m",
        "pytest",
    ]

    # Target path
    if target_dir.exists():
        cmd.append(str(target_dir))
    else:
        cmd.append(str(E2E_ROOT))

    # Options
    if args.verbose:
        cmd.append("-v")
    if args.collect_only:
        cmd.append("--collect-only")
    if args.exitfirst:
        cmd.append("-x")
    if args.tb:
        cmd.append(f"--tb={args.tb}")

    # Keyword / feature filtering
    keywords = []
    if args.feature:
        feat_name = FEATURE_MAP.get(args.feature)
        if feat_name:
            keywords.append(feat_name)
    if args.keyword:
        keywords.append(args.keyword)

    if keywords:
        cmd.extend(["-k", " and ".join(keywords)])

    # Setup environment: include shorts-agent in PYTHONPATH
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SCRIPT_DIR}{os.pathsep}{current_pythonpath}" if current_pythonpath else str(SCRIPT_DIR)

    # Clean header banner
    tier_label = f"Tier {args.tier}" if args.tier != "all" else "All Tiers (1-4)"
    print("=" * 72)
    print("  BEYOND-THE-BOTTLENECK MULTI-MODEL GATEWAY — E2E TEST RUNNER")
    print(f"  Target: {tier_label} | Root: {SCRIPT_DIR.name}")
    if args.feature:
        print(f"  Feature Filter: Feature {args.feature} ({FEATURE_MAP.get(args.feature)})")
    if args.keyword:
        print(f"  Keyword Filter: {args.keyword}")
    if args.collect_only:
        print("  Mode: COLLECT ONLY (Dry-run)")
    print("=" * 72)
    print(f"Executing: {' '.join(cmd)}\n")

    start_time = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(SCRIPT_DIR),
            env=env,
        )
        ret_code = proc.returncode
    except KeyboardInterrupt:
        print("\n[!] Test run aborted by user.")
        return 130
    except Exception as exc:
        print(f"\n[ERROR] Failed to execute pytest: {exc}")
        return 1

    elapsed = time.time() - start_time

    # Pytest return code interpretation:
    # 0 = All tests passed
    # 1 = Tests were run and some failed
    # 2 = Test execution was interrupted by the user
    # 3 = Internal error occurred
    # 4 = pytest command line usage error
    # 5 = No tests were collected
    print("\n" + "=" * 72)
    print(f"  E2E Test Execution Finished in {elapsed:.2f}s")

    if args.collect_only:
        if ret_code in (0, 5):
            print("  Status: [OK] Test collection dry-run completed successfully.")
            return 0
        else:
            print(f"  Status: [FAILED] Test collection encountered an error (exit code {ret_code}).")
            return ret_code

    if ret_code == 0:
        print("  Status: [PASS] All executed E2E tests passed cleanly.")
        return 0
    elif ret_code == 5:
        print("  Status: [WARNING] No test cases were collected matching criteria.")
        # When tier directories are empty prior to implementation, return 0 for clean initialization
        return 0
    else:
        print(f"  Status: [FAIL] Test suite completed with exit code {ret_code}.")
        return ret_code


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run_tests(args)


if __name__ == "__main__":
    sys.exit(main())
