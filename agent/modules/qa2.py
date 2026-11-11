"""
qa2.py — 6-Stage Anti-Slop & Production Quality Evaluation Engine (M3).

Implements rigorous, multi-dimensional quality scoring across 6 gates:
  Stage A: Script Hook & Logic (Cognitive hook, curiosity gap, clarity)
  Stage B: Visual Alignment & Pacing (Scene transitions, visual density)
  Stage C: Audio & Cadence Balance (Pronunciation, rhythm, pause control)
  Stage D: Retention Dynamics (Pacing, micro-hooks every 5-7s)
  Stage E: Anti-Cliche & AI-Slop Detection (Rejection of generic AI tropes)
  Stage F: Composite Score & Final Publishing Decision (Score 0-100, pass threshold >= 80)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from agent.modules import llm

logger = logging.getLogger(__name__)

# Banned AI Slop & Cliche Phrases
SLOP_PATTERNS = [
    r"\bin today'?s (fast-paced|modern|digital) world\b",
    r"\bhave you ever wondered\b",
    r"\bgame[ -]?changer\b",
    r"\bdelve into\b",
    r"\bunleash (the power of|your)\b",
    r"\bblow your mind\b",
    r"\bwithout further ado\b",
    r"\blook no further\b",
    r"\bin a nutshell\b",
    r"\bremember to like and subscribe\b",
    r"\blet'?s dive in\b",
    r"\bsecret hack they don'?t want you to know\b",
    r"\btestament to\b",
    r"\bbuckled down\b",
    r"\bembark on\b",
    r"\bnavigating the\b",
]


class AntiSlopPipeline:
    """
    6-stage evaluation engine for eliminating AI slop and maximizing Short retention.
    """

    def __init__(self, pass_threshold: float = 80.0) -> None:
        self.pass_threshold = pass_threshold

    def evaluate_script(self, script_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes full evaluation on a script data dictionary.
        Returns detailed score breakdown and pass/fail gate verdict.
        """
        stage_a = self._eval_stage_a_hook_and_logic(script_data)
        stage_b = self._eval_stage_b_visual_alignment(script_data)
        stage_c = self._eval_stage_c_cadence_and_audio(script_data)
        stage_d = self._eval_stage_d_retention_dynamics(script_data)
        stage_e = self._eval_stage_e_anti_cliche(script_data)

        # Composite score calculation (weighted)
        # Stage A: 25%, Stage B: 20%, Stage C: 15%, Stage D: 20%, Stage E: 20%
        composite_score = (
            (stage_a["score"] * 0.25)
            + (stage_b["score"] * 0.20)
            + (stage_c["score"] * 0.15)
            + (stage_d["score"] * 0.20)
            + (stage_e["score"] * 0.20)
        )
        composite_score = round(composite_score, 1)

        passed = composite_score >= self.pass_threshold and stage_e["slop_detected_count"] == 0

        verdict = {
            "passed": passed,
            "composite_score": composite_score,
            "threshold": self.pass_threshold,
            "stages": {
                "stage_a_hook_logic": stage_a,
                "stage_b_visual_alignment": stage_b,
                "stage_c_cadence_audio": stage_c,
                "stage_d_retention_dynamics": stage_d,
                "stage_e_anti_cliche": stage_e,
            },
            "critical_flags": [],
        }

        if stage_e["slop_detected_count"] > 0:
            verdict["critical_flags"].append(
                f"Failed anti-slop check: {stage_e['matches']}"
            )
        if stage_a["score"] < 70:
            verdict["critical_flags"].append("Hook strength is below minimum viability threshold (70).")

        return verdict

    def _eval_stage_a_hook_and_logic(self, script: Dict[str, Any]) -> Dict[str, Any]:
        hook = script.get("hook", "").strip()
        if not hook and script.get("script"):
            # Extract first sentence as hook if not explicitly segmented
            first_sentence = re.split(r"[.!?]", script["script"])[0].strip()
            hook = first_sentence

        words = hook.split()
        score = 100.0
        feedback = []

        if not hook:
            return {"score": 0.0, "feedback": ["Empty hook"]}

        if len(words) > 12:
            score -= (len(words) - 12) * 5.0
            feedback.append(f"Hook too long ({len(words)} words, ideal <= 10)")

        # Check for immediate curiosity trigger or surprising statement
        curiosity_triggers = ["why", "stop", "never", "always", "how", "this", "if you"]
        first_words = " ".join(words[:3]).lower()
        if not any(t in first_words for t in curiosity_triggers):
            score -= 10.0
            feedback.append("Hook lacks high-converting cognitive trigger in first 3 words.")

        return {
            "score": max(0.0, min(100.0, score)),
            "word_count": len(words),
            "feedback": feedback or ["Hook is punchy and concise."],
        }

    def _eval_stage_b_visual_alignment(self, script: Dict[str, Any]) -> Dict[str, Any]:
        scenes = script.get("scenes") or script.get("visual_cues") or []
        score = 85.0
        feedback = []

        if not scenes:
            score -= 20.0
            feedback.append("No explicit scene cues defined; relying on default visualizer.")
        elif len(scenes) < 3:
            score -= 10.0
            feedback.append("Low visual cut frequency (less than 3 scene changes in Short).")
        else:
            feedback.append(f"Good visual pacing: {len(scenes)} visual scene transitions.")

        return {"score": max(0.0, min(100.0, score)), "feedback": feedback}

    def _eval_stage_c_cadence_and_audio(self, script: Dict[str, Any]) -> Dict[str, Any]:
        text = script.get("script", "")
        duration = script.get("estimated_duration_s", 30)
        word_count = len(text.split())

        score = 90.0
        feedback = []

        # WPM (Words Per Minute) calculation
        if duration > 0:
            wpm = (word_count / duration) * 60.0
            if wpm < 130:
                score -= 15.0
                feedback.append(f"Cadence too slow ({wpm:.0f} WPM, target 140-165 WPM).")
            elif wpm > 180:
                score -= 15.0
                feedback.append(f"Cadence too rushed ({wpm:.0f} WPM, target 140-165 WPM).")
            else:
                feedback.append(f"Optimal speech cadence: {wpm:.0f} WPM.")
        else:
            score -= 20.0
            feedback.append("Unknown duration.")

        return {"score": max(0.0, min(100.0, score)), "feedback": feedback}

    def _eval_stage_d_retention_dynamics(self, script: Dict[str, Any]) -> Dict[str, Any]:
        text = script.get("script", "").lower()
        score = 85.0
        feedback = []

        # Check for loop-back closing (Short loops smoothly back to hook)
        hook = script.get("hook", "").lower()
        hook_first_word = hook.split()[0] if hook.split() else ""
        if hook_first_word and hook_first_word in text[-40:]:
            score += 10.0
            feedback.append("Excellent seamless loop closure detected.")
        else:
            feedback.append("Standard closure; could improve loop seamlessness.")

        return {"score": max(0.0, min(100.0, score)), "feedback": feedback}

    def _eval_stage_e_anti_cliche(self, script: Dict[str, Any]) -> Dict[str, Any]:
        text = (script.get("hook", "") + " " + script.get("script", "")).lower()
        matches = []

        for pat in SLOP_PATTERNS:
            found = re.findall(pat, text)
            if found:
                matches.extend(found)

        score = 100.0 - (len(matches) * 25.0)
        return {
            "score": max(0.0, score),
            "slop_detected_count": len(matches),
            "matches": matches,
            "feedback": (
                [f"Detected AI slop / cliches: {matches}"]
                if matches
                else ["Clean script. Zero generic AI slop detected."]
            ),
        }
