# Test Infrastructure Specification: Beyond-the-Bottleneck Multi-Model Gateway & Cross-Model History Bridge

**Document Version**: 2.0.0 (Updated with Features 19 & 20)  
**Scope**: Full End-to-End (E2E) Test Harness, Test Strategy, Fixture Architecture, and Master Test Runner  
**Project Root**: `shorts-agent/`  
**Test Root**: `shorts-agent/tests/e2e/`  
**Master Runner**: `shorts-agent/run_e2e_tests.py`  

---

## 1. Test Philosophy & Core Principles

1. **Opaque-Box & Requirement-Driven**:
   Tests are derived strictly from `ORIGINAL_REQUEST.md` and public API contracts (`PROJECT.md`), not internal implementation quirks. They treat the system as a black box with defined HTTP/SSE endpoints, database schemas, and provider contracts.

2. **Progressive Testability & Hermetic Execution**:
   Testing harnesses provide self-contained mocking and server fixtures (`e2e_db`, `mock_gateway_server`) so tests can execute reliably in isolated CI/CD environments without live third-party network dependencies, while remaining fully compatible with live-running daemons.

3. **No External Async Runner Dependency**:
   Because `pytest_asyncio` is not pre-installed in the global runtime environment, tests interact with the gateway server using synchronous `requests` (with `stream=True` for SSE streaming) and standard threading / `asyncio.run()` for internal coroutines. This guarantees zero test failures due to missing runner plugins.

4. **Namespace Isolation & Zero-Cost Override**:
   All new gateway and multi-model tests live strictly inside `tests/e2e/` to prevent interference with legacy YouTube Shorts video pipeline tests. `tests/e2e/conftest.py` explicitly overrides the legacy `zero_cost_guard` fixture so frontier and paid models (`claude-3-5-sonnet`, `gpt-4o`, `deepseek-v3`, etc.) can be tested without raising `ZeroCostViolationError`.

5. **Strict Anti-Cheat & Forensic Verification**:
   No hardcoded test strings or mock facades mimicking success. All tests must verify real state transitions, schema constraints, role format mappings, IMRCM multi-router cascades, and HTTP/SSE protocol compliance.

---

## 2. 4-Tier Test Architecture & Coverage Targets (20 Features)

| Tier | Name | Target Ratio | Target Count | Objective |
|:---:|:---|:---:|:---:|:---|
| **Tier 1** | **Feature Coverage** | ≥5 tests per feature (20 features) | **≥100 tests** | Validate standard inputs, functional happy paths, and core interface contracts for all 20 features. |
| **Tier 2** | **Boundary & Corner Cases** | ≥5 tests per feature (20 features) | **≥100 tests** | Stress edge cases: zero/negative tokens, oversized prompts, network dropouts, 429 cascades, router exhaustion, Unicode/multilingual payloads, and SQLite concurrency locks. |
| **Tier 3** | **Cross-Feature Combinations** | ≥20 pairwise combinatorial tests | **≥20 tests** | Exercise multi-module interactions: e.g. Anthropic SSE stream persistence into SQLite, resumed via OpenAI format on ChatGPT; Model-Preserving Same-Model Cascade on 429 from Arena to OpenRouter to Direct. |
| **Tier 4** | **Real-World Client Scenarios** | ≥10 realistic end-to-end workflows | **≥10 scenarios** | Emulate multi-turn user workflows, Android client polling/streaming, and catastrophic infinite multi-router exhaustion cascade. |
| **Total** | **Comprehensive E2E Suite** | **All 4 Tiers** | **≥230 test cases** | Full opaque-box verification of gateway powerhouse. |

---

## 3. Feature Inventory Coverage Matrix (20 Features)

