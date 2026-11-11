"""
test_viral_score.py — Unit tests for the 100-point viral potential scoring engine.
"""

from agent.modules.viral_score import ViralScoreEngine


def test_viral_score_evaluation_exceptional():
    engine = ViralScoreEngine()
    # High score override
    override = {cat: max_pts for cat, max_pts in engine.CATEGORIES.items()}
    res = engine.evaluate("test script", "test title", scores_override=override)
    assert res["total_score"] == 100.0
    assert res["decision"] == "EXCEPTIONAL"
    assert res["action"] == "READY_FOR_FINAL_QA"


def test_viral_score_evaluation_heuristic():
    engine = ViralScoreEngine()
    script = (
        "There is a ghost ruling these mountains. "
        "Few have ever seen it in the wild. "
        "Every step is a battle for survival. "
        "And when it moves, the mountain disappears. "
        "This is the last kingdom of the snow leopard."
    )
    title = "The Ghost That Rules the Himalayas"
    res = engine.evaluate(script, title, duration_seconds=30.0)

    assert 75.0 <= res["total_score"] <= 95.0
    assert "breakdown" in res
    assert len(res["recommendations"]) > 0


def test_viral_score_low_threshold_revision():
    engine = ViralScoreEngine()
    override = {cat: 5.0 for cat in engine.CATEGORIES.keys()}  # 8 * 5 = 40 pts
    res = engine.evaluate("poor script", "bad title", scores_override=override)
    assert res["total_score"] == 40.0
    assert res["decision"] == "RECONSIDER"
    assert res["action"] == "REGENERATE_CONCEPT"
