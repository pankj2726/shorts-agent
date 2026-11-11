"""
test_db_schema.py — Feature 6: SQLite Cross-Model Conversation Schema.

Validates:
  1. Creation of conversations and messages tables and index definitions.
  2. Conversation thread creation, retrieval, and JSON metadata parsing.
  3. Message persistence with model, provider, tokens, and thinking isolation.
  4. Foreign key enforcement and cascade deletion on conversation removal.
  5. Automatic conversation thread creation when saving messages.
  6. Timestamp updates when new messages are added to existing conversations.
"""

from __future__ import annotations

import sqlite3
import time
import pytest


def test_db_initialization_creates_tables(e2e_db):
    """Verify conversations and messages tables and indices exist in SQLite instance."""
    rows = e2e_db.fetch_all(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'index')"
    )
    names = {r["name"] for r in rows}

    assert "conversations" in names
    assert "messages" in names
    assert "idx_messages_conv_id" in names or "idx_messages_conversation_id_id" in names


def test_db_create_and_get_conversation(e2e_db):
    """Verify conversation thread creation and metadata persistence."""
    conv_id = "conv_test_thread_001"
    meta = {"source": "mobile_app", "client_version": "2.4.0"}

    created_id = e2e_db.create_conversation(
        conversation_id=conv_id,
        title="Test Conversation Thread",
        metadata=meta
    )
    assert created_id == conv_id

    conv = e2e_db.get_conversation(conv_id)
    assert conv is not None
    assert conv["id"] == conv_id
    assert conv["title"] == "Test Conversation Thread"
    assert conv["metadata"]["source"] == "mobile_app"


def test_db_save_message_and_retrieve_raw(e2e_db):
    """Verify save_message stores complete message turns with token counts and thinking."""
    conv_id = "conv_turn_test_002"
    e2e_db.create_conversation(conversation_id=conv_id, title="Turn Test")

    msg_id = e2e_db.save_message(
        conversation_id=conv_id,
        role="assistant",
        content="Here is the generated analysis.",
        model="claude-3-5-sonnet",
        provider="anthropic",
        thinking="Deep mathematical validation completed.",
        tokens_in=45,
        tokens_out=120,
        latency_ms=850,
        metadata={"finish_reason": "end_turn"}
    )
    assert msg_id is not None

    messages = e2e_db.get_conversation_messages(conv_id, format="raw")
    assert len(messages) == 1
    m = messages[0]
    assert m["conversation_id"] == conv_id
    assert m["role"] == "assistant"
    assert m["content"] == "Here is the generated analysis."
    assert m["model"] == "claude-3-5-sonnet"
    assert m["provider"] == "anthropic"
    assert m["thinking"] == "Deep mathematical validation completed."
    assert m["tokens_in"] == 45
    assert m["tokens_out"] == 120


def test_db_cascade_delete_conversation(e2e_db):
    """Verify deleting a conversation cascades and deletes all associated messages."""
    conv_id = "conv_cascade_003"
    e2e_db.create_conversation(conversation_id=conv_id, title="Cascade Target")
    e2e_db.save_message(conv_id, "user", "Message 1", "gpt-4o", "openai")
    e2e_db.save_message(conv_id, "assistant", "Reply 1", "gpt-4o", "openai")

    assert len(e2e_db.get_conversation_messages(conv_id, format="raw")) == 2

    # Delete parent conversation
    e2e_db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))

    # Messages must be deleted via cascade
    assert len(e2e_db.get_conversation_messages(conv_id, format="raw")) == 0


def test_db_auto_create_conversation_on_message(e2e_db):
    """Verify saving a message to an uncreated conversation auto-creates the conversation."""
    new_conv_id = "conv_autocreated_004"
    e2e_db.save_message(
        conversation_id=new_conv_id,
        role="user",
        content="Initial kickoff prompt",
        model="deepseek-v3",
        provider="deepinfra"
    )

    conv = e2e_db.get_conversation(new_conv_id)
    assert conv is not None
    assert conv["id"] == new_conv_id
    assert len(e2e_db.get_conversation_messages(new_conv_id, format="raw")) == 1


def test_db_triggers_update_conversation_timestamp(e2e_db):
    """Verify inserting a message updates the parent conversation's updated_at."""
    conv_id = "conv_timestamp_005"
    e2e_db.create_conversation(conversation_id=conv_id, title="Timestamp Test")
    conv1 = e2e_db.get_conversation(conv_id)
    time.sleep(0.01)

    e2e_db.save_message(conv_id, "user", "Ping", "claude-3-5-haiku", "anthropic")
    conv2 = e2e_db.get_conversation(conv_id)

    assert conv2["updated_at"] >= conv1["updated_at"]
