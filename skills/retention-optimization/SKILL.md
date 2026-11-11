---
name: retention-optimization
version: 0.2.0-beta
status: beta
owner: agent-2-scriptwriter
last_verified: 2026-11-11
confidence: high
evidence_level: platform-tested
---

# Skill: YouTube Shorts Retention Optimization

**Purpose:** Provide actionable, data-backed heuristics and script-pacing structures to maximize viewer retention (>85%), average percentage viewed (APV > 100%), and loop replays on YouTube Shorts.

---

## 1. The 3-Second Retention Cliff

Data across top-performing tech Shorts shows that 60–70% of audience drop-off occurs between seconds 0 and 3.

### Core Rules:
1. **Zero Title Card / Zero Logos:** Never start with an intro screen, channel name, or greeting ("Hey guys!").
2. **First-Frame Visual Anchor:** Display large kinetic captions and a visually distinct UI action on frame 1.
3. **Word Cadence:** Deliver 140–165 WPM. No dead air pauses > 0.4 seconds.

---

## 2. Micro-Hooks & Open Loops (Every 5–7 Seconds)

Never let cognitive tension drop before the final payoff:
- **Second 0–3 (Initial Hook):** State the painful problem or counter-intuitive claim.
- **Second 7–9 (Micro-Hook 1):** "Most people do this instead, which breaks..."
- **Second 14–17 (Micro-Hook 2):** "Here is the one setting that changes everything..."
- **Second 22–26 (Payoff):** The exact keyboard shortcut, command, or solution.
- **Second 27–30 (Seamless Loop):** The final sentence must syntactically or conceptually flow directly back into the opening hook sentence.

---

## 3. Kinetic Caption Engineering
- **Placement:** Center-third of 1080×1920 vertical canvas (avoid YouTube bottom overlay and right sidebar icons).
- **Word Chunking:** 1–3 words per frame with active highlight color (#FFD700 / Yellow or Cyan).
- **Audio Synchronization:** Zero audio-video caption drift (enforced via word-level timestamps in `captions.py`).

---

## 4. Anti-Dropoff Verification Checklist (QA Gate #2)
- [ ] First sentence <= 10 words
- [ ] No filler phrases ("In today's video", "Make sure to subscribe")
- [ ] Pacing tested against EBU R128 (-14 LUFS)
- [ ] Video ends on active peak, not slow fade-out
