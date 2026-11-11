# Phase 0 Reconnaissance: Current Model Selection
## Audit of Existing Model Selection & Configuration Handling

**Date:** 2026-11-11  
**Lead:** Agent 00 — Research Commander  
**Scope:** Model identifiers, dispatch parameters, thinking settings, and selection limitations.

---

### 1. Existing Model Dispatch Paths

The codebase currently routes inference through two distinct mechanisms:

#### Path A: Legacy `agent/modules/llm.py`
* **Static Model Identifiers:**
  * `GEMINI_MODEL = "gemini-flash-lite-latest"`
  * `GROQ_MODEL = "openai/gpt-oss-120b"`
  * `GROQ_FAST_MODEL = "qwen/qwen3.8-27b"`
  * `OPENROUTER_MODEL = "meta-llama/llama-3.1-70b-instruct:free"`
  * `POLLINATIONS_MODEL = "openai/gpt-5.4-nano"`
* **Dispatch Interface:** `complete(prompt, system_prompt, json_mode=False)`
* **Deficiencies:**
  * No model choice parameter available to calling agents.
  * No `reasoning_effort` or `thinking_budget` parameters exposed.
  * Does not route to Arena.AI or Claude 3.5 Sonnet.

#### Path B: Modern `agent/modules/omniroute_embedded.py`
* **Dynamic Provider Chain:** Pollinations $\rightarrow$ OpenRouter $\rightarrow$ Groq $\rightarrow$ Arena.AI $\rightarrow$ DeepInfra.
* **Supported Models:**
  * Arena.AI: `coding-router-preview`, `claude-coding-router-preview`, `claude-fable-5-1`.
  * OpenRouter: Auto-suffixing `:free` (e.g., `deepseek/deepseek-r1:free`, `google/gemini-2.0-flash-exp:free`).
  * Groq: `llama-3.3-70b-versatile`.
  * DeepInfra: `meta-llama/Meta-Llama-3.1-8B-Instruct`.
* **Reasoning Parameter:** Recently updated to pass `"reasoning_effort": "high"` to Arena.AI, but lacks granular controls (Low, Medium, High, Max) and capability discovery.

---

### 2. What Is Artificially Hidden vs. Genuinely Unsupported

| Capability | Current State | Root Cause | Actual Provider Support |
| :--- | :---: | :--- | :--- |
| **Claude 3.5 / 3.7 Sonnet** | Hidden in `llm.py` | Hardcoded to Gemini Flash Lite | ✅ Fully supported via Arena.AI (`claude-coding-router-preview`) |
| **Effort Level Controls** | Hidden | Hardcoded to `"high"` or omitted | ✅ Fully supported by Anthropic API & Arena router (`low`, `medium`, `high`, `max`) |
| **DeepSeek R1 Reasoning** | Hidden | `llm.py` points to Llama 3.1 | ✅ Fully supported via OpenRouter (`deepseek/deepseek-r1:free`) |
| **Model Dropdown UI** | Missing | Telegram commands lack `/model` picker | ✅ Available via Telegram inline buttons & CLI |
| **Thinking Budget Tokens** | Missing | No schema translation layer | ✅ Supported by Claude API (`thinking.budget_tokens`) |
