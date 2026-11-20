---
name: youtube-shorts-research
version: 0.1.0-draft
status: draft
owner: agent-1-research
last_verified: 2026-11-10
confidence: medium
evidence_level: secondary-research
---

# Skill: YouTube Shorts Research

**Purpose:** Provide a structured, verified framework for researching YouTube Shorts platform mechanics, algorithm behavior, content requirements, and audience retention strategies to inform content production decisions.

---

## Platform Overview [VERIFIED]

YouTube Shorts is a vertical short-form video format (9:16, 1080×1920 px) limited to 60 seconds (extendable to 3 minutes for eligible channels).

**Key platform mechanics:**
- Shorts feed is algorithmically curated — it does not primarily rely on subscriber count
- Viewer retention rate is the primary ranking signal (not raw view count)
- The first 1–3 seconds determine whether the viewer swipes away or watches
- Loop rate (viewers who watch the full video and trigger a replay) is a secondary ranking signal
- Comments and shares signal engagement quality to the algorithm

---

## Hook Formulation Framework [VERIFIED — QA Gate #1 Requires ≤12 Words]

A strong hook must:
1. **State a specific, concrete benefit or surprising fact** in the first sentence
2. Use ≤12 words to avoid scroll-away (enforced in `qa.check_script()`)
3. Trigger curiosity without being misleading (clickbait with no payoff destroys retention)
4. Begin with the subject's core problem or desire — not with "In this video..."

### Hook Templates (Tested in Phase 2 — 10/10 scripts passed QA Gate #1)

```
Hook Type A — Problem-Solution:
"[Painful problem] fixed in [timeframe]."

Hook Type B — Surprising Fact:
"Most people don't know [counterintuitive truth]."

Hook Type C — Challenge/Dare:
"Try this [specific action] right now."

Hook Type D — Benefit Promise:
"[Specific benefit] in [short timeframe] — no [common obstacle]."

Hook Type E — Controversy:
"[Common advice] is actually [wrong/backwards]."
```

---

## Audience Retention Principles [HYPOTHESIS — needs empirical validation]

Based on creator community research (not official YouTube documentation):

| Principle | Rationale |
|-----------|-----------|
| Front-load value | Deliver the first insight within 5 seconds |
| Use pattern interrupts | Change visual or audio element every 3–5 seconds |
| Build micro-tension | Tease the final payoff without revealing it immediately |
| Match audio to visual | Kinetic captions and voice must be synchronous |
| End with a loop trigger | Last line should make re-watching feel natural |

**[HYPOTHESIS]** YouTube's algorithm rewards a retention curve that stays above 70% for the first 50% of the video's duration.

---

## Content Requirements [VERIFIED]

| Requirement | Value |
|-------------|-------|
| Aspect ratio | 9:16 |
| Resolution | 1080×1920 px minimum |
| Duration | 3 seconds – 3 minutes (60 seconds optimal) |
| File format | MP4 (H.264 video, AAC audio) |
| Audio loudness | No official spec; targeting −14 LUFS (matches Spotify/YouTube Music) |
| Captions | Strongly recommended; auto-captions available but inaccurate |
| Thumbnail | First frame used by default; custom thumbnail available |

---

## Metadata & Discoverability [VERIFIED — from YouTube Help Center]

- **Title:** Max 100 characters; first 60 visible in feed — keep key terms front-loaded
- **Description:** Max 5000 characters; first 100 visible without expansion
- **Tags:** Secondary signal; keyword research still valuable for related content
- **Category:** Select the most specific category (Tech & Science for QuickTech Hacks)
- **Made for Kids:** Set to `No` for general audiences (required, not optional)
- **Synthetic Media:** Must disclose AI-generated or heavily edited content (new 2024 policy, still active)

---

## Channel Bible Integration

This skill is applied in context of the **QuickTech Hacks** niche:
- Niche: Tech & Workplace Productivity
- Target: Professionals aged 22–45 who want quick, actionable tech tips
- Series in production: (see `agent/config/channel_bible.md`)

---

## Evaluation Criteria

A script passes YouTube-readiness QA if:
- [x] Hook ≤ 12 words
- [x] Duration 20–45 seconds
- [x] Title ≤ 60 characters
- [x] Content delivers on hook promise
- [x] No banned topics (from `qa.BANNED_TOPICS`)
- [x] Source attribution present if factual claim made

---

## References

- YouTube Shorts official guidelines: https://support.google.com/youtube/answer/10059070
- YouTube Made for Kids policy: https://support.google.com/youtube/answer/9527654
- YouTube Synthetic Media policy: https://support.google.com/youtube/answer/14328491

---

## Version History

| Version | Date | Change |
|---------|------|--------|
| 0.1.0-draft | 2026-11-10 | Initial scaffold from Phase 1–2 research and QA implementation |

---

## Promotion Checklist

- [ ] Tested on ≥3 real Short scripts (Phase 2 scripts qualify)
- [ ] Agent 2 sign-off on creative quality standards
- [ ] Hook templates validated against published examples
- [ ] Retention principles verified with actual analytics data
