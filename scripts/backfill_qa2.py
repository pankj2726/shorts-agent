"""
backfill_qa2.py — Backfill Stage A and full QA2 Anti-Slop evaluation across Phase 2 scripts.
"""

import json
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.modules.qa2 import AntiSlopPipeline


def main():
    p = Path("agent/state/phase2_scripts_report.json")
    if not p.exists():
        print("phase2_scripts_report.json not found.")
        return

    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)

    scripts = data if isinstance(data, list) else data.get("scripts", [])
    print(f"Loaded {len(scripts)} scripts. Running QA2 Anti-Slop evaluation...")

    pipeline = AntiSlopPipeline(pass_threshold=80.0)
    results = []
    passed_count = 0

    for i, s in enumerate(scripts, 1):
        verdict = pipeline.evaluate_script(s)
        if verdict["passed"]:
            passed_count += 1
        results.append({
            "script_id": i,
            "title": s.get("title", ""),
            "composite_score": verdict["composite_score"],
            "passed": verdict["passed"],
            "hook_score": verdict["stages"]["stage_a_hook_logic"]["score"],
            "slop_count": verdict["stages"]["stage_e_anti_cliche"]["slop_detected_count"]
        })
        print(f"  Script #{i:02d} | Score: {verdict['composite_score']:4.1f}/100 | Hook: {verdict['stages']['stage_a_hook_logic']['score']} | Passed: {verdict['passed']}")

    print(f"\nFinal Summary: {passed_count}/{len(scripts)} scripts passed QA2 Anti-Slop standards.")

    # Save evaluation report
    out_path = Path("agent/state/qa2_backfill_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "total": len(scripts),
            "passed": passed_count,
            "pass_rate": f"{(passed_count / len(scripts)) * 100:.1f}%",
            "evaluations": results
        }, f, indent=2)
    print(f"Report saved to {out_path}")


if __name__ == "__main__":
    main()
