---
id: kb-cp-001
title: "Vertical Safe Margins, Kinetic Typography & Audio Loudness Standards"
category: creative-production
version: 1.0.0
verification_status: VERIFIED
confidence: 0.99
confidence_level: HIGH
owner_agent: agent-2-anti-slop
sources:
  - "ITU-R BS.1770-4 / EBU R128 Broadcast Loudness Specification"
  - "YouTube Shorts Player UI Overlay Specifications (2025-2026)"
  - "Instagram Reels Safe Zone Overlay Template"
tags:
  - layout
  - safe-zones
  - loudness
  - lufs
  - kinetic-captions
  - ffmpeg
applies_to:
  - "agent/modules/render.py"
  - "agent/modules/captions.py"
  - "agent/modules/tts.py"
  - "agent/modules/qa2.py"
created_at: "2026-11-10T00:00:00Z"
last_verified: "2026-11-10T12:00:00Z"
---

# Vertical Safe Margins, Kinetic Typography & Audio Loudness Standards [VERIFIED]

## 1. 1080x1920 Vertical Safe Zone Geometry
The native video render canvas is 1080×1920 pixels (9:16 vertical aspect ratio). Both YouTube Shorts and Instagram Reels overlay interactive UI buttons that obscure content.

### Bounding Box Coordinates
- **Full Canvas:** Width = 1080 px, Height = 1920 px.
- **Safe Zone Bounding Box:**
  - Left Margin (`X_MIN`): 80 px (avoids left bezel truncation).
  - Right Margin (`X_MAX`): 940 px (reserves 140 px on right for Like, Comment, Share, Sound icons).
  - Top Margin (`Y_MIN`): 220 px (avoids top search pill, camera icon, and live tags).
  - Bottom Margin (`Y_MAX`): 1480 px (reserves 440 px on bottom for channel name, title, sound attribution, scrubber).
- **Usable Safe Area:** Width = 860 px, Height = 1260 px.

### Caption Placement
- Subtitles must be centered horizontally (`Alignment=2` in ASS format).
- Vertical placement: `MarginV = 380` (positions captions at Y = 1540 px, directly above the title metadata pill without colliding with the bottom controls).

## 2. Kinetic Typography (ASS Subtitles) Specification
- **Font Family:** `Montserrat-Black`, `TheBoldFont`, or `Roboto-Black`.
- **Font Size:** 72 pt (optimal legibility across 5.5" to 6.8" smartphone screens).
- **Primary Fill Color:** White (`&H00FFFFFF`).
- **Karaoke Active Word Highlight:** Vibrant Yellow (`&H0000E6FF` in BGR ASS hex) or Bright Cyan (`&H00FFFF00`).
- **Border / Outline:** Black (`&H00000000`), Outline Width = 4.0 px.
- **Shadow:** Depth = 2.0 px, Blur = 1.0 px.
- **Word Chunking:** Maximum 3 words displayed simultaneously on screen. Word boundary sync error must be <150ms.

## 3. Audio Loudness & Mixing Standards
- **Integrated Loudness Target:** −14.0 LUFS (tolerance: ±1.5 LUFS, acceptable window: −12.5 to −15.5 LUFS).
- **True Peak Ceiling:** −1.0 dBFS (prevents clipping distortion during platform AAC lossy transcoding).
- **Loudness Range (LRA):** ≤ 7.0 LU (ensures voiceover clarity over phone speakers in noisy environments).
- **Speech vs Background Music Ducking:**
  - Active Voiceover: Music ducked to −22.0 dB relative to voice.
  - Scene Transitions: Music swells smoothly to −14.0 dB with 300ms ease curve.
- **FFmpeg loudnorm filter call:**
  `loudnorm=I=-14:TP=-1.0:LRA=7:dual_mono=true:print_format=json`