| # | Feature ID & Description | Milestone | Tier 1 (≥5) | Tier 2 (≥5) | Tier 3 (Pairwise) | Tier 4 (Real-World) |
|---|--------------------------|:---------:|:-----------:|:-----------:|:-----------------:|:-------------------:|
| 1 | Arena Warm TLS & H100 GPU Cluster (Agent A) | M1 | ≥5 | ≥5 | ✓ (F1+F7, F1+F6) | Scenario 1, 4 |
| 2 | Anthropic Workspace Scoping Auto-Discovery | M1 | ≥5 | ≥5 | ✓ (F2+F13) | Scenario 2 |
| 3 | OpenRouter Model Suffix Bug Removal | M1 | ≥5 | ≥5 | ✓ (F3+F11) | Scenario 3 |
| 4 | Groq Model Translation Correction | M1 | ≥5 | ≥5 | ✓ (F4+F11) | Scenario 3 |
| 5 | "Hire 5 More Models" Support in Registry | M1 | ≥5 | ≥5 | ✓ (F5+F1) | Scenario 1, 2 |
| 6 | SQLite Cross-Model Conversation Schema | M2 | ≥5 | ≥5 | ✓ (F6+F7, F6+F13) | Scenario 5 |
| 7 | Cross-Model Format & Role Translation | M2 | ≥5 | ≥5 | ✓ (F7+F8, F7+F14) | Scenario 5 |
| 8 | Cross-Model Conversation Continuation | M2 | ≥5 | ≥5 | ✓ (F8+F1, F8+F14) | Scenario 5 |
| 9 | Lift Artificial Zero-Cost & Blacklist | M3 | ≥5 | ≥5 | ✓ (F9+F10) | Scenario 6 |
| 10 | Production Agents OmniRoute Integration | M3 | ≥5 | ≥5 | ✓ (F10+F11) | Scenario 6 |
| 11 | Workspace Proxy & Failover Dispatcher (Agent B) | M3 | ≥5 | ≥5 | ✓ (F11+F1, F11+F4) | Scenario 4 |
| 12 | Inbound Gateway Daemon (aiohttp on 20128) | M4 | ≥5 | ≥5 | ✓ (F12+F16) | Scenario 7 |
| 13 | Anthropic `/v1/messages` SSE Endpoint | M4 | ≥5 | ≥5 | ✓ (F13+F6, F13+F15) | Scenario 7 |
| 14 | OpenAI `/v1/chat/completions` SSE Endpoint | M4 | ≥5 | ≥5 | ✓ (F14+F6, F14+F15) | Scenario 7 |
| 15 | Synthetic Unbottlenecked Rate Limit Headers | M4 | ≥5 | ≥5 | ✓ (F15+F13, F15+F16) | Scenario 8 |
| 16 | External Client & Android LAN/CORS Compatibility | M4 | ≥5 | ≥5 | ✓ (F16+F13, F16+F14) | Scenario 8 |
| 17 | Continuous Research & Dynamic Discovery | M1 | ≥5 | ≥5 | ✓ (F17+F5) | Scenario 9 |
| 18 | Dual-Track E2E Test Suite & Hardening | M5 | ≥5 | ≥5 | ✓ (F18+All) | All Scenarios |
| 19 | Infinite Multi-Router Cascade Matrix (IMRCM) | M3 | ≥5 | ≥5 | ✓ (429 same-model) | Scenario 10 |
| 20 | Cloudflare Edge Router Sync & Bypass | M1 | ≥5 | ≥5 | ✓ (F20+F1) | Scenario 10 |
| **Total** | **20 Features** | | **≥100 Tests** | **≥100 Tests** | **≥20 Tests** | **≥10 Scenarios** |

**Grand Total Test Target: ≥230 Test Cases**

---

## 4. Test Directory Layout

```
tests/e2e/
├── __init__.py
├── conftest.py                       # Pytest fixtures: Gateway server daemon, DB temp instance, client session, IMRCM mocks
├── tier1_features/
│   ├── test_arena_cluster.py         # Feature 1: Arena H100 & Warm TLS
│   ├── test_anthropic_adapter.py     # Feature 2: Workspace auto-discovery
│   ├── test_provider_fixes.py        # Features 3, 4: OpenRouter suffix & Groq clobbering
│   ├── test_model_hire.py            # Feature 5: 5 new frontier models
│   ├── test_db_schema.py             # Feature 6: SQLite tables & schema
│   ├── test_role_translation.py      # Feature 7: Anthropic <-> OpenAI format translation
│   ├── test_continuation.py          # Feature 8: Cross-model conversation resumption
│   ├── test_cost_limits.py           # Feature 9: Unlocked paid models / no blacklist
│   ├── test_agent_integration.py     # Feature 10: Production agents with OmniRoute
│   ├── test_failover_proxy.py        # Feature 11: Failover cascade dispatcher
│   ├── test_gateway_daemon.py        # Feature 12: aiohttp server startup & lifecycle
│   ├── test_messages_sse.py          # Feature 13: /v1/messages SSE streaming
│   ├── test_chat_completions.py      # Feature 14: /v1/chat/completions SSE streaming
│   ├── test_limit_headers.py         # Feature 15: Synthetic rate limit headers
│   ├── test_android_cors.py          # Feature 16: CORS & 0.0.0.0 LAN connectivity
│   ├── test_model_discovery.py       # Feature 17: Dynamic model catalog sync
│   ├── test_dual_track_meta.py       # Feature 18: Test runner & harness verification
│   ├── test_imrcm_cascade.py         # Feature 19: Infinite Multi-Router Cascade Matrix
│   └── test_cloudflare_sync.py       # Feature 20: Cloudflare Edge Router Sync
├── tier2_boundaries/
│   ├── test_boundaries_tokens.py     # Zero tokens, max tokens, negative tokens
│   ├── test_boundaries_prompts.py    # Empty messages, 100k+ char payloads, Unicode & emojis
│   ├── test_boundaries_network.py    # Connection drops, read timeouts, malformed JSON
│   ├── test_boundaries_auth.py       # Missing API keys, invalid headers, bad org IDs
│   ├── test_boundaries_concurrency.py# Concurrent clients, SQLite write lock stress
│   └── test_boundaries_rate_limits.py# 429 burst loops and router exhaustion limits
├── tier3_cross_feature/
│   ├── test_cross_arena_to_chatgpt.py# Conversation started in Arena continued in ChatGPT
│   ├── test_cross_sse_to_db.py       # SSE streamed response logged directly to SQLite
│   ├── test_cross_failover_persistence.py # Failover trigger with state continuity
│   ├── test_cross_cors_streaming.py  # Android CORS preflight + SSE chunk streaming
│   └── test_cross_imrcm_same_model.py# Same-model preservation across multi-router failover
└── tier4_real_world/
    ├── test_scenario_multiturn_research.py # Long multi-turn script generation session
    ├── test_scenario_mobile_client.py     # Emulated Android client polling and chatting
    ├── test_scenario_failover_cascade.py  # Full cascade under provider degradation
    └── test_scenario_infinite_cascade.py  # Infinite router cascade under provider 429 exhaustion
```

