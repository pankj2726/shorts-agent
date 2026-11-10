# architecture/task-ownership.md

# Task Ownership Registry

**Purpose:** Prevent duplicate investigations. Record which agent owns each active task.  
**Updated:** 2026-11-10

---

## Active Task Ownership

| Task ID | Task Name | Owner | Status | Artifact Path |
|---------|-----------|-------|--------|---------------|
| T001 | Provider Registry — initial build | A4 | ✅ Done | `agents/agent-4-providers/registry/providers.json` |
| T002 | Agent infrastructure — directory structure | Orchestrator | ✅ Done | `agents/` |
| T003 | Phase 4 YouTube upload test | A1+Orchestrator | ⏳ Waiting (user auth) | — |
| T004 | Free LLM Tournament experiment | A5→A4 | ⏳ Queued | `agents/agent-5-innovation/experiments/llm-tournament/` |
| T005 | ACE-Step 1.5 local test | A4 | ⏳ Queued | `agents/agent-4-providers/reports/ace-step-test.md` |
| T006 | WhisperX local test | A4 | ⏳ Queued | `agents/agent-4-providers/reports/whisperx-test.md` |
| T007 | youtube-shorts-research skill | A1 | ⏳ Queued | `skills/youtube-shorts-research/` |
| T008 | humanized-scriptwriting skill | A1 | ⏳ Queued | `skills/humanized-scriptwriting/` |
| T009 | Anti-Slop qa2.py module | A2 | ⏳ Queued | `agent/qa2.py` |
| T010 | Social intelligence module | A3 | ⏳ Queued | `agent/social.py` |
| T011 | Model router implementation | A4 | ⏳ Queued | `agent/router.py` |
| T012 | Innovation backlog — initial population | A5 | ✅ Done | `agents/agent-5-innovation/backlog/BACKLOG.md` |

---

## Claiming a Task

When an agent begins a task:
1. Update `Status` to `🔄 In Progress`
2. Record start date in a comment
3. Never start a task already claimed by another agent without Orchestrator approval
