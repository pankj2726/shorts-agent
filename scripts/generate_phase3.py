#!/usr/bin/env python3
"""
generate_phase3.py — Phase 3 Video Assembly & Rendering:
  1. Loads Video #1 from SQLite (QA Gate #1 approved in Phase 2).
  2. Synthesizes high-fidelity voiceover with edge-tts (MP3 + word-timed VTT).
  3. Synthesizes 9:16 portrait visuals using Pollinations AI motion generator.
  4. Renders 1080x1920 MP4 with kinetic ASS subtitles and -14 LUFS loudness.
  5. Verifies through QA Gate #2 (qa.check_render).
  6. Updates SQLite videos record (render_path, render_qa_ok).
  7. Delivers notification + video preview to Telegram.
"""
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.modules import assets, db, notify, qa, render, tts

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("phase3")


def main() -> None:
    print("=" * 70)
    print(" YouTube Shorts Agent — Phase 3 Video Assembly & Rendering Engine")
    print("=" * 70)

    # 1. Fetch Video #1 from SQLite
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM videos WHERE script_qa_ok = 1 ORDER BY id ASC LIMIT 1").fetchone()
    conn.close()

    if not row:
        print("❌ No script found in videos table with script_qa_ok = 1.")
        sys.exit(1)

    video = dict(row)
    video_id = video["id"]
    title = video["title"]
    script_text = video["script"]

    print(f"\n[1/5] Selected Video ID {video_id}:")
    print(f"  Title:  {title}")
    print(f"  Script: {script_text[:120]}...\n")

    # Work directory for artifacts
    work_dir = PROJECT_ROOT / "temp" / f"short_{video_id}"
    work_dir.mkdir(parents=True, exist_ok=True)
    # Clean previous run artifacts
    for old_f in work_dir.glob(f"video_{video_id}_*"):
        try:
            old_f.unlink()
        except Exception:
            pass

    # 2. Generate Voiceover + Word-Timed Captions
    print("[2/5] Synthesizing Neural Voiceover (edge-tts)...")
    tts_data = tts.generate_voiceover(
        script=script_text,
        output_dir=work_dir,
        language="en",
        video_id=video_id,
    )
    duration_s = tts_data["duration_s"]
    word_count = len(tts_data.get("word_timings", []))
    print(f"  ✅ Voiceover generated: {tts_data['audio_path']}")
    print(f"     Duration: {duration_s:.1f}s | Word Timings captured: {word_count} words")

    # 3. Fetch / Synthesize Visuals
    print("\n[3/5] Generating Visual Scene Motion Clips (Zero-Key AI / Procedural)...")
    keywords = [
        "file preview windows computer",
        "keyboard shortcut control space",
        "modern computer desktop documents",
        "speed productivity office setup",
    ]
    clips = assets.fetch_stock_clips(
        keywords=keywords,
        output_dir=work_dir,
        video_id=video_id,
    )
    print(f"  ✅ Prepared {len(clips)} visual motion clips:")
    for idx, c in enumerate(clips, 1):
        print(f"     [{idx}] {c['asset_type']}: {Path(c['local_path']).name} ({c['license'][:30]}...)")

    music = assets.pick_music(work_dir)

    # 4. Render Composite Short Video
    print(f"\n[4/5] Rendering 1080x1920 Short via ffmpeg with Kinetic Captions & −14 LUFS...")
    # Delete old final if re-running
    final_target = work_dir / f"video_{video_id}_final.mp4"
    if final_target.exists():
        final_target.unlink()

    render_info = render.render_short(
        video_id=video_id,
        script_data=video,
        tts_data=tts_data,
        clips=clips,
        music=music,
        output_dir=work_dir,
    )
    print(f"  ✅ Render complete: {render_info['video_path']}")
    print(f"     Resolution: {render_info['width']}x{render_info['height']}")
    print(f"     Duration:   {render_info['duration_s']:.1f}s")
    print(f"     Loudness:   {render_info['loudness_lufs']:.1f} LUFS")

    # 5. QA Gate #2 Verification
    print("\n[5/5] Running QA Gate #2 Verification (check_render)...")
    qa_result = qa.check_render(render_info)
    if not qa_result["passed"]:
        print(f"  ❌ QA Gate #2 Failed: {qa_result['reason']}")
        sys.exit(1)

    print(f"  ✅ QA Gate #2 PASSED: 1080x1920, {render_info['duration_s']:.1f}s, {render_info['loudness_lufs']:.1f} LUFS, Subtitles intact.")

    # Update database
    conn = db.get_conn()
    conn.execute(
        "UPDATE videos SET render_path = ?, render_qa_ok = 1 WHERE id = ?",
        (render_info["video_path"], video_id),
    )
    conn.commit()
    conn.close()
    print("  ✅ Database updated: render_path set and render_qa_ok = 1.")

    # 6. Telegram notification
    print("\nSending Telegram Preview Video...")
    notify.notify(
        event="PHASE3_RENDER_VERIFIED",
        text=f"🎬 Short #{video_id} Rendered & QA #2 Verified!\n\n"
             f"📌 Title: {title}\n"
             f"⏱ Duration: {render_info['duration_s']:.1f}s\n"
             f"🔊 Loudness: {render_info['loudness_lufs']:.1f} LUFS\n"
             f"🎨 Resolution: 1080x1920 (9:16 portrait)",
        level="success",
        video_path=render_info["video_path"],
    )
    print("  ✅ Telegram notification delivered.")

    print("\n" + "=" * 70)
    print("🎉 PHASE 3 SUCCESS: Video Assembly & Rendering 100% Operational!")
    print("=" * 70)


if __name__ == "__main__":
    main()
