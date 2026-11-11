"""
test_boundaries_prompts.py — Tier 2 Boundary Tests for Prompts and Message Content.

Covers:
  - Empty messages list, blank content, whitespace-only content
  - Oversized prompts (100k+ characters)
  - Multi-modal content blocks (text + image + unrecognized blocks)
  - Complex Unicode, emojis, multilingual scripts, and C0/C1 control characters
  - Deeply nested JSON payloads
  - System and developer prompts (empty, oversized, o1/o3 developer role)
  - Thinking blocks & <think> tag isolation
  - Consecutive same-role turns & assistant prefill
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
import pytest
import requests

from agent.modules.conversation_store import (
    THINK_REGEX,
    CanonicalMessage,
    CanonicalRole,
    export_conversation_for_provider
)


# ─── Empty, Blank, and Whitespace-Only Prompts ────────────────────────────────

def test_empty_messages_list_anthropic(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test sending empty messages list [] to /v1/messages."""
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [],
        "max_tokens": 100
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code in (200, 400)
    data = resp.json()
    assert isinstance(data, dict)


def test_empty_messages_list_openai(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test sending empty messages list [] to /v1/chat/completions."""
    payload = {
        "model": "gpt-4o",
        "messages": [],
        "max_tokens": 100
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code in (200, 400)


def test_blank_string_content_user(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test user message with empty string content: content=""."""
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": ""}],
        "max_tokens": 100
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data


def test_whitespace_only_content_anthropic(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test message containing only whitespace characters (spaces, tabs, newlines)."""
    payload = {
        "model": "claude-3-5-haiku",
        "messages": [{"role": "user", "content": "   \n\t   \r\n   "}],
        "max_tokens": 50
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "message"


def test_whitespace_only_content_openai(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test OpenAI message with whitespace only content."""
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "\t\t\n\n   "}],
        "max_tokens": 50
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "choices" in data


# ─── Oversized Prompts (100k+ chars) ──────────────────────────────────────────

def test_oversized_prompt_100k_chars_anthropic(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test 100,000+ character string payload passed to /v1/messages."""
    large_text = "The quick brown fox jumps over the lazy dog. " * 2500  # ~112,500 chars
    assert len(large_text) > 100000

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": large_text}],
        "max_tokens": 100
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=15.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "message"
    assert "content" in data


def test_oversized_prompt_100k_chars_openai(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test 100,000+ character string payload passed to /v1/chat/completions."""
    large_text = "Benchmark multi-router cascade payload stress testing. " * 2000  # ~110,000 chars
    assert len(large_text) > 100000

    payload = {
        "model": "deepseek-v3",
        "messages": [{"role": "user", "content": large_text}],
        "max_tokens": 100
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=15.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "choices" in data


# ─── Multi-Modal Content Blocks ───────────────────────────────────────────────

def test_multimodal_content_blocks_text_and_image(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test Anthropic multi-modal content blocks containing text and base64 image."""
    base64_pixel = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY44YAAAAASUVORK5CYII="
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Analyze the attached single pixel image:"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64_pixel
                    }
                }
            ]
        }],
        "max_tokens": 100
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data


def test_multimodal_unrecognized_block_type(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test handling unrecognized content block types without crashing."""
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "unknown_future_sensor_telemetry", "data": {"accelerometer": [0.1, 0.2, 9.8]}},
                {"type": "text", "text": "Process this telemetry data"}
            ]
        }],
        "max_tokens": 100
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data


# ─── Unicode, Emojis, Multilingual Scripts & Control Characters ───────────────

