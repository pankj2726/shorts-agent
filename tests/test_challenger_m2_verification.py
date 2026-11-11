"""
test_challenger_m2_verification.py — Empirical Challenger Test Suite for Milestone 2.

Tests:
1. End-to-End Cross-Model Handshake Simulation:
   - Arena.AI Claude 3.5 Sonnet -> OpenAI (GPT-4o & o3-mini) -> Groq (Llama-3.3-70b)
   - Role translations (developer for o3-mini, system for GPT-4o & Groq)
   - Chronological message history preservation
2. Gateway Server Reverse Proxy Verification:
   - /v1/chat/completions multi-turn dialogue history preservation (fixing history discard bug)
   - /v1/conversations and /v1/conversations/{id}
   - Backward compatibility for session_id vs conversation_id
3. Boundary & Error Condition Fuzzing:
   - Non-existent IDs
   - Malformed JSON metadata
   - Special unicode, RTL, CJK, math symbols, and control chars
   - Large payload handling (up to 200k chars)
   - Concurrency stress
"""

import json
import os
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from agent.modules import db
from agent.modules.conversation_store import (
    save_conversation,
    save_message,
    get_conversation,
    get_conversation_messages,
    list_conversations,
    export_conversation_for_provider,
    delete_conversation,
    translate_messages,
    CanonicalMessage,
    CanonicalRole
)
from agent.modules.gateway_server import ThreadedHTTPServer, GatewayHandler


# ============================================================================
# Gateway Server Fixture
# ============================================================================

@pytest.fixture(scope="module")
def gateway_server():
    """Spins up an in-process ThreadedHTTPServer on an ephemeral port for testing."""
    port = 20129
    server = ThreadedHTTPServer(("127.0.0.1", port), GatewayHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.3)
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


# ============================================================================
# Test Suite 1: End-to-End Cross-Model Handshake Simulation
# ============================================================================

