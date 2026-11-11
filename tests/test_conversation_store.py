"""
test_conversation_store.py — Comprehensive Unit Test Suite for Milestone 2:
Unified Cross-Model Conversation Store & Context Bridge.

Covers 46 test cases across 6 architectural classes:
  1. TestConversationStoreCRUD (10 tests)
  2. TestAnthropicTurnNormalization (8 tests)
  3. TestSystemDeveloperRoleMapping (6 tests)
  4. TestThinkingContentHandling (6 tests)
  5. TestCrossModelContinuationSimulation (8 tests)
  6. TestEdgeCasesAndSecurity (8 tests)
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
from pathlib import Path
import pytest

from agent.modules import db
from agent.modules import conversation_store
from agent.modules.conversation_store import (
    CanonicalRole,
    CanonicalMessage,
    CanonicalConversation,
    extract_thinking_tags,
    coalesce_consecutive_roles,
    normalize_for_anthropic,
    normalize_for_openai,
    canonical_to_anthropic,
    anthropic_to_canonical,
    canonical_to_openai,
    openai_to_canonical,
    canonical_to_arena,
    arena_to_canonical,
    translate_messages,
    save_conversation,
    save_message,
    get_conversation,
    get_conversation_messages,
    list_conversations,
    export_conversation_for_provider,
    delete_conversation,
)


@pytest.fixture
def temp_db(monkeypatch):
    """Creates a fresh, isolated temporary SQLite database for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_conversations.db"
        monkeypatch.setattr(db, "DB_PATH", db_path)
        # Initialize schema
        conn = db.get_conn()
        conn.close()
        yield db_path


# ============================================================================
# Class 1: Database Operations (CRUD)
# ============================================================================

class TestConversationStoreCRUD:

    def test_save_and_get_conversation(self, temp_db):
        cid = save_conversation(
            conversation_id="conv_crud_1",
            title="Docker Optimization",
            default_provider="arena",
            default_model="claude-3-5-sonnet",
            metadata={"client": "web", "version": 2}
        )
        assert cid == "conv_crud_1"

        conv = get_conversation("conv_crud_1")
        assert conv is not None
        assert conv["id"] == "conv_crud_1"
        assert conv["title"] == "Docker Optimization"
        assert conv["default_provider"] == "arena"
        assert conv["default_model"] == "claude-3-5-sonnet"
        assert conv["metadata"] == {"client": "web", "version": 2}
        assert conv["message_count"] == 0

    def test_save_conversation_defaults(self, temp_db):
        cid = save_conversation()
        assert cid.startswith("conv_")
        conv = get_conversation(cid)
        assert conv is not None
        assert conv["title"] == "New Conversation"
        assert conv["created_at"] is not None
        assert conv["updated_at"] is not None

    def test_save_message_basic(self, temp_db):
        cid = save_conversation(title="Test Message Basic")
        m1 = save_message(cid, role="user", content="Hello world", model="gpt-4o", provider="openai")
        m2 = save_message(cid, role="assistant", content="Greetings!", model="gpt-4o", provider="openai")
        assert m1 > 0
        assert m2 > m1

        msgs = get_conversation_messages(cid, target_format="canonical")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello world"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "Greetings!"

    def test_save_message_auto_creates_conversation(self, temp_db):
        m_id = save_message("auto_conv_99", role="user", content="Auto create test")
        assert m_id > 0
        conv = get_conversation("auto_conv_99")
        assert conv is not None
        assert conv["id"] == "auto_conv_99"

    def test_auto_title_derivation(self, temp_db):
        cid = save_conversation(title="New Conversation")
        long_prompt = "Write a Python script to download YouTube shorts with subtitles automatically"
        save_message(cid, role="user", content=long_prompt)
        conv = get_conversation(cid)
        assert conv["title"] == long_prompt[:40].strip()

    def test_message_chronological_ordering(self, temp_db):
        cid = save_conversation()
        for i in range(1, 6):
            save_message(cid, role="user" if i % 2 == 1 else "assistant", content=f"Msg {i}")

        msgs = get_conversation_messages(cid, target_format="canonical")
        assert len(msgs) == 5
        for i in range(4):
            assert msgs[i]["id"] < msgs[i + 1]["id"]
        assert [m["content"] for m in msgs] == ["Msg 1", "Msg 2", "Msg 3", "Msg 4", "Msg 5"]

    def test_list_conversations_pagination(self, temp_db):
        for i in range(12):
            save_conversation(f"conv_page_{i:02d}", title=f"Thread {i}")

        page1 = list_conversations(limit=5, offset=0)
        page2 = list_conversations(limit=5, offset=5)
        assert len(page1) == 5
        assert len(page2) == 5

        ids_p1 = {c["id"] for c in page1}
        ids_p2 = {c["id"] for c in page2}
        assert ids_p1.isdisjoint(ids_p2)

    def test_cascade_delete_conversation(self, temp_db):
        cid = "conv_cascade"
        save_conversation(cid, title="Delete Me")
        save_message(cid, role="user", content="Turn 1")
        save_message(cid, role="assistant", content="Turn 2")
        save_message(cid, role="user", content="Turn 3")
        assert db.count_messages(cid) == 3

        deleted = delete_conversation(cid)
        assert deleted is True
        assert get_conversation(cid) is None
        assert db.count_messages(cid) == 0

    def test_invalid_role_rejection(self, temp_db):
        with pytest.raises(ValueError):
            save_message("c1", role="superuser", content="Should fail")

    def test_empty_content_handling(self, temp_db):
        cid = save_conversation()
        m_id = save_message(cid, role="user", content="")
        assert m_id > 0
        msgs, _ = get_conversation_messages(cid, target_format="anthropic")
        assert len(msgs) == 1
        assert len(msgs[0]["content"]) >= 1


