"""
test_qa2.py — Test suite for 6-Stage Anti-Slop Engine (M3).
"""

import pytest
from agent.modules.qa2 import AntiSlopPipeline


def test_clean_high_quality_script_passes():
    pipeline = AntiSlopPipeline(pass_threshold=80.0)
    script = {
        "title": "Why Your Phone Battery Dies So Fast",
        "hook": "Stop charging your phone to 100 percent.",
        "script": "Stop charging your phone to 100 percent. Lithium-ion batteries degrade twice as fast when kept under high voltage. Instead, keep your charge between 20 and 80 percent to double your battery lifespan. That is why...",
        "estimated_duration_s": 28,
        "scenes": ["battery animation", "voltage graph", "lifespan comparison"],
    }
    result = pipeline.evaluate_script(script)
    assert result["passed"] is True
    assert result["composite_score"] >= 80.0
    assert result["stages"]["stage_e_anti_cliche"]["slop_detected_count"] == 0


def test_ai_slop_cliche_detection_fails():
    pipeline = AntiSlopPipeline(pass_threshold=80.0)
    script = {
        "title": "AI in modern life",
        "hook": "In today's fast-paced world, AI is a game-changer.",
        "script": "In today's fast-paced world, have you ever wondered how to delve into AI? Without further ado, this secret hack they don't want you to know will blow your mind.",
        "estimated_duration_s": 30,
        "scenes": ["generic ai robot"],
    }
    result = pipeline.evaluate_script(script)
    assert result["passed"] is False
    assert result["stages"]["stage_e_anti_cliche"]["slop_detected_count"] >= 3
    assert len(result["critical_flags"]) > 0
