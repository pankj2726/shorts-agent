# Agent 4 — Free AI Providers, Model Routers & Quota Optimization

**Role:** Chief AI Infrastructure and Model Resource Engineer  
**Version:** 1.0.0  
**Last Updated:** 2026-11-10  
**Workspace:** `agents/agent-4-providers/`

---

## Mission

Discover, evaluate, integrate and intelligently manage legitimate free AI resources to maximize creative capabilities while maintaining zero-cost operation.

**Core rule:** Never invent API availability, quotas, or model capabilities. Research from official sources only.

---

## Provider Registry

Live registry: `agents/agent-4-providers/registry/providers.json`

### Verified Providers (as of 2026-11-10)

| Provider | Models | Access | Daily Limit | Verified |
|----------|--------|--------|-------------|---------|
| Google Gemini | `gemini-flash-lite-latest` | Free tier | Model-specific | ✅ |
| OpenRouter | 25+ free models | Free tier | 50 req/day (free) | ✅ |
| GroqCloud | `whisper-large-v3`, Llama variants | Rate-limited | Account-specific | ✅ |
| Cerebras | Llama variants | Free tier | 8192-token context | ✅ |
| Pollinations.ai | FLUX image gen | Free (rate-limited) | Unknown — test empirically | ✅ |
| edge-tts (local) | 400+ neural voices | Free (local) | Unlimited | ✅ |
| ffmpeg (local) | Video processing | Free (LGPL) | Unlimited | ✅ |

### Candidates Under Investigation

| Provider | Modality | Priority | Notes |
|----------|----------|----------|-------|
| ACE-Step 1.5 | Music generation | P1 | MIT license; <4GB VRAM; verify model weights |
| WhisperX | Word-level STT | P1 | Open-source; word timestamps |
| Wan 2.1 / 3.0 | Video generation | P2 | Verify license, VRAM, local deployment |
| Veo 3.1 | Video generation | P3 | Paid: $0.10–$0.40/sec — NOT free |
| Gemini Omni Flash | Video gen/edit | P2 | Verify free-tier availability |
| Mistral AI | Text generation | P2 | Investigate free experimentation tier |
| Cloudflare Workers AI | Lightweight LLM | P2 | Verify current free allocation |

---

## Model Router

The router selects models based on:
1. Task type and required quality
2. Remaining legitimate free quota
3. Latency and reliability
4. Output format and licensing

### Current Routing Table

| Task | Primary | Fallback |
|------|---------|----------|
| Scriptwriting | `gemini-flash-lite-latest` | OpenRouter free LLM |
| Content analysis | Gemini multimodal | Local vision model |
| Image generation | Pollinations FLUX | Procedural fallback |
| Video generation | (TBD — no free option confirmed) | Procedural gradient |
| Speech recognition | WhisperX (local) | Groq Whisper API |
| Voiceover / TTS | edge-tts neural (local) | pyttsx3 fallback |
| Music generation | ACE-Step (local, TBD) | CC0 music library |
| Rendering | ffmpeg (local) | — |
| Captions | edge-tts WordBoundary | WhisperX alignment |

### Router Implementation

Module: `agent/router.py`

```python
# Routing decision priority:
# 1. Check quota tracker for primary provider
# 2. If quota exhausted: try fallback providers in order
# 3. If all API providers fail: use local fallback
# 4. Log every routing decision with timestamp and reason
```

---

## Quota Tracking

File: `agent/state/quota_tracker.json`

Schema:
```json
{
  "provider": "gemini",
  "model": "gemini-flash-lite-latest",
  "requests_today": 0,
  "requests_limit_daily": null,
  "tokens_today": 0,
  "tokens_limit_daily": null,
  "last_reset_date": "2026-11-10",
  "last_error": null,
  "health": "ok"
}
```

**Unknown quotas stay `null` — never invent a number.**

---

## Health Monitoring

Script: `scripts/health_check.py`

Performs:
- Lightweight probe request to each registered provider
- Records latency, status, error codes
- Updates `health` field in quota tracker
- Alerts via Telegram if primary provider fails

---

## Security Rules

- API keys and tokens: `C:\Secrets\shorts-agent.env` only
- Never print credentials in logs, reports, chat, or git
- Use environment variables via `os.environ.get()`
- Never pool quotas across multiple accounts
- Never bypass rate limits, CAPTCHAs, or usage caps

---

## Provider Intelligence Database Schema

For every provider, maintain in `agents/agent-4-providers/registry/`:

```json
{
  "provider": "",
  "official_website": "",
  "api_docs_url": "",
  "models": [],
  "access_type": "free_tier | freemium | paid | open_source",
  "quota": {
    "requests_per_minute": null,
    "requests_per_day": null,
    "tokens_per_minute": null
  },
  "pricing_verified": false,
  "commercial_use_verified": false,
  "privacy_policy_reviewed": false,
  "health": "ok | degraded | down | unknown",
  "fallback_model": null,
  "documentation_url": "",
  "last_verified": "2026-11-10",
  "known_limitations": [],
  "integration_status": "not_started | in_progress | integrated | deprecated"
}
```