---

## 5. Fixture & Testing Harness Architecture (`tests/e2e/conftest.py`)

### 5.1 `zero_cost_guard` Override
```python
@pytest.fixture(autouse=True)
def zero_cost_guard():
    """Overrides legacy zero_cost_guard to allow testing frontier models."""
    yield
```
- Eliminates `ZeroCostViolationError` on paid models (`gpt-4o`, `claude-3-5-sonnet`, `deepseek-v3`, etc.).
- Does not affect tests running in legacy test folders.

### 5.2 Isolated Database Fixture (`e2e_db`)
- Creates an isolated SQLite database inside `tmp_path / "e2e_conversations.db"`.
- Executes clean schema migrations for `conversations` and `messages`.
- Automatically binds `agent.modules.db.DB_PATH` to this isolated instance.
- Exposes helper CRUD methods:
  - `create_conversation(conversation_id, title, metadata)`
  - `save_message(conversation_id, role, content, model, provider, thinking, tokens_in, tokens_out)`
  - `get_conversation(conversation_id)`
  - `get_conversation_messages(conversation_id, format='anthropic'|'openai'|'raw')`
  - `list_conversations(limit=50)`

### 5.3 In-Process Mock HTTP/SSE Gateway Server (`mock_gateway_server`)
- Runs in a background daemon thread on a dedicated test port (default: `20129` or ephemeral port).
- Exposes:
  - `POST /v1/messages`: Emulates Anthropic API with streaming SSE and synthetic enterprise rate-limit headers:
    - `anthropic-ratelimit-requests-limit: 10000`
    - `anthropic-ratelimit-requests-remaining: 9999`
    - `anthropic-ratelimit-tokens-limit: 2000000`
    - `anthropic-ratelimit-tokens-remaining: 1995000`
  - `POST /v1/chat/completions`: Emulates OpenAI API with streaming SSE chunk events (`data: {"choices": [...]}`) and `data: [DONE]`.
  - `GET /v1/models`: Returns catalog of frontier models (including 5 hired models and Arena H100 models).
  - `GET /v1/conversations`: Returns conversation list from isolated DB.
  - `OPTIONS *`: Handles CORS preflight headers (`Access-Control-Allow-Origin: *`).
- Records all inbound requests (`method`, `path`, `headers`, `body`) for test assertions.
- Supports programmatic mock response overrides and failure simulation (`server.set_mock_response(...)`).

### 5.4 SSE Stream Parsers & Client Helpers
- `parse_sse_events(response)`: Yields parsed event dicts `{"event": ..., "data": ...}` from synchronous `requests.post(..., stream=True)`.
- `parse_anthropic_sse(response)`: Yields typed Anthropic SSE events (`message_start`, `content_block_delta`, `message_delta`, `message_stop`).
- `parse_openai_sse(response)`: Yields decoded OpenAI chunk objects until `[DONE]`.
- `extract_sse_text(response)`: Consumes stream and returns accumulated text response.

---

## 6. Test Runner (`run_e2e_tests.py`)

The runner executes tests using `sys.executable` and `pytest`, providing formatted summaries:

```bash
# Run all E2E test tiers (Tiers 1-4, 20 features)
python run_e2e_tests.py

# Run Tier 1 feature tests only
python run_e2e_tests.py --tier 1

# Run Tier 2 boundary tests only
python run_e2e_tests.py --tier 2

# Run Tier 3 cross-feature tests only
python run_e2e_tests.py --tier 3

# Run Tier 4 real-world scenario tests only
python run_e2e_tests.py --tier 4

# Collect test inventory without running
python run_e2e_tests.py --collect-only

# Verbose output with custom feature filter (e.g. Feature 19 IMRCM)
python run_e2e_tests.py --feature 19 -v
```

---

## 7. Anti-Cheat & Integrity Compliance

- **No Facades**: Mock responses in tests must be driven by explicit test assertions and configurable server states, never hardcoded inside core library code.
- **Genuine Cross-Model Translation**: Role translations between Anthropic (`user`/`assistant`/`thinking`) and OpenAI (`system`/`developer`/`user`/`assistant`) must genuinely validate data integrity across multi-turn exchanges.
- **Real Schema Constraints**: The SQLite tables must enforce foreign keys, non-null fields, and indexing.
- **IMRCM Same-Model Cascade**: Tests must verify that when Arena returns 429, OpenRouter receives the exact requested model slug without `:free` mangling, falling back gracefully to direct provider without dropping context.
