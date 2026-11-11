---
id: kb-ai-001
title: "Free-Tier AI Provider Quotas, Constraints & Operational Limits"
category: ai-providers
version: 1.0.0
verification_status: VERIFIED
confidence: 0.98
confidence_level: HIGH
owner_agent: agent-4-providers
sources:
  - "Google AI Studio Free Tier Documentation (ai.google.dev/pricing)"
  - "GroqCloud API Rate Limits Documentation (console.groq.com/docs/rate-limits)"
  - "OpenRouter Free Models Registry (openrouter.ai/models?max_price=0)"
  - "Microsoft Edge-TTS Open-Source Specification"
tags:
  - quotas
  - free-tier
  - rate-limits
  - zero-cost
  - failover
applies_to:
  - "agent/modules/model_router.py"
  - "agent/modules/quota.py"
  - "agent/modules/llm.py"
created_at: "2026-11-10T00:00:00Z"
last_verified: "2026-11-10T12:00:00Z"
---

# Free-Tier AI Provider Quotas, Constraints & Operational Limits [VERIFIED]

## Executive Summary
Shorts Agent operates under an immutable $0.00 zero-cost budget constraint. All model inference, visual generation, audio synthesis, and transcription must execute exclusively via verified free-tier quotas and local open-source tools.

## 1. Verified Provider Quotas Table

| Provider | Model / Endpoint | Free-Tier Quota Limit | Reset Window | Primary Task Allocation | Failover Priority |
|---|---|---|---|---|---|
| **Google Gemini** | `gemini-flash-lite-latest` | 15 RPM / 1,500 RPD / 1M TPM | Rolling min & Daily UTC | Scriptwriting, Hook scoring, Strategy | Tier 1 (Primary) |
| **Google Gemini** | `gemini-2.0-flash` | 10 RPM / 1,500 RPD / 4M TPM | Rolling min & Daily UTC | Multimodal analysis, Complex QA | Tier 1 (Alternative) |
| **GroqCloud** | `llama-3.3-70b-versatile` | 30 RPM / 14,400 RPD / 6k TPM | Rolling min & Daily UTC | Anti-Slop JSON review, Script rewrite | Tier 2 (Secondary) |
| **GroqCloud** | `whisper-large-v3` | 20 RPM / 2,000 RPD | Rolling min | Word-level audio alignment | Tier 1 (Audio) |
| **OpenRouter** | `meta-llama/llama-3.1-70b-instruct:free` | 20 RPM / 50 RPD | Daily UTC | Final emergency fallback | Tier 3 (Tertiary) |
| **Pollinations.ai** | FLUX Schnell / Realism | No fixed cap; 6.0s rate spacing | Real-time pacing | Visual scene asset generation | Tier 1 (Visual) |
| **Edge-TTS** | `en-IN-NeerjaNeural`, `en-US-GuyNeural` | Unlimited (Local execution) | Continuous | High-retention neural voiceover | Tier 1 (Voice) |
| **Local FFmpeg** | `ffmpeg` (LGPL build) | Unlimited (Local CPU/GPU) | Continuous | Video assembly, captions, loudness | Tier 1 (Render) |

## 2. Rate Pacing & Exponential Backoff Rules
1. **HTTP 429 & RESOURCE_EXHAUSTED Handling:**
   - On HTTP 429, trigger immediate 3-state Circuit Breaker transition (`CLOSED` -> `OPEN`).
   - Compute backoff delay using Full Jitter formula: `t_sleep = random(0, min(max_backoff, base * 2 ^ attempt))`.
   - Base = 2.0s, Max Backoff = 30.0s, Max Retries = 3.
2. **Provider Failover Sequence:**
   - Text Generation: `Gemini 2.0 Flash/Lite` -> `Groq Llama 3.3 70B` -> `OpenRouter :free` -> Raise `LLMError`.
   - Asset Generation: `Pollinations FLUX` -> `Procedural FFmpeg zoompan gradient card`.

## 3. Quota Ledger Integrity
- Quota usage must be logged atomically to `agent/state/quota_ledger.sqlite`.
- When daily remaining quota reaches <20% (threshold 300 RPD for Gemini), send Telegram warning alert.