def test_unicode_emojis_and_flags_prompt(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test Unicode emojis, ZWJ sequences, skin tones, and country flags."""
    emoji_prompt = "Hello 🚀 🤖 🧑🏽‍💻 🔥 🌐 🏳️‍🌈 🇦🇶 🇺🇳! Calculate 2 + 2 🧮."
    payload = {
        "model": "claude-3-5-haiku",
        "messages": [{"role": "user", "content": emoji_prompt}],
        "max_tokens": 50
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data


def test_unicode_surrogates_and_multilingual_scripts(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test multilingual scripts: Chinese, Japanese, Arabic, Devanagari, Hebrew, Cyrillic, and Ogham."""
    multilingual = "日本語: こんにちは, 中文: 你好, हिन्दी: नमस्ते, العربية: مرحباً, עברית: שלום, Русский: Привет, Ogham: ᚛ᚑᚌᚐᚋ᚜"
    payload = {
        "model": "mistral-large-2411",
        "messages": [{"role": "user", "content": multilingual}],
        "max_tokens": 100
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "choices" in data


def test_c0_c1_control_characters_in_prompt(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test strings containing control characters (bell, backspace, formfeed, escape)."""
    ctrl_prompt = "Header\x07Text with backspace\x08 and formfeed\x0c and escape\x1b[31mRed\x1b[0m"
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": ctrl_prompt}],
        "max_tokens": 50
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data


# ─── Deeply Nested JSON & Structured Prompts ──────────────────────────────────

def test_deeply_nested_json_in_prompt_content(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test prompt containing deeply nested JSON structure (25 levels deep)."""
    nested = {"val": "leaf"}
    for i in range(25):
        nested = {f"level_{i}": nested, "index": i}
    json_str = json.dumps(nested)

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": f"Parse this nested JSON: {json_str}"}],
        "max_tokens": 100
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "choices" in data


# ─── System & Developer Role Prompts ──────────────────────────────────────────

def test_system_prompt_empty_string(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test sending empty string system prompt system='' to /v1/messages."""
    payload = {
        "model": "claude-3-5-sonnet",
        "system": "",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 50
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200


def test_system_prompt_large_50k_chars(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test large system prompt (50,000+ characters)."""
    large_sys = "You are a specialized enterprise AI system. " * 1200  # ~52,800 chars
    payload = {
        "model": "claude-3-5-sonnet",
        "system": large_sys,
        "messages": [{"role": "user", "content": "Acknowledge system prompt"}],
        "max_tokens": 50
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=10.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "message"


def test_developer_role_message_openai(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test OpenAI o1/o3 'developer' role message in /v1/chat/completions."""
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "developer", "content": "Always respond in JSON format."},
            {"role": "user", "content": "Return status OK"}
        ],
        "max_tokens": 50
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "choices" in data


# ─── Thinking Blocks & Turn Edge Cases ────────────────────────────────────────

def test_thinking_content_block_anthropic(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test Anthropic extended thinking content block in multi-turn conversation."""
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "What is 17 * 19?"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Calculating: 17 * 20 = 340, minus 17 = 323."},
                    {"type": "text", "text": "17 * 19 is 323."}
                ]
            },
            {"role": "user", "content": "Now add 10."}
        ],
        "max_tokens": 100
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data


def test_deepseek_think_tag_parsing():
    """Verify DeepSeek <think>...</think> extraction via THINK_REGEX."""
    raw_response = "<think>\nStep 1: Analyze user request.\nStep 2: Formulate solution.\n</think>\nHere is the final answer."
    matches = THINK_REGEX.findall(raw_response)
    assert len(matches) == 1
    assert "Step 1: Analyze user request." in matches[0]

    # Cleaned visible text
    cleaned_text = THINK_REGEX.sub("", raw_response).strip()
    assert cleaned_text == "Here is the final answer."


def test_null_content_in_message_payload(e2e_client: requests.Session, openai_headers: Dict[str, str]):
    """Test message with content=None (null) in /v1/chat/completions."""
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": None}],
        "max_tokens": 50
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/chat/completions",
        headers=openai_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code in (200, 400)


def test_consecutive_same_role_messages(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test consecutive same-role messages (User -> User) coalescing without error."""
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "First message from user."},
            {"role": "user", "content": "Follow-up addition from same user."}
        ],
        "max_tokens": 100
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data


def test_trailing_assistant_prefill_anthropic(e2e_client: requests.Session, anthropic_headers: Dict[str, str]):
    """Test Anthropic prompt ending with assistant prefill: {'role': 'assistant', 'content': '{'}."""
    payload = {
        "model": "claude-3-5-haiku",
        "messages": [
            {"role": "user", "content": "Output status JSON:"},
            {"role": "assistant", "content": "{"}
        ],
        "max_tokens": 50
    }
    resp = e2e_client.post(
        f"{e2e_client.gateway_url}/v1/messages",
        headers=anthropic_headers,
        json=payload,
        timeout=5.0
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data
