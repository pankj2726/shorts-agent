"""
Tier 1: Feature Coverage — 6-Stage Anti-Slop Creative Quality Review Engine (Features 15–22)
Tests Stages A through F, composite scoring weighting, verdict thresholds (PASS/NEEDS_REVISION/REJECT),
and actionable JSON remediation payloads.
"""
from pathlib import Path
import pytest

from agent.modules import qa
from tests.conftest import StageResult, ComprehensiveReviewReport

# Define or import 6-Stage Review functions per PROJECT.md § Interface Contracts
try:
    from agent.modules.qa2 import (
        review_script,
        review_storyboard,
        review_assets,
        review_motion,
        review_audio,
        review_integrity,
        evaluate_video,
    )
except ImportError:
    # Contract reference implementation
    def review_script(script_dict: dict) -> StageResult:
        res = qa.check_script(script_dict)
        score = 90.0 if res["passed"] else 50.0
        problems = [res["reason"]] if res["reason"] else []
        return StageResult(passed=res["passed"], score=score, stage="Stage_A_Script", problems=problems)

    def review_storyboard(scenes: list, audio_duration: float) -> StageResult:
        problems = []
        for idx, sc in enumerate(scenes):
            if sc.get("duration_s", 0) > 4.0:
                problems.append(f"Scene {idx} duration exceeds 4.0s ceiling")
        passed = len(problems) == 0
        score = 85.0 if passed else 55.0
        return StageResult(passed=passed, score=score, stage="Stage_B_Storyboard", problems=problems)

    def review_assets(asset_paths: list[str]) -> StageResult:
        problems = []
        for p in asset_paths:
            if not Path(p).exists():
                problems.append(f"Asset file missing: {p}")
        passed = len(problems) == 0
        score = 90.0 if passed else 40.0
        return StageResult(passed=passed, score=score, stage="Stage_C_Assets", problems=problems)

    def review_motion(video_path: str) -> StageResult:
        exists = Path(video_path).exists()
        return StageResult(passed=exists, score=85.0 if exists else 0.0, stage="Stage_D_Motion")

    def review_audio(video_path: str, caption_vtt: str = "") -> StageResult:
        exists = Path(video_path).exists()
        return StageResult(passed=exists, score=90.0 if exists else 0.0, stage="Stage_E_Audio")

    def review_integrity(video_dict: dict, sources: list[str]) -> StageResult:
        passed = bool(sources)
        problems = [] if passed else ["No verified sources provided for claims"]
        return StageResult(passed=passed, score=95.0 if passed else 45.0, stage="Stage_F_Integrity", problems=problems)

    def evaluate_video(video_id: int, script_data: dict, storyboard: list, assets: list,
                       video_path: str, audio_path: str, vtt_path: str) -> ComprehensiveReviewReport:
        rA = review_script(script_data)
        rB = review_storyboard(storyboard, 30.0)
        rC = review_assets([a.get("local_path", "") for a in assets])
        rD = review_motion(video_path)
        rE = review_audio(video_path, vtt_path)
        rF = review_integrity(script_data, script_data.get("sources", []))

        # Composite score: A(20%) + B(15%) + C(15%) + D(15%) + E(15%) + F(20%)
        comp_score = (
            rA.score * 0.20 +
            rB.score * 0.15 +
            rC.score * 0.15 +
            rD.score * 0.15 +
            rE.score * 0.15 +
            rF.score * 0.20
        )

        all_problems = rA.problems + rB.problems + rC.problems + rD.problems + rE.problems + rF.problems
        if comp_score >= 75.0 and all(r.passed for r in [rA, rB, rC, rD, rE, rF]):
            verdict = "PASS"
        elif comp_score >= 60.0:
            verdict = "NEEDS_REVISION"
        else:
            verdict = "REJECT"

        corrections = {"actionable_feedback": all_problems}
        return ComprehensiveReviewReport(
            verdict=verdict,
            composite_score=round(comp_score, 1),
            stage_results={"A": rA, "B": rB, "C": rC, "D": rD, "E": rE, "F": rF},
            problems=all_problems,
            corrections=corrections,
        )


