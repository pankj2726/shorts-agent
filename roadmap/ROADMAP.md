# Shorts Agent — Prioritized Implementation Roadmap

**Version:** 1.0  
**Last Updated:** 2026-11-10  
**Status:** M0 complete; M1 in progress

---

## Completed Milestones

### ✅ M0 — Reconnaissance (Complete)
- Full codebase audit of `shorts-agent/`
- Existing architecture mapped: 16 core modules, 4 GitHub Actions workflows
- Technology stack confirmed: Python 3.11, ffmpeg, edge-tts, SQLite, Gemini API, YouTube Data API v3
- Constraints established: zero paid APIs, `C:\Secrets\shorts-agent.env` for credentials
- Research plan prepared

### ✅ M6 (Partial) — Creative Production Pipeline (Phases 1–3)
- Phase 1: Architecture, all modules, smoke test 100% green
- Phase 2: Content Engine — 30 ideas, 10 QA-passed scripts in SQLite
- Phase 3: Render Engine — kinetic subtitles, −14.9 LUFS EBU R128, 1080×1920, QA Gate #2 PASSED

---

## Active Milestone

### 🔄 M1 — Agent Infrastructure (In Progress)
**Goal:** Create the five specialist agent definitions, shared knowledge storage, and communication protocols.

**Deliverables:**
- [x] Directory structure: `agents/`, `skills/`, `knowledge/`, `experiments/`, `evaluations/`, `reports/`, `architecture/`, `roadmap/`
- [x] Agent definition files: `AGENT.md` for all 5 agents + Orchestrator
- [x] Provider registry: `agents/agent-4-providers/registry/providers.json`
- [x] Roadmap: `roadmap/ROADMAP.md`
- [x] Task ownership tracker: `architecture/task-ownership.md`
- [x] Communication protocol implementation: `logs/agent-comms.jsonl`
- [x] Shared quota tracker & audit trail: `agent/modules/audit.py` & SQLite `audit_logs`

---

## Completed Milestones (Continued)

### ✅ M2 — Research & Skills Engine (Complete)
- `skills/youtube-shorts-research/SKILL.md` (v0.2.0-beta)
- `skills/retention-optimization/SKILL.md`
- `skills/niche-discovery/SKILL.md`
- Trend Radar & Chaos Signals engine (`trend_radar.py` + GitHub Actions daily scan)

### ✅ M3 — Anti-Slop Engine (Complete)
- `agent/modules/qa2.py` 6-stage review pipeline (100% tests passing)
- Backfilled QA2 evaluation across all 10 scripts (10/10 passed)
- 100-Point Explainable Viral Potential Model (`viral_score.py`)

### ✅ M5 — AI Provider Infrastructure & Resilience (Complete)
- In-project embedded router (`omniroute_embedded.py`)
- 24/7 Cloudflare Worker edge router (`deploy/cloudflare-router/`)
- Python 3.12 future-proofed across all workflows and Dockerfile
- Zero-cost Pollinations AI (435 free models) & OpenRouter fallbacks

---

## Active Milestone

### 🔄 M4 & M6 — Social Intelligence & Live Publishing Operations

**Tasks:**
1. Test `publish.upload_short()` — private/unlisted upload of `video_1_final.mp4`
2. Verify synthetic media disclosure checkbox
3. Test comment engagement loop (`engage.yml` / `comments.py`)
4. Test Telegram `/approve` and `/reject` commands
5. Trigger dry-run of `daily_pipeline.yml` on GitHub Actions
6. Push Phase 3 commit to GitHub ✅ (done: `fef13d1`)

### ⏳ M7 — Innovation & Optimization
**Priority:** MEDIUM  
**Estimated effort:** Ongoing

**Tasks:**
1. Run all 5 experiments from Agent 5 backlog
2. Integrate validated discoveries into production pipeline
3. Stagger Pollinations AI requests (delay between scene keywords)
4. Add CC0 music tracks to `assets/music/`
5. Add Noto Sans Bold font to `assets/fonts/`

### ⏳ M8 — End-to-End Validation
**Priority:** MEDIUM (after M6 complete)

**Tasks:**
1. Full pipeline test: idea → script → TTS → assets → render → QA → publish
2. Multi-agent collaboration test
3. Provider failover test
4. GitHub Actions `daily_pipeline.yml` live run
5. 7-day autonomous operation test

---

## Side-Hustle Scaling Roadmap (Post-M8)

Once core platform is validated:

| Phase | Goal | Key Feature |
|-------|------|------------|
| Scale-1 | 1 Short/day automated | Full daily_pipeline.yml autonomous |
| Scale-2 | 2 Shorts/day, 2 series | Multi-series scheduler |
| Scale-3 | A/B test hooks | Variant generator + analytics feedback |
| Scale-4 | Multi-niche expansion | Channel Bible v2 with 5+ niches |
| Scale-5 | Instagram Reels | Reels format adapter (9:16 → platform-specific) |
| Scale-6 | Monetization analytics | Revenue tracking + content ROI scoring |

---

## Dependencies & Blockers

| Blocker | Owner | Status |
|---------|-------|--------|
| Phase 4 YouTube upload test | User authorization required | ⏳ Waiting |
| ACE-Step local hardware test | Agent 4 | ⏳ Queued |
| WhisperX local install | Agent 4 | ⏳ Queued |
| Wan 2.1 VRAM check | Agent 4 | ⏳ Queued |
| Teamwork prompt approval | User | ⏳ Waiting |
