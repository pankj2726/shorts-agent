# Shorts Agent — Enterprise Architecture Blueprint (3.0 / 4.0 / 5.0)
## Workforce AI Access, Shared Quota Intelligence, Privacy Isolation & Zero-Billing Workspace Provisioning

**Document Version:** 3.5.0  
**Status:** Approved & Active Architecture  
**Authoritative Scope:** Development Plan 3.0, Security Directive 4.0, Provisioning Directive 5.0

---

## 1. Executive Summary & Dual-Platform Architecture

Shorts Agent is fundamentally structured into two complementary, enterprise-grade platforms operating under a strict **Zero-Cost ($0.00 Expense)** and **Zero-Billing** constraint:

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                           SHORTS AGENT WORKSPACE ECOSYSTEM                        │
├─────────────────────────────────────────────────┬─────────────────────────────────┤
│          AI INFRASTRUCTURE PLATFORM             │     AI PRODUCTION PLATFORM      │
│  (Workforce Access, Quota Ledger, Privacy)      │  (Autonomous Multi-Agent Studio)│
├─────────────────────────────────────────────────┼─────────────────────────────────┤
│ • Workforce Identity & RBAC (Plan 3.0)          │ • Content Intelligence (Agent 1)│
│ • Entitlement Federation (Plan 3.0)             │ • Anti-Slop 6-Stage Gate (Agent 2)│
│ • Central Quota Ledger & Atomic Locks (Plan 3.0)│ • Social & Community (Agent 3)  │
│ • Multi-Tenant Data Isolation (Directive 4.0)   │ • Creative Engine (Agent 7)     │
│ • Zero-Billing Storage & Lifecycle (Directive 5)│ • E2E 1080x1920 Video Assembly  │
│ • Google Drive per-member Provisioning (Dir 5.0)│ • YouTube/Instagram Publishing  │
│ • Instant Onboarding State Machine (Dir 5.0)    │ • Post-Publish Analytics Loop   │
└─────────────────────────────────────────────────┴─────────────────────────────────┘
```

---

## 2. Nine Specialist Agents + Central Orchestrator (Plan 3.0)

1. **Project Orchestrator:** Coordinates workforce members, specialist agents, resource pools, and production pipelines.
2. **Agent 1 — Provider Discovery:** Researches free AI tiers, developer allocations, organization/team plans, and sharing policies.
3. **Agent 2 — Handshake & Integration:** Manages official APIs, OAuth, credential health checks, and connection lifecycles.
4. **Agent 3 — Workforce Identity & Access:** Manages employee directory, RBAC roles, workspace membership, and session policies.
5. **Agent 4 — Entitlement Federation:** Maps member and tester accounts to accessible models, sharing terms, and delegation rules.
6. **Agent 5 — Quota Intelligence:** Maintains the central resource ledger, atomic reservation locks, and usage reconciliation.
7. **Agent 6 — Intelligent Model Router:** Routes each task to the optimal eligible model using free-first, quality, and latency preferences.
8. **Agent 7 — Creative Production:** Operates the autonomous short-form content generation engine (scripts, audio, visuals, render).
9. **Agent 8 — Quality & Anti-Slop:** Independently enforces the 6-stage review pipeline to reject repetitive or low-quality AI outputs.
10. **Agent 9 — Innovation, Reliability & Security:** Conducts independent Red Team penetration testing, failover monitoring, and R&D.

---

## 3. Quota Federation & The 3 Resource Pools (Plan 3.0)

All AI capacity is categorized into three strictly governed resource pools:

| Resource Pool | Description | Sharing & Availability Scope |
|---|---|---|
| **Pool A: Organisation Resources** | Company-managed free API tiers (Gemini Free Studio, Groq Developer Free, OpenRouter `:free`), team subscriptions, local models. | Shared across all authorised employees according to organisation policy. |
| **Pool B: Delegated Resources** | Employee-connected or tester-connected accounts where owner has granted explicit consent AND provider terms permit. | Permission-bound. Dedicated to owner or explicitly delegated teams. Owner-controlled approval supported. |
| **Pool C: Local Compute** | Local neural engines (`edge-tts`, `ffmpeg`, local Whisper, local procedural generation) running on local hardware. | Unlimited compute available without API quota consumption. |

### Atomic Quota Reservation Cycle
To eliminate concurrency collisions where multiple members or agents request capacity simultaneously:
1. **Request Intake:** Gateway receives task modality, token estimate, and member identity.
2. **Eligibility Filter:** Entitlement engine identifies matching resources permitted for this member.
3. **Atomic Lock:** Database transaction locks required tokens/requests on the candidate resource (`reserved += N`, `remaining -= N`).
4. **Execution:** Model invoked via backend proxy with zero credential exposure.
5. **Reconciliation:** Actual tokens consumed are deducted (`reported += actual`, `reserved -= N`, `remaining += (N - actual)`).
6. **Release on Failure:** Unused reservations instantly unlocked if an upstream error occurs.

---

## 4. Multi-Tenant Data Isolation & Privacy Architecture (Directive 4.0)

> **Non-Negotiable Principle:** Shared infrastructure with private member workspaces — **never** a shared private workspace.

### Data Isolation Rules
1. **Relational Database:** Every table containing user data enforces composite ownership constraints (`workspace_id`, `project_id`, `owner_member_id`). Queries without verified context fail closed.
2. **File & Object Storage:** Member storage paths are isolated: `storage/members/<member_id>/`. Member A cannot list, read, or download files from Member B's path.
3. **Agent Context Isolation:** Autonomous agents are spawned as child security principals carrying only the initiating member's authenticated context. Tool calls cannot traverse outside the owner's permission tree.
4. **Zero-Secret Exposure:** Raw API keys and OAuth refresh tokens are encrypted at rest (AES-256-GCM / Age) in `C:\Secrets\` and never rendered in frontend UI, chat messages, agent prompts, logs, or exports.

---

## 5. Zero-Billing Storage & Automatic Google Drive Provisioning (Directive 5.0)

### Zero-Billing Storage Tier
- **Default Storage Backend:** Local filesystem storage (`shorts-agent/storage/`) supplemented with verified free-tier object storage (e.g. Cloudflare R2 free tier under 8 GB / 10M operations, strictly zero overage enabled).
- **Hard Billing Guard:** Automatic overages and paid plan upgrades are permanently disabled at the infrastructure layer. If usage approaches 90% of free limits, non-critical caches expire and warnings fire. No paid tier is ever activated automatically.

### Automatic Google Drive `Workspace` Provisioning
When a new tester or team member logs in:
1. Member is prompted with an optional, isolated Google Drive OAuth consent flow (`drive.file` scope only).
2. The provisioning engine checks for an existing `Workspace` folder to prevent duplicates.
3. If not found, it provisions exactly one root folder: `Workspace/`
4. Inside `Workspace/`, it scaffolds the standard Shorts Agent directory tree:
   ```
   Workspace/
   └── Shorts Agent/
       ├── Saved Information/
       ├── Research/
       ├── Projects/
       ├── Prompts & Templates/
       ├── Generated Assets/
       ├── Reports/
       └── Exports/
   ```
5. The Google Drive folder ID is stored against the member's private record.
6. **Privacy Enforcement:** Token isolation guarantees Member A's token can never touch Member B's Google Drive. If Google Drive is declined or disconnected, Shorts Agent continues functioning smoothly with local storage.

---

## 6. Idempotent Member Onboarding State Machine (Directive 5.0)

Workflow:
`MEMBER_ADDED`  
→ `IDENTITY_CREATED`  
→ `ROLE_AND_PERMISSIONS_ASSIGNED`  
→ `PRIVATE_WORKSPACE_INITIALISED`  
→ `AI_ENTITLEMENTS_EVALUATED`  
→ `MODEL_CATALOGUE_POPULATED`  
→ `ROUTER_ACCESS_ENABLED`  
→ `DRIVE_CONNECTION_REQUESTED`  
→ `DRIVE_WORKSPACE_CREATED` (if connected)  
→ `ONBOARDING_COMPLETE`

---

## 7. Unified Employee Experience & Dashboards

- **AI Chat:** Instant conversation with eligible free-tier models without provider hopping.
- **Model Arena:** Side-by-side prompt benchmarking across authorized free models (Gemini Flash vs Groq Llama vs OpenRouter Free).
- **Agent Workspace:** Interactive engagement with specialized production agents.
- **Creative Studio:** Frame-by-frame short-form video generation (QuickTech Hacks series).
- **Smart Model Selector:** Auto, Best Quality, Fastest, Free-Only ($0.00), Specific Model.