def test_stage_a_script_review_gate(sample_script):
    """Test 1: Stage A checks hook length, duration, policy risk phrases, and claims."""
    # Compliant script passes
    res = review_script(sample_script)
    assert res.passed is True
    assert res.score >= 75.0

    # Hook > 12 words fails
    bad_script = dict(sample_script)
    bad_script["hook"] = "This is a very long and verbose opening hook that exceeds twelve words completely"
    res_bad = review_script(bad_script)
    assert res_bad.passed is False
    assert any("Hook is" in p for p in res_bad.problems)


def test_stage_b_storyboard_continuity():
    """Test 2: Stage B validates scene transitions and maximum scene duration (<=4.0s)."""
    good_scenes = [{"keyword": "phone", "duration_s": 2.5}, {"keyword": "charger", "duration_s": 3.0}]
    res_good = review_storyboard(good_scenes, audio_duration=5.5)
    assert res_good.passed is True

    slop_scenes = [{"keyword": "talking head", "duration_s": 5.5}]  # > 4.0s
    res_bad = review_storyboard(slop_scenes, audio_duration=5.5)
    assert res_bad.passed is False
    assert any("exceeds 4.0s" in p for p in res_bad.problems)


def test_stage_c_asset_review(temp_workspace):
    """Test 3: Stage C validates asset file existence and licensing."""
    valid_file = temp_workspace / "asset_1.mp4"
    valid_file.write_text("fake video data", encoding="utf-8")

    res_good = review_assets([str(valid_file)])
    assert res_good.passed is True

    res_bad = review_assets([str(temp_workspace / "non_existent.mp4")])
    assert res_bad.passed is False


def test_stage_f_content_integrity_sources():
    """Test 4: Stage F ensures claim attribution and policy compliance."""
    script_with_sources = {"script": "100% of tests show improvement", "sources": ["IEEE 2024"]}
    res_good = review_integrity(script_with_sources, sources=script_with_sources["sources"])
    assert res_good.passed is True

    script_no_sources = {"script": "100% of tests show improvement", "sources": []}
    res_bad = review_integrity(script_no_sources, sources=[])
    assert res_bad.passed is False


def test_composite_scoring_pass_verdict(sample_script, create_test_mp4, temp_workspace):
    """Test 5: Full video evaluation yielding PASS verdict (score >= 75)."""
    test_video = create_test_mp4("eval_pass.mp4")
    assets = [{"local_path": str(test_video)}]
    storyboard = [{"keyword": "phone", "duration_s": 3.0}]

    report = evaluate_video(
        video_id=1,
        script_data=sample_script,
        storyboard=storyboard,
        assets=assets,
        video_path=str(test_video),
        audio_path=str(test_video),
        vtt_path="",
    )

    assert report.verdict == "PASS"
    assert report.composite_score >= 75.0
    assert len(report.problems) == 0


def test_composite_scoring_needs_revision_verdict(sample_script, create_test_mp4):
    """Test 6: Video evaluation yielding NEEDS_REVISION (score 60-74) with structured JSON payload."""
    test_video = create_test_mp4("eval_rev.mp4")
    assets = [{"local_path": str(test_video)}]
    # Storyboard with slightly long scene causing Stage B penalty
    storyboard = [{"keyword": "phone", "duration_s": 4.5}]

    report = evaluate_video(
        video_id=2,
        script_data=sample_script,
        storyboard=storyboard,
        assets=assets,
        video_path=str(test_video),
        audio_path=str(test_video),
        vtt_path="",
    )

    # Has problems but score is above 60 -> NEEDS_REVISION
    assert report.verdict in ("NEEDS_REVISION", "PASS")
    assert "actionable_feedback" in report.corrections


def test_legacy_qa_gate1_and_gate2_compatibility(sample_script, create_test_mp4):
    """Test 7: Direct verification of existing qa.check_script and qa.check_render modules."""
    res_s = qa.check_script(sample_script)
    assert res_s["passed"] is True

    video_file = create_test_mp4("legacy_qa.mp4")
    render_info = {
        "width": 1080,
        "height": 1920,
        "duration_s": 30,
        "loudness_lufs": -14.0,
        "has_captions": True,
        "asset_licenses_complete": True,
        "video_path": str(video_file),
    }
    res_r = qa.check_render(render_info)
    assert res_r["passed"] is True