class TestCrossModelHandshakeSimulation:
    """
    Simulates a multi-turn dialogue starting on Arena.AI with Claude 3.5 Sonnet,
    migrating to OpenAI (GPT-4o and o3-mini), continuing with o3-mini,
    and finally exporting for Groq (Llama-3.3-70b).
    """

    def test_full_cross_model_continuation_lifecycle(self):
        conv_id = f"test_handshake_{int(time.time()*1000)}"

        # Step 1: Simulate realistic dialogue starting on Arena.AI with Claude 3.5 Sonnet
        # System instructions
        db.create_message(
            conversation_id=conv_id,
            role="system",
            content="You are an expert distributed systems engineer.",
            provider="arena",
            model="claude-3-5-sonnet"
        )
        # Turn 1: User -> Claude
        save_message(
            conversation_id=conv_id,
            role="user",
            content="What are the key trade-offs between Paxos and Raft?",
            model="claude-3-5-sonnet",
            provider="arena"
        )
        # Turn 1: Claude Assistant response
        save_message(
            conversation_id=conv_id,
            role="assistant",
            content="Paxos is mathematically elegant but difficult to understand and implement. Raft decomposes consensus into leader election, log replication, and safety.",
            model="claude-3-5-sonnet",
            provider="arena"
        )
        # Turn 2: User -> Claude
        save_message(
            conversation_id=conv_id,
            role="user",
            content="Can Raft handle Byzantine faults?",
            model="claude-3-5-sonnet",
            provider="arena"
        )
        # Turn 2: Claude Assistant response
        save_message(
            conversation_id=conv_id,
            role="assistant",
            content="Standard Raft only handles crash-stop or crash-recovery faults (non-Byzantine). For Byzantine faults, protocols like PBFT or Tendermint are required.",
            model="claude-3-5-sonnet",
            provider="arena"
        )

        # Verify initial conversation state in store
        conv = get_conversation(conv_id)
        assert conv is not None
        assert conv["message_count"] == 5

        # Step 2: Retrieve and export for OpenAI GPT-4o
        gpt4o_payload = export_conversation_for_provider(conv_id, provider="openai", model="gpt-4o")
        assert gpt4o_payload["model"] == "gpt-4o"
        assert "max_tokens" in gpt4o_payload
        assert len(gpt4o_payload["messages"]) == 5

        # Verify GPT-4o role mapping: system role MUST be "system", NOT "developer"
        assert gpt4o_payload["messages"][0]["role"] == "system"
        assert gpt4o_payload["messages"][0]["content"] == "You are an expert distributed systems engineer."
        assert gpt4o_payload["messages"][1]["role"] == "user"
        assert gpt4o_payload["messages"][2]["role"] == "assistant"
        assert gpt4o_payload["messages"][3]["role"] == "user"
        assert gpt4o_payload["messages"][4]["role"] == "assistant"

        # Step 3: Retrieve and export for OpenAI o3-mini (Reasoning Model)
        o3_payload = export_conversation_for_provider(conv_id, provider="openai", model="o3-mini")
        assert o3_payload["model"] == "o3-mini"
        # Reasoning models on OpenAI require max_completion_tokens and reject temperature
        assert "max_completion_tokens" in o3_payload
        assert "temperature" not in o3_payload
        assert len(o3_payload["messages"]) == 5

        # Verify o3-mini role mapping: system role MUST be mapped to "developer"
        assert o3_payload["messages"][0]["role"] == "developer"
        assert o3_payload["messages"][0]["content"] == "You are an expert distributed systems engineer."
        assert o3_payload["messages"][1]["role"] == "user"
        assert o3_payload["messages"][2]["role"] == "assistant"
        assert o3_payload["messages"][3]["role"] == "user"
        assert o3_payload["messages"][4]["role"] == "assistant"

        # Step 4: Continue conversation with o3-mini assistant turn
        # Turn 3: User -> o3-mini
        save_message(
            conversation_id=conv_id,
            role="user",
            content="Compare PBFT's 3-phase commit with 2PC.",
            model="o3-mini",
            provider="openai"
        )
        # Turn 3: o3-mini Assistant response (with internal thinking isolated)
        save_message(
            conversation_id=conv_id,
            role="assistant",
            content="2PC is an atomic commitment protocol requiring a coordinator and assumes no Byzantine nodes. PBFT uses pre-prepare, prepare, and commit phases with 3f+1 nodes to tolerate f Byzantine nodes.",
            thinking_content="Need to explain coordinator vulnerability in 2PC vs Byzantine quorums in PBFT.",
            model="o3-mini",
            provider="openai"
        )

        conv_updated = get_conversation(conv_id)
        assert conv_updated["message_count"] == 7

        # Step 5: Export for Groq (Llama-3.3-70b)
        groq_payload = export_conversation_for_provider(conv_id, provider="groq", model="llama-3.3-70b")
        assert groq_payload["model"] == "llama-3.3-70b"
        assert "max_tokens" in groq_payload
        assert len(groq_payload["messages"]) == 7

        # Crucial Verification for Groq:
        # 1. Groq MUST receive role: "system" (NEVER "developer", which causes HTTP 400 on Groq)
        system_roles = [m["role"] for m in groq_payload["messages"] if m["role"] in ("system", "developer")]
        assert len(system_roles) == 1
        assert system_roles[0] == "system"
        for m in groq_payload["messages"]:
            assert m["role"] != "developer", f"Found illegal developer role in Groq payload: {m}"

        # 2. All preceding turns from Claude and o3-mini MUST be preserved chronologically
        expected_roles = ["system", "user", "assistant", "user", "assistant", "user", "assistant"]
        actual_roles = [m["role"] for m in groq_payload["messages"]]
        assert actual_roles == expected_roles

        # 3. Thinking content from o3-mini must NOT leak into Groq wire text
        assert "Need to explain coordinator vulnerability" not in groq_payload["messages"][-1]["content"]
        assert "2PC is an atomic commitment" in groq_payload["messages"][-1]["content"]

        # Cleanup
        delete_conversation(conv_id)


# ============================================================================
# Test Suite 2: Gateway Server Reverse Proxy Verification
# ============================================================================

