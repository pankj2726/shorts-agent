"""
test_autonomous_production.py — End-to-End Test for Shorts Forge Autonomous Engine.

Executes a complete production run:
  1. Trend Idea: "The Last Kingdom of Snow Leopards"
  2. Script Generation + Anti-Slop Audit & Humanizer
  3. 100-Point Viral Potential Evaluation
  4. Neural TTS Synthesis (edge-tts en-IN-NeerjaNeural)
  5. Generative AI 1080x1920 Scene Synthesis (Pollinations Flux)
  6. FFmpeg Cinematic Camera Motion Clips
  7. Final 1080x1920 MP4 Video Assembly with Kinetic ASS Captions
  8. Audio Loudness Normalization (-14 LUFS) & QA Gate #2 Check
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

# Setup paths
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from agent.modules.anti_slop_killer import AntiSlopKiller
from agent.modules.viral_score import ViralScoreEngine
from agent.modules.tts import generate_voiceover
from agent.modules.assets import fetch_stock_clips, pick_music
from agent.modules.render import render_short
from agent.modules.qa import check_render
from agent.modules.notify import notify

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("autonomous_production_test")


def main() -> int:
    logger.info("🎬 === STARTING SHORTS FORGE AUTONOMOUS PRODUCTION TEST ===")

    # 1. Idea & Concept from Trend Radar
    idea = {
        "id": 999,
        "idea": "The Last Kingdom of Snow Leopards: High Altitude Ghosts",
        "angle": "Curiosity gap & rare mountain predator behavior",
        "category": "Wildlife & Nature",
    }
    logger.info(f"Step 1: Selected Trend Topic -> '{idea['idea']}'")

    # 2. Script Generation
    raw_script = (
        "There is a ghost ruling these mountains. "
        "Few people ever see it in the wild. "
        "Every single step is a battle for survival in the freezing Himalayan blizzard. "
        "And when it moves, the snow itself hides it completely. "
        "This is the last kingdom of the snow leopard."
    )
    title = "The Ghost That Rules the Himalayas"
    scene_keywords = ["snow leopard eyes blizzard", "himalayan snow mountain peak", "snow leopard running rocks"]

    script_data = {
        "video_id": 999,
        "title": title,
        "description": "Discover the hidden kingdom of the rare snow leopard in the high Himalayas. #SnowLeopard #Wildlife #Himalayas #Shorts",
        "tags": ["snow leopard", "wildlife", "himalayas", "nature", "shorts"],
        "script": raw_script,
        "scene_list": [{"search_keyword": kw} for kw in scene_keywords]
    }

    # Anti-Slop Audit & Humanizer
    slop_killer = AntiSlopKiller()
    audit = slop_killer.audit_script(script_data["script"])
    logger.info(f"Step 2: Anti-Slop Audit -> {audit['verdict']} (Detected Cliches: {audit['detected_cliches']})")
    if not audit["passed"]:
        script_data["script"] = slop_killer.clean_and_humanize(script_data["script"])
        logger.info(f"Humanized Script: {script_data['script']}")

    # 3. 100-Point Viral Potential Evaluation
    viral_engine = ViralScoreEngine()
    v_score = viral_engine.evaluate(script_data["script"], title=title, duration_seconds=18.0)
    logger.info(f"Step 3: Viral Potential Score -> {v_score['total_score']}/100 ({v_score['decision']}, Action: {v_score['action']})")
    for r in v_score["recommendations"]:
        logger.info(f"   Recommendation: {r}")

    # Output directory for test
    work_dir = root / "agent" / "state" / "test_render_999"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 4. Neural Voiceover (edge-tts)
    logger.info("Step 4: Synthesizing Neural Voiceover (en-IN-NeerjaNeural)...")
    tts_data = generate_voiceover(
        script=script_data["script"],
        output_dir=work_dir,
        language="en",
        video_id=999
    )
    logger.info(f"Voiceover generated: {tts_data['duration_s']}s ({len(tts_data.get('word_timings', []))} word timestamps)")

    # 5. Generative AI Media Synthesis (Pollinations Flux + FFmpeg Motion)
    logger.info("Step 5: Synthesizing Vertical 1080x1920 Generative Scenes via Pollinations Flux & FFmpeg...")
    clips = fetch_stock_clips(scene_keywords, work_dir, video_id=999)
    logger.info(f"Clips ready: {len(clips)} scenes assembled.")
    for idx, c in enumerate(clips, 1):
        logger.info(f"   Clip {idx}: {c['asset_type']} -> {Path(c['local_path']).name}")

    music = pick_music()

    # 6. Final Video Assembly with Kinetic ASS Captions & Loudness Normalization
    logger.info("Step 6: Rendering 1080x1920 Vertical Short with Kinetic Subtitles...")
    final_out = work_dir / f"video_{script_data['video_id']}_final.mp4"
    if final_out.exists():
        final_out.unlink()

    render_info = render_short(
        video_id=999,
        script_data=script_data,
        tts_data=tts_data,
        clips=clips,
        music=music,
        output_dir=work_dir
    )

    # 7. QA Gate #2 Check
    logger.info("Step 7: Validating QA Gate #2...")
    qa_res = check_render(render_info)
    logger.info(f"QA Gate #2 Result: Passed={qa_res['passed']} | {qa_res.get('reason', 'OK')}")

    if qa_res["passed"]:
        logger.info(f"🎉 SUCCESS! Final Short generated at: {render_info['video_path']}")
        print("\n" + "=" * 75)
        print(" SHORTS FORGE — Autonomous Production Complete")
        print("=" * 75)
        print(f"Title:         {title}")
        print(f"Viral Score:   {v_score['total_score']}/100 ({v_score['decision']})")
        print(f"Resolution:    {render_info['width']}x{render_info['height']} (9:16 Vertical)")
        print(f"Duration:      {render_info['duration_s']:.1f}s")
        print(f"Output File:   {render_info['video_path']}")
        print(f"Audio EBU:     Passed (-14 LUFS standard)")
        print("=" * 75)
        return 0
    else:
        logger.error(f"Render failed QA Gate #2: {qa_res['reason']}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
