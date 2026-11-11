"""
run_trend_radar.py — CLI tool to run the Trend Radar & Chaos Detector.

Scans trending opportunities, computes opportunity scores, and optionally
delivers the morning digest to Telegram.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add project root to sys.path
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from agent.modules.trend_radar import TrendRadar
from agent.modules.notify import notify

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trend_radar_runner")


def main() -> int:
    parser = argparse.ArgumentParser(description="Shorts Forge — Daily Trend Radar")
    parser.add_argument("--niche", type=str, default="Technology & Nature", help="Target niche")
    parser.add_argument("--telegram", action="store_true", help="Send formatted digest to Telegram")
    args = parser.parse_args()

    radar = TrendRadar()
    logger.info(f"Scanning Trend Radar & Chaos Signals for niche: {args.niche}...")
    trends = radar.scan_trends(niche=args.niche)

    print("\n" + "=" * 75)
    print(" SHORTS FORGE — Trend Radar & Chaos Signals")
    print("=" * 75)
    for idx, t in enumerate(trends, 1):
        chaos_tag = "[CHAOS BREAKOUT]" if t["chaos_breakout"] else "[TREND]"
        print(f"{idx}. {t['topic']}")
        print(f"   Score: {t['opportunity_score']}/100 {chaos_tag} | Category: {t['category']}")
        print(f"   Window: {t['opportunity_window']} | Demand: {t['search_demand']} | Growth: {t['growth_rate']}%")
        print(f"   Insight: {t['reason']}\n")

    if args.telegram:
        digest_text = radar.format_telegram_digest(trends)
        logger.info("Delivering Trend Digest to Telegram...")
        try:
            notify(event="TREND_RADAR_DIGEST", text=digest_text, level="info")
            logger.info("Trend digest delivered to Telegram successfully!")
        except Exception as e:
            logger.error(f"Failed to send to Telegram: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
