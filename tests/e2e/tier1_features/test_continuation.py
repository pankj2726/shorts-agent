"""
test_continuation.py — Feature 8: Cross-Model Conversation Continuation.

Validates that conversations started on one provider (e.g. Arena.AI or Claude)
can be exported, formatted, and resumed seamlessly on another provider (e.g. ChatGPT / OpenAI)
using centralized persistent conversation context without amnesia or format corruption.
"""

from __future__ import annotations

import pytest

from agent.modules import db
from agent.modules.conversation_store import export_conversation_for_provider


@pytest.fixture(autouse=True)
def align_db_schema(e2e_db):
    """Ensure database schema has required columns for agent.modules.db compatibility."""
    for col in ["default_provider", "default_model"]:
        try:
            e2e_db.execute(f"ALTER TABLE conversations ADD COLUMN {col} TEXT")
        except Exception:
            pass
    try:
        e2e_db.execute("ALTER TABLE messages ADD COLUMN thinking_content TEXT")
    except Exception:
        pass


def test_continuation_export_for_openai_after_anthropic(e2e_db):
    """Verify an Anthropic conversation with thinking is cleanly exported for an OpenAI client."""
    conv_id = "conv_cross_anthropic_to_openai"
    db.create_conversation(conv_id=conv_id, title="Anthropic Initial")

    db.create_message(
        conversation_id=conv_id,
        role="user",
        content="Write a 15-second hook about AI.",
        provider="anthropic",
        model="claude-3-5-sonnet"
    )
    db.create_message(
        conversation_id=conv_id,
        role="assistant",
        content="Here is your hook: The machines are no longer learning, they are teaching.",
        provider="anthropic",
        model="claude-3-5-sonnet",
        thinking_content="Evaluate emotional resonance and retention."
    )

    export_payload = export_conversation_for_provider(conv_id, provider="openai", model="gpt-4o")

    assert "messages" in export_payload
    openai_msgs = export_payload["messages"]
    assert len(openai_msgs) == 2
    assert openai_msgs[0]["role"] == "user"
    assert "Write a 15-second hook" in openai_msgs[0]["content"]
    assert openai_msgs[1]["role"] == "assistant"
    assert "Here is your hook" in openai_msgs[1]["content"]


def test_continuation_export_for_anthropic_after_openai(e2e_db):
    """Verify an OpenAI conversation with system prompt is exported with system at top-level."""
    conv_id = "conv_cross_openai_to_anthropic"
    db.create_conversation(conv_id=conv_id, title="OpenAI Initial")

    db.create_message(
        conversation_id=conv_id,
        role="system",
        content="You are an elite short-form scriptwriter.",
        provider="openai",
        model="gpt-4o"
    )
    db.create_message(
        conversation_id=conv_id,
        role="user",
        content="Create 3 angles on quantum computing.",
        provider="openai",
        model="gpt-4o"
    )

    export_payload = export_conversation_for_provider(
        conv_id, provider="anthropic", model="claude-3-5-haiku"
    )

    assert "system" in export_payload
    assert "You are an elite short-form scriptwriter." in export_payload["system"]
    assert "messages" in export_payload
    anthropic_msgs = export_payload["messages"]
    assert len(anthropic_msgs) == 1
    assert anthropic_msgs[0]["role"] == "user"
    assert anthropic_msgs[0]["content"] == "Create 3 angles on quantum computing."


def test_continuation_multi_turn_history_preserved(e2e_db):
    """Verify multi-turn history spanning multiple providers is preserved in chronological order."""
    conv_id = "conv_multi_turn_federated"
    db.create_conversation(conv_id=conv_id, title="Federated Multi-Turn")

    # Turn 1: Arena
    db.create_message(conv_id, "user", "Turn 1 question", provider="arena", model="claude-coding-router-preview")
    db.create_message(conv_id, "assistant", "Turn 1 answer", provider="arena", model="claude-coding-router-preview")

    # Turn 2: Anthropic
    db.create_message(conv_id, "user", "Turn 2 question", provider="anthropic", model="claude-3-5-sonnet")
    db.create_message(conv_id, "assistant", "Turn 2 answer", provider="anthropic", model="claude-3-5-sonnet")

    # Turn 3: OpenRouter
    db.create_message(conv_id, "user", "Turn 3 question", provider="openrouter", model="deepseek-v3")
    db.create_message(conv_id, "assistant", "Turn 3 answer", provider="openrouter", model="deepseek-v3")

    export_payload = export_conversation_for_provider(conv_id, provider="openai", model="o3-mini")
    msgs = export_payload["messages"]

    assert len(msgs) == 6
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant", "user", "assistant"]
    assert msgs[0]["content"] == "Turn 1 question"
    assert msgs[-1]["content"] == "Turn 3 answer"


def test_continuation_system_turn_injection(e2e_db):
    """Verify system instructions injected mid-conversation are safely converted for Anthropic."""
    conv_id = "conv_midstream_system"
    db.create_conversation(conv_id=conv_id, title="System Injection")

    db.create_message(conv_id, "user", "Initial request", provider="openai", model="gpt-4o")
    db.create_message(conv_id, "system", "Change tone to sarcastic.", provider="system", model="rule")
    db.create_message(conv_id, "user", "Continue now.", provider="openai", model="gpt-4o")

    export_payload = export_conversation_for_provider(conv_id, provider="anthropic", model="claude-3-5-sonnet")
    msgs = export_payload["messages"]

    # For Anthropic, all items in messages list must be user or assistant
    roles = {m["role"] for m in msgs}
    assert roles.issubset({"user", "assistant"})


def test_continuation_handles_missing_conversation(e2e_db):
    """Verify export_conversation_for_provider returns safe empty payload for missing conversation ID."""
    export_payload = export_conversation_for_provider("conv_non_existent_999", provider="openai", model="gpt-4o")
    assert isinstance(export_payload, dict)
    assert export_payload.get("messages") == []
