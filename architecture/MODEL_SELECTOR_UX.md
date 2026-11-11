# Agent 05: Unified Model Selector & Product Experience
## Multi-Platform UX Architecture: CLI, Telegram, and Mobile Clients

**Agent:** Agent 05 — Product & Interface Architect  
**Review Status:** Approved by Research Commander  

---

### 1. Unified Model Selector Specification

The selector presents the entire catalog of authorized models across all connected providers, displaying real availability instead of consumer-app paywalls:

```text
===========================================================================
 SHORTS FORGE — Unified Model Selector & Capability Hub
===========================================================================
[Filter: ALL]  [Family: ANTHROPIC | OPENAI | DEEPSEEK | GOOGLE | META]

  1. [*] Claude 3.5 Sonnet        (Arena.AI)     [200k Context] [Effort: Low/Med/High/Max]
  2. [ ] Claude 3.7 Sonnet        (Claude WIF)   [200k Context] [Hybrid Thinking]
  3. [ ] DeepSeek R1 Reasoning    (OpenRouter)   [64k Context]  [$0.00 Free Tier]
  4. [ ] Gemini 2.0 Flash         (OpenRouter)   [1M Context]   [$0.00 Free Tier]
  5. [ ] Llama 3.3 70B Versatile  (Groq)         [128k Context] [Ultra-Fast ~500 t/s]
  6. [ ] Qwen 2.5 Coder 32B       (OpenRouter)   [32k Context]  [$0.00 Free Tier]
  7. [ ] Flux 1080x1920 Stills    (Pollinations) [Vertical 9:16][$0.00 Free Tier]

Selected: Claude 3.5 Sonnet | Effort: High | Provider: Arena.AI TokenRouter
Status:   ONLINE (Latency: 647ms) | Cost: Micro-billing from $50 balance
===========================================================================
```

---

### 2. The Three State Model

To ensure transparency and prevent user confusion:
* **`[OK] AVAILABLE`**: Legitimate API connection verified; model ready to execute immediately.
* **`[!] RESTRICTED`**: Provider credentials missing, quota exhausted, or account balance depleted. Displays exact resolution steps.
* **`[-] UNSUPPORTED`**: Parameter physically unsupported by model (e.g., setting Thinking Effort on pure Llama 3.3). Gracefully ignored.

---

### 3. Telegram Master Operator UI Integration

In `@ShortsOperator_bot`:
* Command `/models`: Renders inline keyboard buttons allowing immediate one-tap switching between frontier models directly from your Android phone or desktop Telegram!
* Command `/effort <low|medium|high|max>`: Instantly toggles the thinking budget for subsequent prompts.
