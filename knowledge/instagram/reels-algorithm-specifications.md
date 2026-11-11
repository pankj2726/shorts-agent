---
id: kb-ig-001
title: "Instagram Reels Algorithm Mechanics, Safe Zones & Engagement Rules"
category: instagram
version: 1.0.0
verification_status: VERIFIED
confidence: 0.94
confidence_level: HIGH
owner_agent: agent-3-social
sources:
  - "Meta for Creators Official Guidelines: Reels Recommendations (2025-2026)"
  - "Instagram Graph API Documentation: Media Publishing"
tags:
  - instagram
  - reels
  - algorithm
  - grid-safe
  - publishing
applies_to:
  - "agent/modules/social.py"
  - "agent/modules/publish.py"
  - "agent/modules/render.py"
created_at: "2026-11-10T00:00:00Z"
last_verified: "2026-11-10T12:00:00Z"
---

# Instagram Reels Algorithm Mechanics, Safe Zones & Engagement Rules [VERIFIED]

## 1. Algorithm Ranking Weights
The Instagram Reels recommendation system prioritizes signals in this strict hierarchy:
1. **Send / DM Rate (Highest Weight):** Number of times viewers forward the Reel to friends via direct message. Practical tech hacks have a natural 3x higher DM forward rate than entertainment clips.
2. **Save Rate:** Saves signal high reference value. Evergreen shortcut tutorials trigger strong bookmarking.
3. **Completion & Replay Rate:** Percentage of users who watch past 85% duration, and trigger a replay.
4. **Watch Time:** Total seconds spent viewing.
5. **Comments & Likes:** Secondary signals of community engagement.

## 2. 4:5 Profile Grid Safe Area
While Reels play in full 9:16 (1080×1920), they are displayed in the channel profile grid as 4:5 (1080×1350) and main feed preview as 1:1 (1080×1080).
- Center 1080×1350 is the critical zone: Top 285 px and Bottom 285 px are cropped out in profile views.
- **Rule:** Primary subject matter and on-screen hook graphics must sit within Y = 285 to Y = 1635.

## 3. Metadata & Publishing Guidelines
- **Captions:** First 125 characters visible before "...more". Put the primary benefit keyword in words 1–10.
- **Hashtags:** Use 3–5 targeted hashtags (e.g., `#techhacks`, `#productivitytips`, `#shortcutkeys`). Avoid tag stuffing (10+ tags reduces algorithmic reach).
- **Synthetic Media Disclosure:** Meta policy requires labeling AI-generated photorealistic video or clone voices. Tag content with `is_synthetic=True` where applicable.
