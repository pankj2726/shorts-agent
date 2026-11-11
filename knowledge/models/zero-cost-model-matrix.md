---
id: kb-mod-001
title: "Zero-Cost LLM & Generative Model Capability Matrix"
category: models
version: 1.0.0
verification_status: VERIFIED
confidence: 0.97
confidence_level: HIGH
owner_agent: agent-4-providers
sources:
  - "Google DeepMind Gemini 2.0 Technical Report (2025)"
  - "Groq Llama 3.3 70B Benchmarks & Latency Profiling"
  - "Black Forest Labs FLUX.1 Schnell Technical Specs"
tags:
  - models
  - benchmarks
  - context-window
  - token-limits
  - zero-cost
applies_to:
  - "agent/modules/model_router.py"
  - "agent/modules/llm.py"
  - "agent/modules/qa2.py"
created_at: "2026-11-10T00:00:00Z"
last_verified: "2026-11-10T12:00:00Z"
---

# Zero-Cost LLM & Generative Model Capability Matrix [VERIFIED]

## 1. Whitelisted 100% Free Models

| Model Identifier | Provider | Modality | Context Window | Peak Generation Speed | Optimal Temperature | Primary Pipeline Application |
|---|---|---|---|---|---|---|
| `gemini-2.0-flash` | Google AI Studio | Text + Vision + Audio | 1,048,576 tokens | 120 tokens/sec | 0.7 (Creative) / 0.2 (QA) | Script generation, Multimodal asset audit |
| `gemini-flash-lite-latest` | Google AI Studio | Text | 1,048,576 tokens | 180 tokens/sec | 0.6 | Fast idea generation, Hook taxonomy scoring |
| `llama-3.3-70b-versatile` | GroqCloud | Text | 128,000 tokens | 280 tokens/sec | 0.3 | Anti-Slop JSON review, Script humanization |
| `llama-3.1-8b-instant` | GroqCloud | Text | 128,000 tokens | 550 tokens/sec | 0.1 | Comment classification, Sentiment tagging |
| `whisper-large-v3` | GroqCloud / Local | Audio to Text | 30s chunks | Near real-time | 0.0 | Word boundary timestamp extraction |
| `flux-schnell` | Pollinations.ai | Text to Image | N/A | 3.5s per frame | N/A | Vertical 9:16 scene illustrations |

## 2. Model Routing Guidelines & Temperature Tuning
1. **Scriptwriting & Narrative Arc:**
   - Provider: `gemini-2.0-flash` or `gemini-flash-lite-latest`.
   - Temperature: `0.70`. Encourages conversational creativity and idiomatic phrasing while strictly adhering to system prompt constraints.
2. **Anti-Slop Quality Reviews & JSON Schema Output:**
   - Provider: `llama-3.3-70b-versatile` (Groq) or `gemini-2.0-flash` (Structured Output mode).
   - Temperature: `0.20`. Minimizes hallucination and produces deterministic JSON payloads matching `ComprehensiveReviewReport`.
3. **Comment Intent Classification:**
   - Provider: `llama-3.1-8b-instant` (Groq).
   - Temperature: `0.10`. Fast (550 tokens/sec), single-token classification into 6 intent categories.
