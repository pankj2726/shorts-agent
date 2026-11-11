# Architectural Blueprint: 24/7 Always-On Router & Compute Resilience

**Date:** 2026-11-11  
**Project:** YouTube Shorts Agent (`shorts-agent`)

---

## 1. Problem Statement
The user currently runs OmniRoute via a manual PowerShell session on `http://localhost:20128/`.
- If the PowerShell session is closed, or the PC shuts down, the local router becomes unavailable.
- The user requires an architecture where:
  1. Router and services can run inside the project or remain active 24/7 without keeping PowerShell open or the PC powered on.
  2. Disaster recovery mirrors the entire project to Google Drive and Cloudflare R2.
  3. GitHub updates remain prioritized.

---

## 2. The 3-Tier Solution Matrix

```
                      ┌────────────────────────────────────────────────────────┐
                      │                 Shorts Agent Core LLM                  │
                      └──────────────────────────┬─────────────────────────────┘
                                                 │
          ┌──────────────────────────────────────┼──────────────────────────────────────┐
          │ Tier 1: In-Process                   │ Tier 2: 24/7 Edge Cloud              │ Tier 3: Local Silent Service
          ▼                                      ▼                                      ▼
┌───────────────────────────────┐      ┌───────────────────────────────┐      ┌───────────────────────────────┐
│ In-Project Embedded Router   │      │ Cloudflare Worker Edge Proxy  │      │ Windows Background Service    │
│ (agent/modules/omniroute_     │      │ (deploy/cloudflare-router/)   │      │ (deploy/windows-service/)     │
│  embedded.py)                 │      │ 100,000 req/day free ($0.00)  │      │ Starts on PC boot             │
│ Zero process overhead        │      │ Always-On when PC is OFF      │      │ No PowerShell window open     │
└───────────────────────────────┘      └───────────────────────────────┘      └───────────────────────────────┘
```

### Tier 1: In-Project Embedded Router (Already Built & Verified)
- **File:** `agent/modules/omniroute_embedded.py` (3/3 passing unit tests).
- **How it works:** Executes in-process within Python itself whenever any agent script runs (locally or in GitHub Actions).
- **Advantage:** Zero background daemon, zero open PowerShell windows, and runs inside GitHub Actions without needing any local server.

### Tier 2: 24/7 Serverless Edge Router (Zero Cost, PC Turned Off)
- **Directory:** `deploy/cloudflare-router/` (`worker.js` + `wrangler.toml`).
- **How it works:** Deploys as a Cloudflare Worker on Cloudflare's global edge network.
  - Generous Free Tier: **100,000 requests per day** at **$0.00 cost**.
  - Always active 24 hours a day, 365 days a year even when the local machine is powered off.
  - Exposes standard OpenAI-compatible `/v1/chat/completions` and `/v1/models` endpoints.
  - Automatically fails over from Pollinations AI (435 free models) to OpenRouter free models (`:free`) to DeepInfra.
- **Deployment:** Run `npx wrangler deploy` from `deploy/cloudflare-router/`. Then point `OMNIROUTE_BASE_URL` to `https://shorts-omniroute-edge.<subdomain>.workers.dev/v1`.

### Tier 3: Local Windows Background Service (Silent Autostart)
- **Directory:** `deploy/windows-service/install_omniroute_service.ps1`.
- **How it works:** Registers OmniRoute as a native Windows Background Service (or hidden Scheduled Task on boot).
- **Advantage:** Runs automatically upon Windows login with zero visible PowerShell or terminal windows.

---

## 3. Cloudflare R2 vs VRAM Clarification

### Cloudflare R2 Capabilities:
- **Object Storage (S3-compatible):** R2 is high-speed distributed blob storage.
- **Cache Saving:** Ideal for caching generated audio, video clips, model weights, database backups, and project snapshots.
- **Zero Egress Fees:** Uploading and downloading from R2 is free of bandwidth/egress charges.
- **Project Mirror:** Project is mirrored to `r2:workspace-cache/mirror/shorts-agent` via `ws-save.ps1` with an 8 GB safety ceiling.

### Does R2 Support VRAM?
- **No.** Cloudflare R2 does **not** provide VRAM or GPU compute. VRAM (Video RAM) is physical memory attached to graphics hardware (GPUs such as NVIDIA A100, H100, RTX 4090).
- **Zero-Cost VRAM Alternative:**
  - For LLM inference VRAM: Remote serverless APIs (Pollinations AI, OpenRouter `:free`, DeepInfra) handle GPU compute and VRAM on their infrastructure at **$0.00 cost**.
  - For serverless GPU compute: Cloudflare Workers AI (10,000 neurons/day free) or Hugging Face ZeroGPU (free A100 access).
