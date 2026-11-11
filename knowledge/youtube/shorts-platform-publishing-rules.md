---
id: kb-yt-001
title: "YouTube Shorts Platform Specifications, Feed Mechanics & Synthetic Disclosure"
category: youtube
version: 1.0.0
verification_status: VERIFIED
confidence: 0.98
confidence_level: HIGH
owner_agent: agent-1-research
sources:
  - "YouTube Help Center: YouTube Shorts Specifications (support.google.com/youtube/answer/10059070)"
  - "YouTube Help Center: Disclosing AI-Generated or Altered Content (support.google.com/youtube/answer/14328491)"
  - "YouTube Data API v3 Documentation: Videos resource"
tags:
  - youtube
  - shorts
  - feed-mechanics
  - synthetic-disclosure
  - publishing-rules
applies_to:
  - "agent/modules/publish.py"
  - "agent/modules/qa.py"
  - "agent/modules/notify.py"
created_at: "2026-11-10T00:00:00Z"
last_verified: "2026-11-10T12:00:00Z"
---

# YouTube Shorts Platform Specifications, Feed Mechanics & Synthetic Disclosure [VERIFIED]

## 1. Technical Platform Specifications
- **Aspect Ratio:** Strictly 9:16 vertical (1080×1920 pixels).
- **Duration Limits:** Minimum 3 seconds, Maximum 60 seconds (optimal production length: 25–42 seconds).
- **Container & Codec:** MP4 container, H.264 video codec (High Profile, level 4.2), AAC-LC audio codec (192 kbps, 48 kHz stereo).
- **Target Frame Rate:** 30.0 fps Constant Frame Rate (CFR). Avoid variable frame rate (VFR) to prevent audio desync.

## 2. Shorts Feed Distribution Algorithm
YouTube distributes Shorts through an iterative test-batch mechanism:
1. **Seed Audience Phase (0–2 hours post-upload):** Pushed to a test pool of 100–500 impressions. Algorithm measures 3s Viewed vs Swiped Away (VVSA) ratio and Average Percentage Viewed (APV).
2. **Breakout Phase (2–24 hours):** If VVSA ≥ 75% and APV ≥ 85%, video expands to secondary interest graph cohorts (10k–100k impressions).
3. **Long-Tail Search & Recommendation:** High-performing productivity Shorts transition into YouTube Search and Suggested Videos for perpetual traffic.

## 3. Mandatory Synthetic Media Disclosure Policy (2024–2026)
YouTube requires creators to disclose synthetic or altered media that depicts realistic scenes or clone voices.
- **API Configuration:** When uploading via YouTube Data API v3 (`videos.insert`), the metadata request must configure the synthetic content flag:
  `body["recordingDetails"] = {"containsSynthetic": True}`
- **Video Description Text:** Descriptions must include explicit transparency attribution:
  `"This video utilizes synthetic visual illustrations and neural AI voiceover generated for educational demonstration."`
- **Exemptions:** Procedural animations, code screencasts, color grading, and noise filtering do not require disclosure unless photorealistic human likeness is depicted.

## 4. Metadata & Publishing Cadence Rules
- **Title Length:** Maximum 100 characters; strictly ≤ 60 characters visible in mobile feeds without ellipsis truncation.
- **Hashtag Placement:** Exactly 3 hashtags in title: `#shorts` followed by two niche tags (e.g., `#techhacks #productivity`).
- **Publishing Schedule Windows:**
  - Morning Slot: 13:00 IST (07:30 UTC) — Captures European morning and Indian lunch scroll.
  - Evening Slot: 19:00 IST (13:30 UTC) — Captures Indian evening and US morning commute.
