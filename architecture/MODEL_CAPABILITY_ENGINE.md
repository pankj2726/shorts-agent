# Agent 04: Model & Effort Engineering
## Universal Capability Registry, Parameter Translation & Effort Parity Engine

**Agent:** Agent 04 — Model & Effort Specialist  
**Review Status:** Approved by Research Commander  

---

### 1. Capability Registry Schema

The Capability Registry tracks model specifications dynamically rather than hardcoding static lists:

```json
{
  "models": {
    "claude-3-5-sonnet": {
      "canonical_id": "claude-3-5-sonnet",
      "display_name": "Claude 3.5 Sonnet",
      "provider_mappings": {
        "arena": "claude-coding-router-preview",
        "openrouter": "anthropic/claude-3.5-sonnet",
        "anthropic_wif": "claude-3-5-sonnet-20241022"
      },
      "capabilities": {
        "reasoning_effort": {
          "supported": true,
          "levels": ["low", "medium", "high", "max"]
        },
        "extended_thinking": true,
        "max_thinking_budget": 8192,
        "vision": true,
        "tool_calling": true,
        "structured_json": true,
        "context_window": 200000,
        "max_output": 8192
      }
    },
    "deepseek-r1": {
      "canonical_id": "deepseek-r1",
      "display_name": "DeepSeek R1 (Reasoning)",
      "provider_mappings": {
        "openrouter": "deepseek/deepseek-r1:free"
      },
      "capabilities": {
        "reasoning_effort": {
          "supported": true,
          "levels": ["auto"]
        },
        "extended_thinking": true,
        "max_thinking_budget": 32768,
        "vision": false,
        "tool_calling": false,
        "structured_json": true,
        "context_window": 64000,
        "max_output": 8192
      }
    }
  }
}
```

---

### 2. Parameter Normalization Engine

When an agent requests a generation with normalized settings:
```python
{
    "model": "claude-3-5-sonnet",
    "effort": "high",
    "thinking_budget": 4096,
    "temperature": 0.7
}
```

The Translation Layer automatically maps this to the provider-specific payload:
* **Arena.AI:** `{"model": "claude-coding-router-preview", "reasoning_effort": "high"}`
* **Anthropic API / WIF:** `{"model": "claude-3-5-sonnet-20241022", "thinking": {"type": "enabled", "budget_tokens": 4096}}`
* **OpenRouter DeepSeek R1:** Native thinking is enabled automatically; `reasoning_effort` string is omitted to prevent HTTP 400.
* **Groq Llama 3.3:** Reasoning parameters omitted; standard sampling parameters preserved.
