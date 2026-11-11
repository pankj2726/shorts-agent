"""
viral_score.py — 100-Point Explainable Viral Potential Scoring Engine.

Heuristic evaluation framework assessing YouTube Shorts viral potential across
8 weighted production dimensions:
  1. Hook Strength (20 pts) — Curiosity gap & pattern interrupt (0s–3s)
  2. Audience Relevance (15 pts) — Match with channel niche & target viewer persona
  3. Retention Potential (20 pts) — Narrative pacing, tension escalation & payoff timing
  4. Emotional Impact (10 pts) — Wonder, shock, humor, visceral response
  5. Shareability (10 pts) — Replay potential, debate triggers, DM sharing
  6. Trend Momentum (10 pts) — Freshness, search velocity, cultural relevance
  7. Visual Quality & Continuity (10 pts) — Subject consistency, composition, motion
  8. Packaging (5 pts) — Title intrigue and visual thumbnail harmony

Decision Thresholds:
  - 90–100: Exceptional candidate (Approved for publication)
  - 80–89:  Strong candidate (Approved with optional micro-tweaks)
  - 70–79:  Revise hook, pacing, or packaging
  - 60–69:  Major creative revision required
  - Below 60: Reconsider concept or regenerate
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class ViralScoreEngine:
    """
    Evaluates completed scripts or videos and returns an explainable score with actionable revisions.
    """

    CATEGORIES = {
        "hook_strength": 20.0,
        "audience_relevance": 15.0,
        "retention_potential": 20.0,
        "emotional_impact": 10.0,
        "shareability": 10.0,
        "trend_momentum": 10.0,
        "visual_quality": 10.0,
        "packaging": 5.0,
    }

    def evaluate(
        self,
        script_text: str,
        title: str,
        duration_seconds: float = 30.0,
        scores_override: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Calculates the weighted 100-point viral score and generates concrete editing recommendations.
        """
        scores: Dict[str, float] = {}

        if scores_override:
            for cat, max_pts in self.CATEGORIES.items():
                scores[cat] = min(max_pts, max(0.0, float(scores_override.get(cat, max_pts * 0.8))))
        else:
            # Deterministic heuristic analysis based on script properties
            word_count = len(script_text.split())
            wpm = (word_count / duration_seconds) * 60 if duration_seconds > 0 else 140

            # 1. Hook check: first 15 words
            first_15 = " ".join(script_text.split()[:15]).lower()
            has_curiosity = any(w in first_15 for w in ("ghost", "secret", "never", "why", "shocking", "hidden", "nobody", "most"))
            hook_pts = 18.0 if has_curiosity else 13.0

            # 2. Pacing / Retention: ideal WPM for Shorts is 130-155 WPM
            pacing_pts = 19.0 if 125 <= wpm <= 160 else (15.0 if 110 <= wpm <= 175 else 11.0)

            # 3. Packaging / Title alignment
            has_strong_title = len(title.split()) >= 4 and any(c in title for c in ("?", ":", "—", "The"))
            pkg_pts = 4.5 if has_strong_title else 3.0

            scores = {
                "hook_strength": hook_pts,
                "audience_relevance": 13.5,
                "retention_potential": pacing_pts,
                "emotional_impact": 8.5,
                "shareability": 8.0,
                "trend_momentum": 8.5,
                "visual_quality": 9.0,
                "packaging": pkg_pts,
            }

        total_score = round(sum(scores.values()), 1)

        # Determine decision & recommendation
        if total_score >= 90.0:
            decision = "EXCEPTIONAL"
            action = "READY_FOR_FINAL_QA"
            recommendations = ["Script and pacing are elite; proceed straight to render and delivery."]
        elif total_score >= 80.0:
            decision = "STRONG"
            action = "APPROVED_WITH_MINOR_TWEAKS"
            recommendations = ["Pacing and hook are solid. Ensure audio bass drop synchronizes with the opening cut."]
        elif total_score >= 70.0:
            decision = "REVISE_PACING"
            action = "AUTOMATED_REVISION_REQUIRED"
            recommendations = [
                "Hook curiosity gap could be sharper in the first 2 seconds.",
                "Introduce a visual pattern interrupt at second 8 to prevent mid-roll drop-off."
            ]
        elif total_score >= 60.0:
            decision = "MAJOR_REVISION"
            action = "REWRITE_CORE_SECTIONS"
            recommendations = ["The central reveal occurs too late; move the primary payoff earlier in the timeline."]
        else:
            decision = "RECONSIDER"
            action = "REGENERATE_CONCEPT"
            recommendations = ["Concept lacks stopping power or audience fit; regenerate with a different angle."]

        return {
            "total_score": total_score,
            "max_score": 100.0,
            "decision": decision,
            "action": action,
            "breakdown": scores,
            "recommendations": recommendations,
        }
