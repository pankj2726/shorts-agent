# Phase 0 Reconnaissance: Identity Infrastructure Audit
## Multi-Provider Identity, Credentials, and Secret Store Audit

**Date:** 2026-11-11  
**Lead:** Agent 00 — Research Commander  
**Scope:** Local vault, cloud mirrors, GitHub environments, and credential lifecycles.

---

### 1. Current Storage Layers

1. **Local Vault (`C:\Secrets\shorts-agent.env`):**
   * Contains verified keys: `ARENA_API_KEY`, `OPENROUTER_API_KEY`, `DEEPINFRA_API_KEY`, `FAL_AI_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
   * Security: Kept strictly outside project folders and git tracking.
2. **Cloudflare R2 Encrypted Vault (`r2:workspace-cache/vault/`):**
   * Active: `shorts-agent.env.age` encrypted with recipient `age1f8mfu5...`.
   * Version History: `history/shorts-agent-<timestamp>.env.age`.
   * Security: Full ciphertext encryption before leaving disk.
3. **GitHub Actions Environment Secrets (`production`):**
   * Configured in `.github/workflows/provider_handshake.yml`.
   * Variables: `GCP_PROJECT_ID`, `GCP_REGION`, `OPENCODE_API_BASE`.

---

### 2. Transition Plan to Workload Identity Federation (WIF)

Static API keys are functional, but long-lived credentials carry operational exposure risks. The target architecture establishes **Google Cloud Workload Identity Federation (WIF)**:
* Google Cloud issues short-lived OIDC tokens.
* Upstream providers (Anthropic Claude Console, Google Vertex AI) exchange the Google OIDC token for temporary, auto-expiring access tokens (`sk-ant-oat...`).
* Result: Eliminates static key management entirely for production workloads.
