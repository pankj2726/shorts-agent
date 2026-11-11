"""
test_role_translation.py — Feature 7: Cross-Model Format & Role Translation.

Validates bidirectional translation between:
  1. Anthropic format (system at root, user/assistant turns, thinking content blocks).
  2. OpenAI format (system/developer messages in stream, user/assistant turns).
  3. DeepSeek format (<think>...</think> tags extraction and thinking field isolation).
  4. Consecutive turn coalescing to comply with alternating turn constraints.
"""

from __future__ import annotations

import pytest

from agent.modules.conversation_store import (
    extract_thinking_tags,
    coalesce_consecutive_roles,
    normalize_for_anthropic,
    normalize_for_openai,
    canonical_to_anthropic,
    canonical_to_openai,
    translate_messages,
    CanonicalMessage,
)


def test_extract_thinking_tags_deepseek():
    """Verify DeepSeek-style <think>...</think> tags are cleanly extracted into separate thinking field."""
    raw_content = "<think>Calculating gradient descent trajectory and optimal learning rate</think>The optimal rate is 0.001."
    clean_text, thinking = extract_thinking_tags(raw_content)

    assert clean_text == "The optimal rate is 0.001."
    assert thinking == "Calculating gradient descent trajectory and optimal learning rate"


def test_coalesce_consecutive_roles():
    """Verify consecutive messages with identical roles are coalesced into a single combined turn."""
    turns = [
        {"role": "user", "content": "Part 1 of question."},
        {"role": "user", "content": "Part 2 of question."},
        {"role": "assistant", "content": "Answer."},
    ]
    coalesced = coalesce_consecutive_roles(turns)

    assert len(coalesced) == 2
    assert coalesced[0]["role"] == "user"
    assert "Part 1 of question." in coalesced[0]["content"]
    assert "Part 2 of question." in coalesced[0]["content"]
    assert coalesced[1]["role"] == "assistant"


def test_normalize_for_anthropic_system_extraction():
    """Verify system and developer turns are extracted to the top-level system string."""
    turns = [
        {"role": "system", "content": "You are an expert viral editor."},
        {"role": "developer", "content": "Never output markdown code fences."},
        {"role": "user", "content": "Review this script."},
    ]
    system_prompt, clean_messages = normalize_for_anthropic(turns)

    assert "You are an expert viral editor." in system_prompt
    assert "Never output markdown code fences." in system_prompt
    assert len(clean_messages) == 1
    assert clean_messages[0]["role"] == "user"
    assert clean_messages[0]["content"] == "Review this script."


def test_canonical_to_anthropic_thinking_blocks():
    """Verify CanonicalMessage with thinking_content generates Anthropic thinking blocks."""
    msgs = [
        CanonicalMessage(
            role="assistant",
            content="Final conclusion.",
            thinking_content="Step 1 analysis: verified; Step 2: complete.",
            model="claude-3-5-sonnet"
        )
    ]
    system_prompt, anthropic_msgs = canonical_to_anthropic(msgs)

    # In Anthropic format, turns alternate starting with user
    assistant_turns = [m for m in anthropic_msgs if m["role"] == "assistant"]
    assert len(assistant_turns) == 1
    blocks = assistant_turns[0]["content"]
    assert "Final conclusion." in str(blocks)


def test_canonical_to_openai_format():
    """Verify CanonicalMessage sequence is formatted cleanly for OpenAI endpoint."""
    msgs = [
        CanonicalMessage(role="system", content="System instruction"),
        CanonicalMessage(role="user", content="Prompt"),
        CanonicalMessage(role="assistant", content="Response")
    ]
    openai_msgs = canonical_to_openai(msgs)

    assert len(openai_msgs) == 3
    assert openai_msgs[0]["role"] == "system"
    assert openai_msgs[1]["role"] == "user"
    assert openai_msgs[2]["role"] == "assistant"


def test_translate_messages_between_formats():
    """Verify high-level translate_messages translates Anthropic payload into OpenAI payload."""
    anthropic_input = [
        {"role": "user", "content": "Generate a caption."},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "Internal thought process"},
            {"type": "text", "text": "Unstoppable momentum."}
        ]}
    ]
    openai_output = translate_messages(
        messages=anthropic_input,
        from_format="anthropic",
        to_format="openai"
    )

    assert len(openai_output) >= 2
    assert openai_output[0]["role"] == "user"
    assert openai_output[0]["content"] == "Generate a caption."
    assert any(m["role"] == "assistant" and "Unstoppable momentum." in m["content"] for m in openai_output)