class TestGatewayServerReverseProxy:
    """
    Verifies the Gateway HTTP server endpoints (/v1/chat/completions and /v1/conversations),
    checks that dialogue history is preserved, and checks backward compatibility
    for session_id vs conversation_id.
    """

    def test_gateway_chat_completions_preserves_dialogue_history(self, gateway_server):
        """
        Verify that multiple turns in a conversation accumulate in the unified store
        and that the full dialogue history is sent to the router rather than discarded.
        """
        session_id = f"test_gw_hist_{int(time.time()*1000)}"

        captured_prompts = []

        # Mock router.complete_text to capture the prompt sent to the LLM backend
        def mock_complete_text(prompt, system_prompt=None, model=None, effort=None):
            captured_prompts.append({"prompt": prompt, "system_prompt": system_prompt})
            return {
                "text": f"Echo reply to: {prompt[:30]}...",
                "provider": "arena",
                "canonical_model": model or "claude-3-5-sonnet"
            }

        with patch("agent.modules.gateway_server.router.complete_text", side_effect=mock_complete_text):
            # Turn 1: Client sends first question
            req1 = {
                "session_id": session_id,
                "model": "claude-3-5-sonnet",
                "messages": [
                    {"role": "user", "content": "Hello, my favorite color is teal."}
                ]
            }
            req_data1 = json.dumps(req1).encode("utf-8")
            http_req1 = urllib.request.Request(
                f"{gateway_server}/v1/chat/completions",
                data=req_data1,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(http_req1, timeout=5) as resp:
                assert resp.status == 200
                res1 = json.loads(resp.read().decode("utf-8"))
                assert res1["object"] == "chat.completion"
                assert len(res1["choices"]) == 1

            assert len(captured_prompts) == 1
            # Turn 1 prompt should contain the user question
            assert "favorite color is teal" in captured_prompts[0]["prompt"]

            # Turn 2: Client sends follow-up. Standard OpenAI client sends full dialogue history
            req2 = {
                "session_id": session_id,
                "model": "claude-3-5-sonnet",
                "messages": [
                    {"role": "user", "content": "Hello, my favorite color is teal."},
                    {"role": "assistant", "content": "Echo reply to: Hello, my favorite color is t..."},
                    {"role": "user", "content": "What is my favorite color?"}
                ]
            }
            req_data2 = json.dumps(req2).encode("utf-8")
            http_req2 = urllib.request.Request(
                f"{gateway_server}/v1/chat/completions",
                data=req_data2,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(http_req2, timeout=5) as resp:
                assert resp.status == 200
                res2 = json.loads(resp.read().decode("utf-8"))
                assert res2["object"] == "chat.completion"

            assert len(captured_prompts) == 2
            # CRITICAL VERIFICATION: Dialogue history MUST NOT be discarded!
            # Prompt 2 must contain both Turn 1 (teal) and Turn 2 (What is my favorite color?)
            prompt_turn_2 = captured_prompts[1]["prompt"]
            assert "favorite color is teal" in prompt_turn_2, (
                f"History discarded! Turn 1 context missing from prompt: {prompt_turn_2}"
            )
            assert "What is my favorite color?" in prompt_turn_2, (
                f"Turn 2 missing from prompt: {prompt_turn_2}"
            )

            # Check database state: exactly 4 messages stored (Turn 1 Q&A + Turn 2 Q&A)
            history = db.get_messages_by_conversation(session_id, order="ASC")
            assert len(history) == 4, f"Expected 4 messages in DB, got {len(history)}"
            assert "favorite color is teal" in history[0]["content"]
            assert "What is my favorite color?" in history[2]["content"]

    def test_gateway_conversations_endpoint_retrieval(self, gateway_server):
        """
        Verify GET /v1/conversations and GET /v1/conversations/{conversation_id}.
        """
        cid = f"test_conv_api_{int(time.time()*1000)}"
        db.create_conversation(conv_id=cid, title="Gateway Conversation Test")
        db.create_message(conversation_id=cid, role="user", content="Test question 1")
        db.create_message(conversation_id=cid, role="assistant", content="Test answer 1")

        # 1. Test GET /v1/conversations (list)
        req_list = urllib.request.Request(f"{gateway_server}/v1/conversations")
        with urllib.request.urlopen(req_list, timeout=5) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "conversations" in data
            assert "threads" in data
            # Check thread is present in thread list
            thread_ids = [t["id"] for t in data["threads"]]
            assert cid in thread_ids

        # 2. Test GET /v1/conversations/{id} (individual)
        req_single = urllib.request.Request(f"{gateway_server}/v1/conversations/{cid}")
        with urllib.request.urlopen(req_single, timeout=5) as resp:
            assert resp.status == 200
            single_data = json.loads(resp.read().decode("utf-8"))
            assert "conversation" in single_data
            assert single_data["conversation"]["id"] == cid
            assert "messages" in single_data
            assert len(single_data["messages"]) == 2
            assert single_data["messages"][0]["content"] == "Test question 1"
            assert single_data["messages"][1]["content"] == "Test answer 1"

        # 3. Test non-existent conversation returns JSON error
        req_nonexistent = urllib.request.Request(f"{gateway_server}/v1/conversations/non_existent_conv_xyz")
        with urllib.request.urlopen(req_nonexistent, timeout=5) as resp:
            assert resp.status == 200
            err_data = json.loads(resp.read().decode("utf-8"))
            assert "error" in err_data
            assert err_data["error"] == "Conversation not found"

    def test_backward_compatibility_session_id_vs_conversation_id(self, gateway_server):
        """
        Verify backward compatibility for session_id vs conversation_id:
        1. X-Session-ID header
        2. session_id in JSON payload
        3. conversation_id in JSON payload
        4. canonical message dicts contain both session_id and conversation_id
        """
        # Sub-test A: X-Session-ID header
        header_sid = f"hdr_sess_{int(time.time()*1000)}"
        with patch("agent.modules.gateway_server.router.complete_text", return_value={"text": "OK", "provider": "arena", "canonical_model": "claude-3-5-sonnet"}):
            req_a = {"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "Header session test"}]}
            http_req_a = urllib.request.Request(
                f"{gateway_server}/v1/chat/completions",
                data=json.dumps(req_a).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Session-ID": header_sid}
            )
            with urllib.request.urlopen(http_req_a, timeout=5) as resp:
                assert resp.status == 200

            # Verify persisted under header_sid
            history_a = db.get_messages_by_conversation(header_sid)
            assert len(history_a) >= 1
            assert history_a[0]["content"] == "Header session test"

        # Sub-test B: session_id in payload
        payload_sid = f"payload_sess_{int(time.time()*1000)}"
        with patch("agent.modules.gateway_server.router.complete_text", return_value={"text": "OK", "provider": "arena", "canonical_model": "claude-3-5-sonnet"}):
            req_b = {
                "session_id": payload_sid,
                "model": "claude-3-5-sonnet",
                "messages": [{"role": "user", "content": "Payload session_id test"}]
            }
            http_req_b = urllib.request.Request(
                f"{gateway_server}/v1/chat/completions",
                data=json.dumps(req_b).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(http_req_b, timeout=5) as resp:
                assert resp.status == 200

            history_b = db.get_messages_by_conversation(payload_sid)
            assert len(history_b) >= 1
            assert history_b[0]["content"] == "Payload session_id test"

        # Sub-test C: Check canonical message and conversation dicts have both session_id and conversation_id
        msg = CanonicalMessage(conversation_id="conv_dual_key", role="user", content="Dual key test")
        msg_dict = msg.to_dict()
        assert "conversation_id" in msg_dict
        assert "session_id" in msg_dict
        assert msg_dict["conversation_id"] == "conv_dual_key"
        assert msg_dict["session_id"] == "conv_dual_key"

        conv_dict = get_conversation(header_sid)
        assert conv_dict is not None
        assert "id" in conv_dict
        assert "session_id" in conv_dict
        assert conv_dict["session_id"] == header_sid

    def test_gateway_chat_completions_conversation_id_field_support(self, gateway_server):
        """
        Verify that clients sending 'conversation_id' in JSON body correctly route
        to that conversation thread (no longer falls back to default_session).
        """
        payload_cid = f"payload_conv_{int(time.time()*1000)}"
        with patch("agent.modules.gateway_server.router.complete_text", return_value={"text": "OK", "provider": "arena", "canonical_model": "claude-3-5-sonnet"}):
            req = {
                "conversation_id": payload_cid,
                "model": "claude-3-5-sonnet",
                "messages": [{"role": "user", "content": "Payload conversation_id test"}]
            }
            http_req = urllib.request.Request(
                f"{gateway_server}/v1/chat/completions",
                data=json.dumps(req).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(http_req, timeout=5) as resp:
                assert resp.status == 200

            history = db.get_messages_by_conversation(payload_cid)
            assert len(history) >= 1, (
                f"Gateway failed to associate conversation_id in body! Messages were not stored under {payload_cid}."
            )
            assert history[0]["content"] == "Payload conversation_id test"


# ============================================================================
# Test Suite 3: Boundary & Error Condition Fuzzing
# ============================================================================

class TestBoundaryAndErrorFuzzing:
    """
    Fuzzing and stress testing:
    - Non-existent IDs
    - Malformed JSON metadata
    - Special unicode, RTL, CJK, emoji, and control chars
    - Very long messages (up to 200k chars)
    - Database concurrency under load
    """

    def test_nonexistent_conversation_ids_safe_degradation(self):
        bogus_id = "non_existent_id_9999999999"

        # 1. get_conversation returns None
        assert get_conversation(bogus_id) is None

        # 2. get_conversation_messages returns empty list
        assert get_conversation_messages(bogus_id, target_format="canonical") == []
        assert get_conversation_messages(bogus_id, target_format="openai") == []
        msgs, sys = get_conversation_messages(bogus_id, target_format="anthropic")
        assert msgs == []
        assert sys is None

        # 3. export_conversation_for_provider produces valid empty payload without throwing
        exp_openai = export_conversation_for_provider(bogus_id, provider="openai", model="gpt-4o")
        assert exp_openai["messages"] == []
        exp_anthropic = export_conversation_for_provider(bogus_id, provider="anthropic", model="claude-3-5-sonnet")
        assert exp_anthropic["messages"] == []

        # 4. delete_conversation returns False
        assert delete_conversation(bogus_id) is False

    def test_malformed_and_exotic_metadata_handling(self):
        cid = f"test_meta_fuzz_{int(time.time()*1000)}"

        # Valid nested dictionary metadata
        valid_meta = {
            "tags": ["alpha", "beta"],
            "nested": {"score": 98.6, "verified": True},
            "unicode_key_🔑": "unicode_val_🚀"
        }
        db.create_conversation(conv_id=cid, metadata=valid_meta)
        conv = get_conversation(cid)
        assert conv["metadata"]["nested"]["score"] == 98.6
        assert conv["metadata"]["unicode_key_🔑"] == "unicode_val_🚀"

        # Direct DB corrupt metadata (e.g. invalid string injected directly into table)
        with db.get_db() as conn:
            conn.execute("UPDATE conversations SET metadata = ? WHERE id = ?", ("{broken: json, not valid!}", cid))

        # System should not crash when reading corrupt metadata; it must safely fall back to {}
        conv_corrupted = get_conversation(cid)
        assert conv_corrupted is not None
        assert conv_corrupted["metadata"] == {}

        delete_conversation(cid)

    def test_special_unicode_cjk_rtl_math_roundtrip(self):
        cid = f"test_unicode_fuzz_{int(time.time()*1000)}"

        test_payloads = [
            # Emojis & Modifiers
            "🔥🚀🤖✨🎉👍🏽👩‍💻",
            # RTL languages (Arabic, Hebrew)
            "مرحبا بك في عالم الذكاء الاصطناعي! שלום עולם!",
            # CJK scripts (Chinese, Japanese, Korean)
            "这是一段中文测试。これは日本語のテストです。한국어 테스트입니다.",
            # Mathematical symbols and Greek alphabet
            "∀x ∈ ℝ, ∃y: x² + y² = 1; ∫₀^∞ e^(-x) dx = 1; α, β, γ, δ, ε, θ, λ, μ, π, σ, ω, ∑, ∏, √",
            # Whitespace & Control sequences (excluding C-style null bytes)
            "Line 1\r\nLine 2\tTabbed\n\n\nTripleNewline   Spaces   \u200BZeroWidthSpace"
        ]

        for idx, text in enumerate(test_payloads):
            save_message(
                conversation_id=cid,
                role="user" if idx % 2 == 0 else "assistant",
                content=text,
                model="claude-3-5-sonnet",
                provider="arena"
            )

        # Verify Canonical roundtrip
        canonical_msgs = get_conversation_messages(cid, target_format="canonical")
        assert len(canonical_msgs) == len(test_payloads)
        for original, retrieved in zip(test_payloads, canonical_msgs):
            assert retrieved["content"] == original

        # Verify OpenAI format export roundtrip
        openai_export = export_conversation_for_provider(cid, provider="openai", model="gpt-4o")
        for original, exported in zip(test_payloads, openai_export["messages"]):
            assert exported["content"] == original

        delete_conversation(cid)

    def test_large_message_payload_stress(self):
        """
        Stress-tests large content (100k+ chars) including thinking content.
        Verifies regex, SQLite, and format converters handle high-volume text.
        """
        cid = f"test_large_payload_{int(time.time()*1000)}"

        # Construct 100,000 char thinking block + 50,000 char text body
        large_thinking = "Deep thought step. " * 5000  # ~95,000 chars
        large_content = "This is a comprehensive response to the question. " * 1000  # ~50,000 chars
        composite_body = f"<think>\n{large_thinking}\n</think>\n\n{large_content}"

        # Test A: extract_thinking_tags stress on 150k char text (verifying no ReDoS or recursion crash)
        from agent.modules.conversation_store import extract_thinking_tags
        clean_text, extracted_think = extract_thinking_tags(composite_body)
        assert clean_text == large_content.strip()
        assert extracted_think == large_thinking.strip()

        # Test B: Ingest via universal translate_messages bridge (OpenAI format)
        openai_input = [
            {"role": "user", "content": "Generate a massive reasoning trace."},
            {"role": "assistant", "content": composite_body}
        ]
        canonical = translate_messages(openai_input, from_format="openai", to_format="canonical")
        assert len(canonical) == 2
        assert canonical[1].thinking_content == large_thinking.strip()
        assert canonical[1].content == large_content.strip()

        # Test C: Direct store persistence of 100k+ chars with isolated thinking
        save_message(
            conversation_id=cid,
            role="user",
            content="Generate a massive reasoning trace."
        )
        save_message(
            conversation_id=cid,
            role="assistant",
            content=large_content,
            thinking_content=large_thinking,
            model="deepseek-v3",
            provider="deepinfra"
        )

        conv = get_conversation(cid)
        assert conv["message_count"] == 2

        # Export for OpenAI: verify thinking was stripped and text retained without truncation
        openai_exp = export_conversation_for_provider(cid, provider="openai", model="gpt-4o")
        asst_msg = openai_exp["messages"][1]["content"]
        assert "<think>" not in asst_msg
        assert "Deep thought step" not in asst_msg
        assert "This is a comprehensive response" in asst_msg
        assert len(asst_msg) >= 49000

        delete_conversation(cid)

    def test_concurrent_message_writes(self):
        """
        Verify SQLite WAL mode and busy timeout under concurrent multi-threaded write load.
        """
        cid = f"test_concurrency_{int(time.time()*1000)}"
        save_conversation(cid, title="Concurrency Stress Test")

        errors = []

        def worker_task(thread_id: int):
            try:
                for i in range(10):
                    save_message(
                        conversation_id=cid,
                        role="user" if i % 2 == 0 else "assistant",
                        content=f"Thread {thread_id} - Iteration {i}",
                        model="claude-3-5-sonnet",
                        provider="arena"
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker_task, args=(tid,)) for tid in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Encountered concurrency errors: {errors}"
        conv = get_conversation(cid)
        # 8 threads * 10 iterations = 80 messages
        assert conv["message_count"] == 80

        delete_conversation(cid)
