# Changelog

All notable changes to the Automated YouTube Shorts Channel agent will be documented in this file.

## [1.0.0] - 2026-11-10

### Initial Release (Phase 0 & Phase 1)
- **Workflows:** Added GitHub Actions workflows for `daily_pipeline.yml`, `engage.yml`, `analytics.yml`, and `commands.yml`.
- **LLM Chain:** Created `agent/modules/llm.py` with automatic 429 failover: Gemini (Flash) -> Groq (70B/8B) -> OpenRouter (`:free`).
- **Quota Ledger:** Created `agent/modules/quota.py` tracking daily YouTube API consumption against 10,000 unit cap with fail-closed protection below 20%.
- **Notifications:** Built `agent/modules/notify.py` for Telegram status updates, video delivery, and inline interactive approval buttons.
- **Production Engine:**
  - `agent/modules/research.py`: YouTube trend discovery and competitor playlist monitoring (1 unit/call).
  - `agent/modules/strategy.py`: Topic selection and curiosity gap analysis.
  - `agent/modules/script.py`: 25–40s high-retention scriptwriting with viral hook patterns.
  - `agent/modules/qa.py`: Automated QA Gate #1 (script/policy) and QA Gate #2 (render/loudness/sync).
  - `agent/modules/tts.py`: Microsoft edge-tts neural voiceover generation with word-level timestamps.
  - `agent/modules/captions.py`: Kinetic subtitle generation with current-word highlight and Groq Whisper fallback.
  - `agent/modules/assets.py`: Automated vertical stock clip search and CC0 background music selection.
  - `agent/modules/render.py`: ffmpeg composition engine (1080x1920, −14 LUFS, subtitle burning).
  - `agent/modules/publish.py`: YouTube Data API v3 scheduled upload with synthetic content disclosure.
  - `agent/modules/comments.py`: Policy-compliant comment reading and contextual reply agent.
  - `agent/modules/analytics.py`: Daily performance aggregation and weekly `learnings.md` synthesis.
- **Tools & Setup:**
  - `scripts/oauth_setup.py`: Interactive OAuth 2.0 refresh token generator for Desktop App credentials.
  - `scripts/smoke_test.py`: Phase 1 exit criteria verification test.
