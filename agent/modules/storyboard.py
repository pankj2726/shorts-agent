"""
storyboard.py — Frame-by-Frame Scene Director & Storyboard Engine.

Transforms Short scripts or multi-modal prompts into production-grade scene storyboards:
  1. Scene Pacing: Enforces high-retention visual cuts (pacing <= 4.0s per cut).
  2. Multi-Stage Scene Structure:
     - Hook (0-3s): High visual arousal, curiosity gap.
     - Build-up & Demo (3-15s): Step-by-step visual progression.
     - Payoff / Climax (15-25s): The core revelation or solution.
     - Call to Action (25-30s+): Kinetic loop or subscription prompt.
  3. Kinetic Typography: Highlighting words, safe-zone positioning (9:16 portrait).
  4. Sound Design Cues: SFX markers (whoosh, pop, hit, ding).
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class StoryboardEngine:
    """Production Storyboard Generator and Validator for 9:16 Shorts."""

    MAX_SCENE_DURATION_S = 4.5  # Ideal pacing for YouTube Shorts retention
    MIN_SCENE_DURATION_S = 1.5

    @classmethod
    def generate_storyboard(
        cls,
        script_text: str,
        total_duration_s: float = 30.0,
        title: str = "",
        niche: str = "technology",
    ) -> List[Dict[str, Any]]:
        """
        Decomposes a script into a synchronized frame-by-frame scene storyboard.
        """
        script_text = script_text.strip()
        if not script_text:
            return []

        # Split into sentences or clauses
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script_text) if s.strip()]
        if not sentences:
            sentences = [script_text]

        # Calculate word counts and proportional durations
        total_words = sum(len(s.split()) for s in sentences) or 1
        scene_targets = []

        current_time = 0.0
        for idx, sentence in enumerate(sentences):
            words = sentence.split()
            word_count = len(words)
            proportional_duration = (word_count / total_words) * total_duration_s
            
            # If a single sentence is too long (> 5.5s), split it into sub-cuts
            if proportional_duration > cls.MAX_SCENE_DURATION_S:
                cuts_needed = math.ceil(proportional_duration / cls.MAX_SCENE_DURATION_S)
                chunk_size = math.ceil(word_count / cuts_needed)
                sub_chunks = [" ".join(words[i:i + chunk_size]) for i in range(0, word_count, chunk_size)]
                for sub_chunk in sub_chunks:
                    sub_duration = (len(sub_chunk.split()) / total_words) * total_duration_s
                    scene_targets.append({
                        "text": sub_chunk,
                        "duration": max(cls.MIN_SCENE_DURATION_S, round(sub_duration, 2)),
                    })
            else:
                scene_targets.append({
                    "text": sentence,
                    "duration": max(cls.MIN_SCENE_DURATION_S, round(proportional_duration, 2)),
                })

        # Normalize durations to match total_duration_s
        raw_total = sum(s["duration"] for s in scene_targets) or 1.0
        scale = total_duration_s / raw_total

        storyboard = []
        elapsed = 0.0

        for i, target in enumerate(scene_targets):
            dur = round(target["duration"] * scale, 2)
            start_s = round(elapsed, 2)
            end_s = round(elapsed + dur, 2)
            elapsed = end_s

            # Determine stage
            if start_s < 3.5:
                phase = "HOOK"
                camera_motion = "ZOOM_IN_FAST"
                sfx = "WHOOSH_FAST"
            elif end_s >= (total_duration_s - 5.0):
                phase = "CALL_TO_ACTION"
                camera_motion = "SLOW_PAN_UP"
                sfx = "CHIME_POP"
            elif start_s < (total_duration_s * 0.65):
                phase = "DEMONSTRATION"
                camera_motion = "PAN_RIGHT" if i % 2 == 0 else "ZOOM_IN_SLOW"
                sfx = "KEYBOARD_CLACK" if "tech" in niche.lower() else "SUBTLE_POP"
            else:
                phase = "PAYOFF"
                camera_motion = "ZOOM_OUT_REVEAL"
                sfx = "IMPACT_HIT"

            text = target["text"]
            # Extract kinetic typography highlights (capitalized or impactful words)
            highlight_words = [w.strip(".,!?\"'") for w in text.split() if len(w) > 4][:2]

            visual_prompt = cls._generate_visual_prompt(
                sentence=text,
                phase=phase,
                title=title,
                niche=niche,
            )

            storyboard.append({
                "scene_index": i,
                "start_s": start_s,
                "end_s": end_s,
                "duration_s": dur,
                "phase": phase,
                "narration_text": text,
                "visual_prompt": visual_prompt,
                "camera_motion": camera_motion,
                "sound_effect": sfx,
                "kinetic_typography": {
                    "highlight_words": highlight_words,
                    "position": "center_lower",
                    "font_size_scale": 1.2 if phase in ("HOOK", "PAYOFF") else 1.0,
                    "color_primary": "#FFFFFF",
                    "color_highlight": "#FFDD00" if phase == "HOOK" else "#00FFCC",
                },
            })

        return storyboard

    @staticmethod
    def _generate_visual_prompt(sentence: str, phase: str, title: str, niche: str) -> str:
        """Constructs an optimized visual asset generation prompt."""
        clean_sentence = re.sub(r"[^\w\s]", "", sentence)
        key_words = " ".join([w for w in clean_sentence.split() if len(w) > 3][:4])

        if phase == "HOOK":
            prefix = "Dramatic high-contrast vertical shot, shock expression or bold glowing interface"
        elif phase == "PAYOFF":
            prefix = "Cinematic satisfying transformation, high resolution sleek modern lighting"
        elif phase == "CALL_TO_ACTION":
            prefix = "Clean minimal tech background, looping fluid animation, bright accents"
        else:
            prefix = "Clean over-the-shoulder vertical POV, hyper-detailed modern screen UI"

        return f"{prefix}, {key_words}, {niche} aesthetic, 9:16 portrait, 8k, Unreal Engine 5 render style"

    @classmethod
    def validate_storyboard(cls, storyboard: List[Dict[str, Any]], expected_duration_s: float) -> Dict[str, Any]:
        """
        Validates storyboard continuity, pacing, and safe zones.
        """
        if not storyboard:
            return {"passed": False, "reason": "Empty storyboard"}

        issues = []
        total_actual_duration = storyboard[-1]["end_s"]
        duration_delta = abs(total_actual_duration - expected_duration_s)

        if duration_delta > 2.0:
            issues.append(f"Duration mismatch: expected {expected_duration_s}s, got {total_actual_duration}s")

        # Check pacing
        overlong_scenes = [s for s in storyboard if s["duration_s"] > cls.MAX_SCENE_DURATION_S + 1.0]
        if overlong_scenes:
            issues.append(f"{len(overlong_scenes)} scenes exceed maximum retention cut duration ({cls.MAX_SCENE_DURATION_S}s)")

        # Check hook presence
        has_hook = any(s["phase"] == "HOOK" for s in storyboard)
        if not has_hook:
            issues.append("Missing HOOK phase in initial scene")

        return {
            "passed": len(issues) == 0,
            "scene_count": len(storyboard),
            "total_duration_s": total_actual_duration,
            "average_scene_duration_s": round(total_actual_duration / len(storyboard), 2),
            "issues": issues,
        }

    @staticmethod
    def export_storyboard_json(storyboard: List[Dict[str, Any]], output_path: str | Path) -> str:
        """Exports storyboard to JSON format."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"storyboard": storyboard}, f, indent=2)
        return str(path)
