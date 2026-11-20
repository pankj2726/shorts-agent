# Automated YouTube Shorts Channel Agent

A production-grade, autonomous YouTube Shorts publishing agent designed to create and publish 1–2 high-retention Shorts per day at zero cost (using free tiers and official APIs only).

---

## 1. Architecture Overview

```
GitHub Actions Cron Scheduler
  │
  ├── 1. Research    : Trending topics (`videos.list` 1 unit) + competitor analysis
  ├── 2. Strategy    : Select novelty angle, curiosity hook, verify against past 40 videos
  ├── 3. Script      : 25–40s (70–110 words), 0-3s hook, loop ending, comment prompt
  ├── 4. QA Gate #1  : Originality check, policy check, hook length ≤ 12 words
  ├── 5. Assets      : Neural voiceover (edge-tts) + vertical stock (Pexels/Pixabay API)
  ├── 6. Render      : ffmpeg/MoviePy assembly, 1080x1920, kinetic captions, −14 LUFS
  ├── 7. QA Gate #2  : Format/duration check, audio loudness, caption sync, license logs
  ├── 8. Publish     : YouTube Data API v3 (`videos.insert` scheduled private)
  ├── 9. Engage      : 2x daily comment moderation & context-aware replies
  └── 10. Analytics  : Daily metrics + weekly learning report fed back into Strategy
```

---

## 2. Directory Layout

```
shorts-agent/
├── .github/workflows/
│   ├── daily_pipeline.yml     # Cron orchestrator: research -> script -> render -> publish
│   ├── engage.yml             # Comment reader & auto-replier (2x daily)
│   ├── analytics.yml          # Daily metrics & weekly report to learnings.md
│   └── commands.yml           # Telegram bot command dispatcher (/status, /pause, /resume)
├── agent/
│   ├── __init__.py
│   ├── main.py                # CLI dispatcher for all sub-commands
│   ├── config/
│   │   ├── channel_bible.md   # Channel niche, tone, avatar, target audience
│   │   ├── hook_rules.md      # Viral hook guidelines & 3-second hold rules
│   │   └── banned_topics.md   # Policy safety boundaries (medical, financial, copyright)
│   ├── modules/
│   │   ├── llm.py             # Resilient LLM router: Gemini -> Groq -> OpenRouter :free
│   │   ├── quota.py           # Daily 10,000 unit quota ledger (fails closed < 20%)
│   │   ├── notify.py          # Telegram bot alerts with inline approval buttons
│   │   ├── db.py              # SQLite state store (ideas, videos, assets, quota)
│   │   ├── research.py        # YouTube Data API trend discovery (1 unit/call)
│   │   ├── strategy.py        # Idea scoring and gap analysis
│   │   ├── script.py          # Scriptwriter & metadata generator
│   │   ├── qa.py              # Quality gates #1 & #2
│   │   ├── tts.py             # edge-tts neural voiceover with word timestamps
│   │   ├── captions.py        # Kinetic ASS styling & Groq Whisper fallback
│   │   ├── assets.py          # Pexels & Pixabay stock video/music fetcher
│   │   ├── render.py          # ffmpeg vertical composition & audio mixing
│   │   ├── publish.py         # YouTube Data API upload & schedule
│   │   ├── comments.py        # Safe comment bot (own-channel only, 1 reply/thread)
│   │   └── analytics.py       # Metrics aggregator & learning generator
│   ├── prompts/               # System prompts for Strategist, Scriptwriter, Comment Agent
│   └── state/                 # SQLite database storage (db.sqlite)
├── assets/
│   ├── fonts/                 # Noto Sans fonts for subtitles
│   └── music/                 # Royalty-free CC0 background music tracks
├── scripts/
│   ├── oauth_setup.py         # Interactive one-time OAuth 2.0 token generator
│   └── smoke_test.py          # Phase 1 integration verification test
├── requirements.txt
├── CHANGELOG.md
└── CHECKPOINT.md
```

---

## 3. Required GitHub Secrets & Variables

### Secrets (`Settings -> Secrets and variables -> Actions -> Repository secrets`)
| Secret Name | Description | Required? |
|---|---|---|
| `GEMINI_API_KEY` | Primary LLM (Google AI Studio) | **Yes** |
| `PEXELS_API_KEY` | Stock video clips (pexels.com/api) | **Yes** |
| `YT_CLIENT_ID` | Google Cloud OAuth Desktop client ID | **Yes** |
| `YT_CLIENT_SECRET` | Google Cloud OAuth Desktop client secret | **Yes** |
| `YT_REFRESH_TOKEN` | Generated via `scripts/oauth_setup.py` | **Yes** |
| `TELEGRAM_BOT_TOKEN` | Status alerts & approval bot (@BotFather) | **Yes** |
| `TELEGRAM_CHAT_ID` | Authorized Telegram chat ID | **Yes** |
| `YT_API_KEY` | Read-only public API key for trends | Recommended |
| `GROQ_API_KEY` | LLM fallback & free Whisper STT | Recommended |
| `PIXABAY_API_KEY` | Secondary stock footage & music | Recommended |
| `OPENROUTER_API_KEY` | Tertiary LLM fallback (`:free` models) | Optional |
| `GH_PAT` | Repo-scoped PAT for `/pause` and `/resume` | Optional |

### Variables (`Settings -> Secrets and variables -> Actions -> Repository variables`)
| Variable Name | Default | Purpose |
|---|---|---|
| `CHANNEL_NICHE` | `Tech & Productivity Tips` | Niche focus |
| `LANGUAGE` | `en` | `en` or `hi` (sets neural TTS voice) |
| `APPROVAL_REQUIRED` | `true` | Telegram manual approval gate |
| `PAUSE_PUBLISHING` | `false` | Master kill switch |
| `MAX_REPLIES_PER_DAY` | `50` | Safe cap for comment replies |

---

## 4. Setup & Quickstart

### Step 1: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 2: Generate YouTube OAuth Refresh Token
Run the interactive setup helper:
```bash
python scripts/oauth_setup.py
```
Sign in with your channel's Google account and grant the requested permissions. Copy the printed `YT_REFRESH_TOKEN` to your GitHub Secrets.

### Step 3: Run the Smoke Test
```bash
python scripts/smoke_test.py
```
This tests your Gemini key, Pexels API, edge-tts synthesis, ffmpeg installation, YouTube read access, and Telegram notifications.

---

## 5. Safety & Compliance Controls

1. **YouTube API Quota:** 10,000 units/day. The pipeline tracks all calls in SQLite via `quota.py` and fails closed if remaining quota drops below 20%.
2. **Comment Bot Safeguards:** Operates strictly on your own channel, uses official `youtube.force-ssl` OAuth scope, limits replies to 1 per top-level thread, enforces 20-second pacing, skips comments with URLs, and runs in draft-only mode for the first 14 days.
3. **AI Content Disclosure:** Automatically sets YouTube's synthetic media flag (`status.containsSyntheticMedia`) when realistic synthetic assets are included, complying with YouTube's Feb 2026 mandate.
4. **Kill Switch:** Set repository variable `PAUSE_PUBLISHING=true` (or send `/pause` via Telegram) to immediately halt all publishing and comment activity.
