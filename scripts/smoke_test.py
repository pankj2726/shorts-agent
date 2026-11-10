#!/usr/bin/env python3
"""
smoke_test.py — Phase 1 verification smoke test.

Verifies:
  1. Gemini LLM (via agent.modules.llm)
  2. Groq LLM fallback (if key provided)
  3. Pexels stock video API
  4. Pixabay API (if key provided)
  5. edge-tts neural voiceover generation
  6. ffmpeg installation & version
  7. YouTube Data API v3 read-only lookup
  8. Telegram bot status dispatch (PIPELINE_ONLINE)

Usage:
  python scripts/smoke_test.py
  python -m agent.main smoke
"""
import os
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.main import run_smoke


def main() -> None:
    print("=" * 65)
    print(" YouTube Shorts Agent — Integration Smoke Test (Phase 1)")
    print("=" * 65)
    run_smoke()


if __name__ == "__main__":
    main()
