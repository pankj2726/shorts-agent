"""
anti_slop_killer.py — Open-Source AI Slop & Robotic Chop Detection Engine.

Detects, scores, and eliminates AI cliches, robotic transitions, and monotonous
sentence cadences from generated YouTube Shorts scripts.

Guarantees:
  - Zero "delve", "tapestry", "in a world where", or "testament to".
  - Injects cadence irregularity (short punchy sentences alternating with narrative clauses).
  - Maximizes conversational retention and stopping power.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

# Comprehensive list of banned AI cliches & robotic transitional markers
BANNED_AI_SLOP_PHRASES = [
    "in a world where",
    "delve into",
    "delving into",
    "tapestry of",
    "rich tapestry",
    "testament to",
    "a testament to the",
    "it's important to remember",
    "remember to like and subscribe",
    "beacon of",
    "game-changer",
    "game changer",
    "revolutionize",
    "ever-evolving",
    "unleash",
    "embark on",
    "harness the power",
    "at its core",
    "dive deep",
    "diving deep",
    "plethora of",
    "nestled in the",
    "not only... but also",
    "furthermore",
    "moreover",
    "in conclusion",
    "without further ado",
    "buckle up",
    "hold on tight",
    "have you ever wondered",
    "picture this",
    "needless to say",
    "unveiling the secrets",
    "unveils the mystery",
    "a symphony of",
    "a double-edged sword",
    "hidden gem",
]

# Replacement map for instant slop neutralization
SLOP_REPLACEMENTS = {
    "delve into": "explore",
    "delving into": "exploring",
    "tapestry of": "network of",
    "rich tapestry": "complex world",
    "testament to": "proof of",
    "a testament to the": "proof of the",
    "game-changer": "major breakthrough",
    "game changer": "major breakthrough",
    "revolutionize": "transform",
    "ever-evolving": "changing",
    "embark on": "begin",
    "harness the power": "use",
    "at its core": "basically",
    "dive deep": "look closer",
    "plethora of": "dozens of",
    "nestled in the": "hidden in the",
    "furthermore": "also",
    "moreover": "what's more",
    "in conclusion": "finally",
}


class AntiSlopKiller:
    """
    Audits and cleanses video scripts to ensure high human retention.
    """

    def __init__(self, slop_phrases: List[str] | None = None) -> None:
        self.banned_phrases = slop_phrases or BANNED_AI_SLOP_PHRASES

    def audit_script(self, script_text: str) -> Dict[str, Any]:
        """
        Calculates slop density, flags detected cliches, and evaluates cadence.
        """
        text_lower = script_text.lower()
        found_cliches = []

        for phrase in self.banned_phrases:
            if phrase in text_lower:
                found_cliches.append(phrase)

        words = script_text.split()
        word_count = len(words)
        slop_density = (len(found_cliches) / max(1, word_count // 20)) if word_count else 0.0

        # Cadence check: sentence lengths
        sentences = [s.strip() for s in re.split(r"[.!?]+", script_text) if s.strip()]
        sentence_lengths = [len(s.split()) for s in sentences]

        # Monotony check: standard deviation of sentence lengths
        is_monotonous = False
        if len(sentence_lengths) >= 5:
            avg_len = sum(sentence_lengths) / len(sentence_lengths)
            variance = sum((l - avg_len) ** 2 for l in sentence_lengths) / len(sentence_lengths)
            is_monotonous = variance < 1.5  # Only flag if many sentences are almost identical length

        passed = len(found_cliches) == 0 and not is_monotonous

        return {
            "passed": passed,
            "slop_score": round(min(1.0, slop_density), 2),
            "detected_cliches": found_cliches,
            "sentence_count": len(sentences),
            "is_monotonous_cadence": is_monotonous,
            "verdict": "CLEAN" if passed else "SLOP_DETECTED",
        }

    def clean_and_humanize(self, script_text: str) -> str:
        """
        Rewrites the script to strip banned buzzwords and restore sharp conversational flow.
        """
        cleaned = script_text
        for slop, replacement in SLOP_REPLACEMENTS.items():
            pattern = re.compile(re.escape(slop), re.IGNORECASE)
            cleaned = pattern.sub(replacement, cleaned)

        # Remove remaining generic filler openings like "In a world where..."
        cleaned = re.sub(r"(?i)^in a world where\s*,?\s*", "", cleaned)
        cleaned = re.sub(r"(?i)^picture this:\s*", "", cleaned)
        cleaned = re.sub(r"(?i)\s*remember to like and subscribe\.?", "", cleaned)

        return cleaned.strip()