# ============================================================================
# Class 2: Anthropic Turn Normalization
# ============================================================================

class TestAnthropicTurnNormalization:

    def test_consecutive_user_messages_coalesced(self, temp_db):
        cid = save_conversation()
        save_message(cid, role="user", content="Hello")
        save_message(cid, role="user", content="Are you ready?")
        msgs, _ = get_conversation_messages(cid, target_format="anthropic")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello\n\nAre you ready?"

    def test_consecutive_assistant_messages_coalesced(self, temp_db):
        cid = save_conversation()
        save_message(cid, role="user", content="Prompt")
        save_message(cid, role="assistant", content="Part 1")
        save_message(cid, role="assistant", content="Part 2")
        msgs, _ = get_conversation_messages(cid, target_format="anthropic")
        assert len(msgs) == 2
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "Part 1\n\nPart 2"

    def test_system_message_extracted_to_top_level(self, temp_db):
        cid = save_conversation()
        save_message(cid, role="system", content="You are a video expert.")
        save_message(cid, role="user", content="Give me ideas.")
        save_message(cid, role="assistant", content="Here are 3 ideas.")

        msgs, sys_prompt = get_conversation_messages(cid, target_format="anthropic")
        assert sys_prompt == "You are a video expert."
        assert len(msgs) == 2
        assert all(m["role"] in ("user", "assistant") for m in msgs)

    def test_multiple_system_messages_merged(self, temp_db):
        cid = save_conversation()
        save_message(cid, role="system", content="Rule 1: Be concise.")
        save_message(cid, role="developer", content="Rule 2: Use Markdown.")
        save_message(cid, role="user", content="Hi.")

        msgs, sys_prompt = get_conversation_messages(cid, target_format="anthropic")
        assert sys_prompt == "Rule 1: Be concise.\n\nRule 2: Use Markdown."
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"

    def test_starts_with_user_turn(self, temp_db):
        cid = save_conversation()
        save_message(cid, role="assistant", content="Hello! How can I help?")
        save_message(cid, role="user", content="I need video scripts.")

        msgs, _ = get_conversation_messages(cid, target_format="anthropic")
        assert len(msgs) == 3
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert msgs[2]["role"] == "user"

    def test_clean_alternation_invariant(self, temp_db):
        cid = save_conversation()
        roles = ["system", "user", "user", "assistant", "assistant", "assistant", "user", "user", "assistant", "assistant"]
        for idx, r in enumerate(roles):
            save_message(cid, role=r, content=f"Turn {idx}")

        msgs, _ = get_conversation_messages(cid, target_format="anthropic")
        assert msgs[0]["role"] == "user"
        for i in range(len(msgs) - 1):
            assert msgs[i]["role"] != msgs[i + 1]["role"], f"Invariant failed at {i}: {msgs[i]} vs {msgs[i+1]}"

    def test_midstream_system_update_converted_to_user_note(self, temp_db):
        raw = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "system", "content": "User switched to Python mode"},
            {"role": "user", "content": "Write quicksort"}
        ]
        sys_str, msgs = normalize_for_anthropic(raw)
        assert len(msgs) == 3
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert msgs[2]["role"] == "user"
        assert "[System Update: User switched to Python mode]" in msgs[2]["content"]
        assert "Write quicksort" in msgs[2]["content"]

    def test_empty_content_sanitized_for_anthropic(self, temp_db):
        raw = [
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": ""}
        ]
        _, msgs = normalize_for_anthropic(raw)
        assert len(msgs) == 2
        assert msgs[0]["content"] == " "
        assert msgs[1]["content"] == " "


