# Phase 0 Reconnaissance: Existing Capability Matrix
## Detailed Capability & Parameter Parity Across Verified Providers

**Date:** 2026-11-11  
**Lead:** Agent 00 — Research Commander  
**Scope:** Reasoning effort, thinking budgets, streaming, tool calling, and multimodal support.

---

### 1. Provider & Model Capability Parity Table

| Provider | Target Model | Max Context | Extended Thinking / Reasoning | Effort Levels Supported | Tool Calling | Structured JSON | Cost per 1M (Approx) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Arena.AI** | `claude-coding-router-preview` | 200k | ✅ Native Thinking | `low`, `medium`, `high`, `max` | ✅ Full | ✅ Full | ~\$0.80 – \$1.20 |
| **Arena.AI** | `coding-router-preview` | 128k | ✅ Dynamic Meta-Router | `low`, `medium`, `high` | ✅ Full | ✅ Full | ~\$0.50 – \$0.90 |
| **OpenRouter** | `deepseek/deepseek-r1:free` | 64k | ✅ Native Chain-of-Thought | Automatic (DeepSeek) | ❌ Limited | ✅ Yes | **\$0.00** |
| **OpenRouter** | `google/gemini-2.0-flash-exp:free` | 1M | ✅ Thinking Budget Mode | Dynamic | ✅ Full | ✅ Full | **\$0.00** |
| **Groq Cloud** | `llama-3.3-70b-versatile` | 128k | ❌ None | N/A | ✅ High-speed | ✅ Full | **\$0.00** / Free Tier |
| **Google Vertex**| `gemini-2.0-flash` | 1M | ✅ Multimodal & Reasoning | Thinking Config | ✅ Full | ✅ Full | Cloud Quota |
| **DeepInfra** | `meta-llama/Meta-Llama-3.1-8B-Instruct` | 128k | ❌ None | N/A | ✅ Basic | ✅ Full | ~\$0.06 |
| **Pollinations** | `flux` | N/A | ❌ N/A (Image Diffusion) | N/A | N/A | N/A | **\$0.00** |
| **fal.ai** | `fal-ai/flux/schnell` | N/A | ❌ N/A (Image Diffusion) | N/A | N/A | N/A | ~\$0.003 / frame |

---

### 2. Parameter Normalization Requirements

To achieve feature parity without errors:
1. **Effort Translation:** When a user sets Effort to `Max`, the gateway must translate this to:
   * Anthropic / Arena: `"reasoning_effort": "high"` or `"thinking": {"type": "enabled", "budget_tokens": 8192}`.
   * OpenAI / Gemini: `"reasoning_effort": "high"`.
   * Standard Open Models: Silently omit reasoning parameters (preventing HTTP 400 Bad Request).
2. **Context Guard:** Prevent sending prompts that exceed the model's physical window (e.g. capping DeepSeek R1 at 64k).
