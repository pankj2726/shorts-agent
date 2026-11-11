---
name: niche-discovery
version: 0.2.0-beta
status: beta
owner: agent-1-research
last_verified: 2026-11-11
confidence: high
evidence_level: platform-tested
---

# Skill: YouTube Shorts Niche Discovery & Blue Ocean Ideation

**Purpose:** Provide structured research heuristics to discover high-CPM, low-saturation sub-niches in short-form video that have viral viewer interest and sustainable evergreen search demand.

---

## 1. Niche Viability Scoring Matrix

Every candidate niche is scored on a 0–100 scale based on 4 pillars:
1. **Search & Browse Volume (30%):** Does the topic generate high algorithmic recommendations and search queries?
2. **Competition Saturation (25%):** Are top search results dominated by faceless low-quality slop, leaving a quality gap?
3. **Monetization & RPM Potential (25%):** CPM/RPM category tier (Tech/Finance/Productivity = $0.20–$0.60 per 1k Short views vs Comedy/Memes = $0.02–$0.05).
4. **Automation & Production Feasibility (20%):** Can assets be procedurally generated via code, terminal capture, or high-definition screen records?

---

## 2. High-Yield Target Verticals for Shorts Agent

### A. QuickTech & OS Power Hacks (Current Primary Niche)
- **Sub-topics:** Hidden Windows 11/12 hotkeys, macOS terminal secrets, Chrome DevTools shortcuts, productivity extensions.
- **Audience:** Developers, knowledge workers, students, tech enthusiasts.
- **Why it works:** Immediate 3-second practical value, high save/share rate, universal applicability.

### B. Developer Tooling & CLI Superpowers
- **Sub-topics:** Git tricks, Docker one-liners, regex in 30 seconds, VS Code hidden features.
- **Audience:** High-intent programmers, high viewer retention, high comment engagement.

### C. Free AI Tools & Workflow Automations
- **Sub-topics:** Free alternative to paid AI, local models, productivity automations.
- **Audience:** Broad professional audience seeking efficiency gains.

---

## 3. Ideation Workflow (Automated via Agent 1)
1. Discover top trending queries from search trends and GitHub release notes.
2. Filter against `config/banned_topics.md` (no scams, zero crypto hype, zero policy violations).
3. Validate each idea against `ideas` table in SQLite for uniqueness.
4. Export top 30 ideas per batch into SQLite database with category tagging.
