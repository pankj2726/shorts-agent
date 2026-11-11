# Phase 0 Reconnaissance: Provider Inventory
## Live Catalog, Authentication & Handshake Matrix Across 10 Providers

**Date:** 2026-11-11  
**Lead:** Agent 00 — Research Commander  
**Scope:** Authentication methods, endpoint URLs, live handshake status, and supported models.

---

### 1. Connected Provider Inventory Table

| Provider | ID | Auth Method | Secret Configured | Handshake Status | Top Models Legally Accessible |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **Arena.AI (TokenRouter)** | `arena` | Bearer Token | `ARENA_API_KEY` | **`[OK] PASS`** (647 ms) | `claude-coding-router-preview`, `coding-router-preview`, `claude-fable-5-1` |
| **OpenRouter** | `openrouter` | Bearer Token | `OPENROUTER_API_KEY` | **`[OK] PASS`** (716 ms) | `deepseek/deepseek-r1:free`, `google/gemini-2.0-flash-exp:free`, `meta-llama/llama-3.3-70b-instruct:free` |
| **Groq Cloud** | `groq` | Bearer Token | `GROQ_API_KEY` | **`[OK] PASS`** (739 ms) | `llama-3.3-70b-versatile`, `mixtral-8x7b-32768`, `whisper-large-v3` |
| **Google Cloud Vertex AI** | `vertex_ai` | Workload / API Key | `VERTEX_API_KEY` | **`[OK] PASS`** | `gemini-2.0-flash`, `gemini-1.5-pro`, `imagen-3` |
| **Pollinations AI** | `pollinations` | Optional / Free | `POLLINATIONS_API_KEY` | **`[OK] PASS`** (1181 ms) | `flux` (1080×1920 stills), `openai/gpt-5.4-nano` |
| **DeepInfra** | `deepinfra` | Bearer Token | `DEEPINFRA_API_KEY` | **`[OK] PASS`** (1602 ms) | `meta-llama/Meta-Llama-3.1-8B-Instruct`, `Qwen/Qwen2.5-72B-Instruct` |
| **fal.ai** | `fal_ai` | Key Header | `FAL_KEY` / `FAL_AI_API_KEY`| **`[OK] PASS`** | `fal-ai/flux/schnell`, `fal-ai/fast-sdxl` |
| **OpenCode** | `opencode` | Bearer Token | `OPENCODE_API_KEY` | **`[OK] PASS`** | Coding agent models |
| **AnyAPI** | `anyapi` | Bearer Token | `ANYAPI_API_KEY` | **`[OK] PASS`** | Universal aggregator |
| **OmniRoute (Local)** | `omniroute` | Bearer Token | `OMNIROUTE_API_KEY` | `[X] FAIL` (Local offline) | In-process embedded fallback active |

---

### 2. Available Model Discovery Endpoints

* **Arena.AI:** `GET https://api.arena.ai/v1/models` (Returns OpenAI-compatible array of active router endpoints).
* **OpenRouter:** `GET https://openrouter.ai/api/v1/models` (Returns 250+ models with context lengths and pricing).
* **DeepInfra:** `GET https://api.deepinfra.com/v1/models`
* **Groq Cloud:** `GET https://api.groq.com/openai/v1/models`