# ============================================================================
# Class 3: System vs Developer Role Mapping
# ============================================================================

class TestSystemDeveloperRoleMapping:

    def test_canonical_to_openai_standard(self, temp_db):
        cid = save_conversation()
        save_message(cid, role="system", content="System instruction.")
        save_message(cid, role="user", content="Hello.")
        payload = export_conversation_for_provider(cid, provider="openai", model="gpt-4o")
        msgs = payload["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "System instruction."
        assert msgs[1]["role"] == "user"

    def test_canonical_to_openai_reasoning_developer(self, temp_db):
        cid = save_conversation()
        save_message(cid, role="system", content="Reasoning steering instruction.")
        save_message(cid, role="user", content="Solve logic puzzle.")

        # Test with o1
        p_o1 = export_conversation_for_provider(cid, provider="openai", model="o1-preview")
        assert p_o1["messages"][0]["role"] == "developer"
        assert p_o1["messages"][0]["content"] == "Reasoning steering instruction."

        # Test with o3-mini
        p_o3 = export_conversation_for_provider(cid, provider="openai", model="o3-mini")
        assert p_o3["messages"][0]["role"] == "developer"

    def test_canonical_developer_mapped_to_system_for_groq(self, temp_db):
        cid = save_conversation()
        save_message(cid, role="developer", content="System steering.")
        save_message(cid, role="user", content="Fast inference.")
        payload = export_conversation_for_provider(cid, provider="groq", model="llama-3.3-70b-versatile")
        msgs = payload["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "System steering."

    def test_openai_consecutive_user_turns_preserved(self, temp_db):
        cid = save_conversation()
        save_message(cid, role="user", content="Turn A")
        save_message(cid, role="user", content="Turn B")
        payload = export_conversation_for_provider(cid, provider="openai", model="gpt-4o")
        msgs = payload["messages"]
        assert len(msgs) == 2
        assert msgs[0]["content"] == "Turn A"
        assert msgs[1]["content"] == "Turn B"

    def test_deepinfra_and_cerebras_strict_system_mapping(self, temp_db):
        cm = [
            CanonicalMessage(role="developer", content="Developer instruction"),
            CanonicalMessage(role="user", content="Prompt")
        ]
        out_di = canonical_to_openai(cm, target_provider="deepinfra", target_model="meta-llama/Llama-3.3-70B-Instruct")
        assert out_di[0]["role"] == "system"

        out_cb = canonical_to_openai(cm, target_provider="cerebras", target_model="llama-3.3-70b")
        assert out_cb[0]["role"] == "system"

    def test_default_system_prepended_when_missing(self, temp_db):
        cm = [CanonicalMessage(role="user", content="Single user turn")]
        out = canonical_to_openai(cm, default_system="Default steer", target_provider="openai", target_model="gpt-4o")
        assert len(out) == 2
        assert out[0]["role"] == "system"
        assert out[0]["content"] == "Default steer"


# ============================================================================
# Class 4: Thinking Content Handling
# ============================================================================

class TestThinkingContentHandling:

    def test_thinking_content_roundtrip(self, temp_db):
        cid = save_conversation()
        m_id = save_message(
            cid,
            role="assistant",
            content="Answer text",
            thinking_content="Step 1: Calculate... Step 2: Conclude."
        )
        assert m_id > 0
        msgs = get_conversation_messages(cid, target_format="canonical")
        assert len(msgs) == 1
        assert msgs[0]["thinking_content"] == "Step 1: Calculate... Step 2: Conclude."
        assert msgs[0]["content"] == "Answer text"

    def test_thinking_isolated_from_openai_export(self, temp_db):
        cid = save_conversation()
        save_message(
            cid,
            role="assistant",
            content="Visible solution",
            thinking_content="Secret internal scratchpad"
        )
        payload = export_conversation_for_provider(cid, provider="openai", model="gpt-4o")
        msgs = payload["messages"]
        assert len(msgs) == 1
        assert msgs[0]["content"] == "Visible solution"
        assert "Secret internal scratchpad" not in msgs[0]["content"]

    def test_thinking_isolated_from_groq_export(self, temp_db):
        cid = save_conversation()
        save_message(
            cid,
            role="assistant",
            content="Llama answer",
            thinking_content="Hidden scratchpad trace"
        )
        payload = export_conversation_for_provider(cid, provider="groq", model="llama-3.3-70b-versatile")
        msgs = payload["messages"]
        assert msgs[0]["content"] == "Llama answer"
        assert "Hidden scratchpad trace" not in msgs[0]["content"]

    def test_thinking_preserved_in_conversation_detail(self, temp_db):
        cid = save_conversation()
        save_message(cid, role="user", content="What is P vs NP?")
        save_message(
            cid,
            role="assistant",
            content="P vs NP is an unsolved problem in computer science.",
            thinking_content="Considering Turing machines and polynomial verifiability..."
        )
        msgs = get_conversation_messages(cid, target_format="canonical")
        assert len(msgs) == 2
        assert msgs[1]["thinking_content"] == "Considering Turing machines and polynomial verifiability..."

    def test_deepseek_think_tag_extraction(self, temp_db):
        raw_text = "<think>Analyzing quadratic roots via formula b^2-4ac</think>The roots are 2 and 3."
        clean, thinking = extract_thinking_tags(raw_text)
        assert thinking == "Analyzing quadratic roots via formula b^2-4ac"
        assert clean == "The roots are 2 and 3."

        # Case without think tags
        clean2, thinking2 = extract_thinking_tags("Plain message without tags")
        assert thinking2 is None
        assert clean2 == "Plain message without tags"

    def test_claude_37_thinking_signature_preserved_and_missing_isolated(self, temp_db):
        # Case A: Valid signature -> Emit structured thinking block
        cm_valid = [
            CanonicalMessage(
                role="user",
                content="Analyze"
            ),
            CanonicalMessage(
                role="assistant",
                content="Result",
                thinking_content="Deep analysis...",
                thinking_signature="sig_cryptographic_12345"
            )
        ]
        _, msgs_37 = canonical_to_anthropic(cm_valid, target_model="claude-3-7-sonnet-20250219")
        assert isinstance(msgs_37[1]["content"], list)
        assert msgs_37[1]["content"][0]["type"] == "thinking"
        assert msgs_37[1]["content"][0]["signature"] == "sig_cryptographic_12345"
        assert msgs_37[1]["content"][1]["type"] == "text"

        # Case B: Missing signature (e.g. from DeepSeek) -> Do NOT emit synthetic thinking block
        cm_no_sig = [
            CanonicalMessage(role="user", content="Analyze"),
            CanonicalMessage(role="assistant", content="Result", thinking_content="Deep analysis...", thinking_signature=None)
        ]
        _, msgs_no_sig = canonical_to_anthropic(cm_no_sig, target_model="claude-3-7-sonnet-20250219")
        assert isinstance(msgs_no_sig[1]["content"], str)
        assert msgs_no_sig[1]["content"] == "Result"

        # Case C: Target is Claude 3.5 Sonnet -> Thinking stripped
        _, msgs_35 = canonical_to_anthropic(cm_valid, target_model="claude-3-5-sonnet")
        assert isinstance(msgs_35[1]["content"], str)
        assert msgs_35[1]["content"] == "Result"


# ============================================================================
# Class 5: Cross-Model Continuation Simulation
# ============================================================================

class TestCrossModelContinuationSimulation:

    def test_claude_to_chatgpt_continuation(self, temp_db):
        cid = save_conversation(title="Docker vs Podman")
        # Turn 1 on Claude
        save_message(cid, role="user", content="Compare Docker and Podman.", model="claude-3-5-sonnet", provider="anthropic")
        save_message(
            cid,
            role="assistant",
            content="Docker uses a daemon, while Podman is daemonless.",
            model="claude-3-5-sonnet",
            provider="anthropic",
            thinking_content="Inspecting architecture of rootless containers..."
        )

        # Turn 2: Continued on GPT-4o
        save_message(cid, role="user", content="Give me the CLI commands for both.", model="gpt-4o", provider="openai")
        payload = export_conversation_for_provider(cid, provider="openai", model="gpt-4o")

        msgs = payload["messages"]
        assert len(msgs) == 3
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "Docker uses a daemon, while Podman is daemonless."
        assert msgs[2]["role"] == "user"
        assert msgs[2]["content"] == "Give me the CLI commands for both."

        # Save GPT-4o reply
        save_message(cid, role="assistant", content="Docker: docker run... Podman: podman run...", model="gpt-4o", provider="openai")
        all_msgs = get_conversation_messages(cid, target_format="canonical")
        assert len(all_msgs) == 4
        assert [m["model"] for m in all_msgs] == ["claude-3-5-sonnet", "claude-3-5-sonnet", "gpt-4o", "gpt-4o"]

    def test_chatgpt_to_claude_continuation(self, temp_db):
        cid = save_conversation(title="Rust Async")
        # Turn 1 on GPT-4o
        save_message(cid, role="developer", content="You are a senior Rust mentor.")
        save_message(cid, role="user", content="Explain Tokio runtime.", model="gpt-4o", provider="openai")
        save_message(cid, role="assistant", content="Tokio is an async runtime...", model="gpt-4o", provider="openai")

        # Turn 2 on Claude
        save_message(cid, role="user", content="How does work-stealing work?", model="claude-3-5-sonnet", provider="anthropic")
        payload = export_conversation_for_provider(cid, provider="anthropic", model="claude-3-5-sonnet")

        assert payload["system"] == "You are a senior Rust mentor."
        msgs = payload["messages"]
        assert len(msgs) == 3
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert msgs[2]["role"] == "user"

    def test_arena_to_groq_continuation(self, temp_db):
        cid = save_conversation(title="Arena to Groq")
        save_message(cid, role="user", content="Draft a system design for rate limiting.", model="coding-router-preview", provider="arena")
        save_message(cid, role="assistant", content="Use token bucket in Redis.", model="coding-router-preview", provider="arena")

        save_message(cid, role="user", content="Now give me the Python code.", model="llama-3.3-70b-versatile", provider="groq")
        payload = export_conversation_for_provider(cid, provider="groq", model="llama-3.3-70b-versatile")
        msgs = payload["messages"]
        assert len(msgs) == 3
        assert msgs[2]["content"] == "Now give me the Python code."

    def test_multi_model_three_way_continuation(self, temp_db):
        cid = save_conversation(title="Tri-Model Thread")
        # Turn 1: Claude
        save_message(cid, role="user", content="Q1", model="claude-3-5-sonnet", provider="anthropic")
        save_message(cid, role="assistant", content="A1", model="claude-3-5-sonnet", provider="anthropic")
        # Turn 2: OpenAI
        save_message(cid, role="user", content="Q2", model="gpt-4o", provider="openai")
        save_message(cid, role="assistant", content="A2", model="gpt-4o", provider="openai")
        # Turn 3: Groq
        save_message(cid, role="user", content="Q3", model="llama-3.3-70b-versatile", provider="groq")
        save_message(cid, role="assistant", content="A3", model="llama-3.3-70b-versatile", provider="groq")

        msgs = get_conversation_messages(cid, target_format="canonical")
        assert len(msgs) == 6
        assert [m["provider"] for m in msgs] == ["anthropic", "anthropic", "openai", "openai", "groq", "groq"]

    def test_arena_paired_round_transcript_conversion(self, temp_db):
        arena_transcript = {
            "session_id": "arena_test_1",
            "turns": [
                {
                    "turn_id": 1,
                    "user_prompt": "Explain Raft",
                    "assistant_response": "Raft is consensus.",
                    "model": "coding-router-preview",
                    "provider": "arena",
                    "latency_ms": 500
                }
            ]
        }
        canonical = arena_to_canonical(arena_transcript)
        assert len(canonical) == 2
        assert canonical[0].role == CanonicalRole.USER.value
        assert canonical[0].content == "Explain Raft"
        assert canonical[1].role == CanonicalRole.ASSISTANT.value
        assert canonical[1].content == "Raft is consensus."

        roundtrip = canonical_to_arena(canonical, format_type="session_transcript")
        assert "turns" in roundtrip
        assert len(roundtrip["turns"]) == 1
        assert roundtrip["turns"][0]["user_prompt"] == "Explain Raft"
        assert roundtrip["turns"][0]["assistant_response"] == "Raft is consensus."

    def test_export_conversation_for_provider_anthropic_payload(self, temp_db):
        cid = save_conversation()
        save_message(cid, role="system", content="System steering.")
        save_message(cid, role="user", content="User prompt.")
        payload = export_conversation_for_provider(cid, provider="anthropic", model="claude-3-5-sonnet", max_tokens=2048, temperature=0.5)
        assert payload["model"] == "claude-3-5-sonnet"
        assert payload["max_tokens"] == 2048
        assert payload["system"] == "System steering."
        assert len(payload["messages"]) == 1
        assert payload["temperature"] == 0.5

    def test_export_conversation_for_provider_openai_payload(self, temp_db):
        cid = save_conversation()
        save_message(cid, role="user", content="Hello.")
        payload = export_conversation_for_provider(cid, provider="openai", model="gpt-4o", max_tokens=1024, temperature=0.7)
        assert payload["model"] == "gpt-4o"
        assert payload["max_tokens"] == 1024
        assert payload["temperature"] == 0.7

    def test_export_conversation_for_provider_reasoning_effort_o1(self, temp_db):
        cid = save_conversation()
        save_message(cid, role="user", content="Deep thought prompt.")
        payload = export_conversation_for_provider(cid, provider="openai", model="o1", max_tokens=8192, reasoning_effort="high")
        assert payload["model"] == "o1"
        assert payload["max_completion_tokens"] == 8192
        assert payload["reasoning_effort"] == "high"
        assert "temperature" not in payload


# ============================================================================
# Class 6: Edge Cases & Security
# ============================================================================

class TestEdgeCasesAndSecurity:

    def test_sql_injection_resilience(self, temp_db):
        evil_cid = "'; DROP TABLE messages; --"
        cid = save_conversation(evil_cid, title="Injection Test")
        assert cid == evil_cid
        save_message(evil_cid, role="user", content="Test injection")

        # Confirm messages table is intact
        msgs = get_conversation_messages(evil_cid, target_format="canonical")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "Test injection"

    def test_unicode_and_multilingual(self, temp_db):
        cid = save_conversation(title="Unicode Thread")
        complex_text = "🔥🎥 नमस्ते दुनिया! こんにちは 世界! ```python\nprint('Hello world!')\n```"
        save_message(cid, role="user", content=complex_text)
        msgs = get_conversation_messages(cid, target_format="canonical")
        assert msgs[0]["content"] == complex_text

    def test_large_payload_handling(self, temp_db):
        cid = save_conversation(title="Large Payload")
        large_body = "x" * 65536  # 64 KB text
        m_id = save_message(cid, role="user", content=large_body)
        assert m_id > 0
        msgs = get_conversation_messages(cid, target_format="canonical")
        assert len(msgs[0]["content"]) == 65536

    def test_malformed_metadata_json(self, temp_db):
        cid = save_conversation(title="Metadata test")
        # Direct SQL injection of corrupt JSON in metadata column
        with db.get_db() as conn:
            conn.execute("UPDATE conversations SET metadata = 'NOT_VALID_JSON' WHERE id = ?", (cid,))

        conv = get_conversation(cid)
        assert conv is not None
        assert conv["metadata"] == {}

    def test_concurrent_database_writes(self, temp_db):
        cid = save_conversation(title="Concurrency Test")
        thread_count = 10
        errors = []

        def worker(idx):
            try:
                save_message(cid, role="user", content=f"Concurrent message {idx}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert db.count_messages(cid) == thread_count

    def test_delete_nonexistent_conversation_returns_false(self, temp_db):
        res = delete_conversation("conv_nonexistent_xyz")
        assert res is False

    def test_translate_messages_universal_bridge(self, temp_db):
        openai_input = [
            {"role": "system", "content": "System directive"},
            {"role": "user", "content": "Question 1"},
            {"role": "assistant", "content": "Answer 1"},
            {"role": "user", "content": "Question 2"}
        ]
        result = translate_messages(openai_input, from_format="openai", to_format="anthropic")
        assert "system" in result
        assert result["system"] == "System directive"
        assert len(result["messages"]) == 3
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][1]["role"] == "assistant"
        assert result["messages"][2]["role"] == "user"

    def test_unsupported_format_raises_value_error(self, temp_db):
        with pytest.raises(ValueError):
            translate_messages([], from_format="bizarre_proto", to_format="openai")
        with pytest.raises(ValueError):
            translate_messages([], from_format="openai", to_format="bizarre_proto")
