"""
conversation_store.py — Unified Cross-Model Conversation Store & Context Bridge.

Provides:
  1. Lossless Canonical message and conversation representation.
  2. Database-backed CRUD operations on top of agent.modules.db.
  3. Format & protocol translators (Anthropic, OpenAI, Arena, Canonical).
  4. Thinking scratchpad isolation and cryptographic signature preservation.
  5. Cross-model continuation export with turn coalescing and system extraction.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from agent.modules import db

logger = logging.getLogger("ConversationStore")

# Regex for DeepSeek-style <think>...</think> tags
THINK_REGEX = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


# ============================================================================
# Canonical Data Structures
# ============================================================================

class CanonicalRole(str, Enum):
    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    FUNCTION = "function"


@dataclass
class CanonicalContentBlock:
    type: str  # "text", "image", "tool_use", "tool_result", "thinking"
    text: Optional[str] = None
    media_type: Optional[str] = None
    data: Optional[str] = None
    tool_use_id: Optional[str] = None
    name: Optional[str] = None
    input: Optional[Dict[str, Any]] = None
    is_error: Optional[bool] = None


@dataclass
class CanonicalMessage:
    id: Optional[Union[str, int]] = None
    conversation_id: Optional[str] = None
    role: str = CanonicalRole.USER.value
    content: str = ""
    content_blocks: List[Dict[str, Any]] = field(default_factory=list)
    thinking_content: Optional[str] = None
    thinking_signature: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    reasoning_effort: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    latency_ms: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Ensure session_id alias for backward compatibility
        d["session_id"] = self.conversation_id
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CanonicalMessage:
        meta = data.get("metadata", {})
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        cid = data.get("conversation_id") or data.get("session_id")
        return cls(
            id=data.get("id"),
            conversation_id=cid,
            role=data.get("role", CanonicalRole.USER.value),
            content=data.get("content", ""),
            content_blocks=data.get("content_blocks", []),
            thinking_content=data.get("thinking_content"),
            thinking_signature=data.get("thinking_signature"),
            model=data.get("model"),
            provider=data.get("provider"),
            reasoning_effort=data.get("reasoning_effort") or meta.get("reasoning_effort"),
            tokens_in=data.get("tokens_in") or meta.get("prompt_tokens"),
            tokens_out=data.get("tokens_out") or meta.get("completion_tokens"),
            latency_ms=data.get("latency_ms") or meta.get("latency_ms"),
            metadata=meta,
            created_at=data.get("created_at")
        )


@dataclass
class CanonicalConversation:
    id: str
    title: Optional[str] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    system_prompt: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    messages: List[CanonicalMessage] = field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.id,
            "title": self.title,
            "default_provider": self.default_provider,
            "default_model": self.default_model,
            "system_prompt": self.system_prompt,
            "metadata": self.metadata,
            "messages": [m.to_dict() for m in self.messages],
            "message_count": len(self.messages),
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CanonicalConversation:
        meta = data.get("metadata", {})
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        raw_msgs = data.get("messages", [])
        msgs = [CanonicalMessage.from_dict(m) if isinstance(m, dict) else m for m in raw_msgs]
        return cls(
            id=data.get("id") or data.get("session_id", ""),
            title=data.get("title"),
            default_provider=data.get("default_provider"),
            default_model=data.get("default_model"),
            system_prompt=data.get("system_prompt"),
            metadata=meta,
            messages=msgs,
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at")
        )


# ============================================================================
# Thinking & Sanitization Helpers
# ============================================================================

def extract_thinking_tags(text: str) -> Tuple[str, Optional[str]]:
    """
    Extracts DeepSeek-style <think>...</think> tags from text content.
    Returns (cleaned_content, extracted_thinking_content).
    """
    if not isinstance(text, str):
        return str(text), None

    match = THINK_REGEX.search(text)
    if not match:
        return text, None

    thinking = match.group(1).strip()
    cleaned = THINK_REGEX.sub("", text).strip()
    return cleaned, thinking


def coalesce_consecutive_roles(
    messages: List[Dict[str, Any]],
    separator: str = "\n\n"
) -> List[Dict[str, Any]]:
    """
    Coalesces contiguous messages sharing the exact same role into a single turn.
    Preserves thinking blocks and content blocks where applicable.
    """
    if not messages:
        return []

    coalesced: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if coalesced and coalesced[-1].get("role") == role:
            prev = coalesced[-1]
            prev_content = prev.get("content", "")
            if isinstance(prev_content, str) and isinstance(content, str):
                prev["content"] = f"{prev_content}{separator}{content}" if prev_content else content
            elif isinstance(prev_content, list) or isinstance(content, list):
                p_blocks = prev_content if isinstance(prev_content, list) else [{"type": "text", "text": str(prev_content)}]
                c_blocks = content if isinstance(content, list) else [{"type": "text", "text": str(content)}]
                prev["content"] = p_blocks + c_blocks

            # Preserve thinking attributes if missing from previous
            if msg.get("thinking_content") and not prev.get("thinking_content"):
                prev["thinking_content"] = msg.get("thinking_content")
                prev["thinking_signature"] = msg.get("thinking_signature")
        else:
            coalesced.append(dict(msg))

    return coalesced


# ============================================================================
# Protocol Normalizers
# ============================================================================

def normalize_for_anthropic(
    messages: List[Dict[str, Any]],
    default_system: Optional[str] = None,
    dummy_user_prompt: str = "[Resumed conversation]"
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Normalizes a message sequence to strictly satisfy Anthropic Messages API constraints:
      1. All initial 'system' and 'developer' messages extracted into top-level system parameter.
      2. Mid-conversation system turns adapted to user notes.
      3. Consecutive turns with identical roles coalesced.
      4. The conversation guaranteed to start with a 'user' turn.
      5. Empty contents sanitized to non-empty string.
    Returns:
        (consolidated_system_prompt, clean_alternating_messages)
    """
    system_parts: List[str] = []
    if default_system and default_system.strip():
        system_parts.append(default_system.strip())

    filtered_messages: List[Dict[str, Any]] = []
    has_seen_user_turn = False

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")

        if role in ("system", "developer"):
            if not has_seen_user_turn:
                if isinstance(content, str) and content.strip():
                    system_parts.append(content.strip())
            else:
                # Mid-stream system message -> convert to user note
                filtered_messages.append({
                    "role": "user",
                    "content": f"[System Update: {content}]"
                })
        else:
            if role == "user":
                has_seen_user_turn = True
            filtered_messages.append(dict(m))

    # Coalesce consecutive same roles
    coalesced = coalesce_consecutive_roles(filtered_messages)

    # Ensure conversation starts with 'user'
    if coalesced and coalesced[0].get("role") == "assistant":
        coalesced.insert(0, {
            "role": "user",
            "content": dummy_user_prompt
        })

    # Sanitize empty contents
    for m in coalesced:
        c = m.get("content")
        if isinstance(c, str) and not c.strip():
            m["content"] = " "

    consolidated_system = "\n\n".join(system_parts) if system_parts else None
    return consolidated_system, coalesced


def normalize_for_openai(
    messages: List[Dict[str, Any]],
    target_model: str = "",
    target_provider: str = "openai",
    default_system: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Normalizes a message sequence for OpenAI and OpenAI-compatible providers:
      1. Prepend default_system if provided and no initial system message exists.
      2. Resolves 'developer' vs 'system' role:
         - o1 / o3-mini on OpenAI -> map 'system' to 'developer'.
         - Groq, Cerebras, DeepInfra, OpenRouter, Arena -> strictly map 'developer' to 'system'.
         - Standard OpenAI models (gpt-4o) -> map 'developer' to 'system'.
      3. Strips Anthropic-specific structured thinking blocks from content objects.
    """
    prov_clean = (target_provider or "openai").lower()
    is_reasoning_openai = (
        prov_clean == "openai" and any(r in target_model.lower() for r in ("o1", "o3"))
    )

    formatted: List[Dict[str, Any]] = []

    if default_system and (not messages or messages[0].get("role") not in ("system", "developer")):
        init_role = "developer" if is_reasoning_openai else "system"
        formatted.append({"role": init_role, "content": default_system.strip()})

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")

        # Role translation
        if role in ("system", "developer"):
            target_role = "developer" if is_reasoning_openai else "system"
        else:
            target_role = role

        # Clean content from structured thinking blocks
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            content_str = "".join(text_parts)
        else:
            content_str = str(content)

        formatted.append({
            "role": target_role,
            "content": content_str
        })

    return formatted


# ============================================================================
# Bidirectional Format Converters
# ============================================================================

def canonical_to_anthropic(
    canonical_messages: List[CanonicalMessage],
    default_system: Optional[str] = None,
    target_model: str = "claude-3-5-sonnet",
    preserve_thinking: bool = True
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Translates a list of CanonicalMessage objects into Anthropic's wire format.
    Returns (consolidated_system, clean_messages).
    """
    is_claude_37 = "3-7" in target_model or "sonnet-3-7" in target_model

    raw_list: List[Dict[str, Any]] = []
    for cm in canonical_messages:
        item: Dict[str, Any] = {
            "role": cm.role,
            "content": cm.content
        }

        # Extended thinking handling for Claude 3.7
        if (
            cm.role == CanonicalRole.ASSISTANT.value
            and is_claude_37
            and preserve_thinking
            and cm.thinking_content
            and cm.thinking_signature
        ):
            item["content"] = [
                {
                    "type": "thinking",
                    "thinking": cm.thinking_content,
                    "signature": cm.thinking_signature
                },
                {
                    "type": "text",
                    "text": cm.content
                }
            ]
        raw_list.append(item)

    return normalize_for_anthropic(raw_list, default_system=default_system)


def anthropic_to_canonical(
    messages: List[Dict[str, Any]],
    system: Optional[str] = None,
    model: Optional[str] = None,
    provider: str = "anthropic"
) -> List[CanonicalMessage]:
    """
    Translates Anthropic Messages API payload into CanonicalMessage objects.
    Extracts top-level system, text blocks, and extended thinking blocks with signatures.
    """
    results: List[CanonicalMessage] = []

    if system:
        results.append(CanonicalMessage(
            role=CanonicalRole.SYSTEM.value,
            content=system.strip(),
            model=model,
            provider=provider
        ))

    for m in messages:
        role = m.get("role", "user")
        raw_content = m.get("content", "")

        text_content = ""
        thinking_content = None
        thinking_signature = None
        blocks: List[Dict[str, Any]] = []

        if isinstance(raw_content, str):
            text_content = raw_content
        elif isinstance(raw_content, list):
            for b in raw_content:
                if not isinstance(b, dict):
                    continue
                b_type = b.get("type")
                if b_type == "text":
                    text_content += b.get("text", "")
                    blocks.append(b)
                elif b_type == "thinking":
                    thinking_content = b.get("thinking")
                    thinking_signature = b.get("signature")
                    blocks.append(b)
                elif b_type in ("image", "tool_use", "tool_result"):
                    blocks.append(b)

        results.append(CanonicalMessage(
            role=role,
            content=text_content,
            content_blocks=blocks,
            thinking_content=thinking_content,
            thinking_signature=thinking_signature,
            model=model,
            provider=provider
        ))

    return results


def canonical_to_openai(
    canonical_messages: List[CanonicalMessage],
    target_model: str = "",
    target_provider: str = "openai",
    default_system: Optional[str] = None,
    include_thinking_in_text: bool = False
) -> List[Dict[str, Any]]:
    """
    Translates a list of CanonicalMessage objects into OpenAI chat completion messages.
    Thinking content is isolated and omitted from prompt text unless include_thinking_in_text is True.
    """
    raw_list: List[Dict[str, Any]] = []
    for cm in canonical_messages:
        content = cm.content
        if include_thinking_in_text and cm.thinking_content:
            content = f"<think>\n{cm.thinking_content}\n</think>\n\n{cm.content}"

        raw_list.append({
            "role": cm.role,
            "content": content
        })

    return normalize_for_openai(
        raw_list,
        target_model=target_model,
        target_provider=target_provider,
        default_system=default_system
    )


def openai_to_canonical(
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    provider: str = "openai"
) -> List[CanonicalMessage]:
    """
    Translates OpenAI chat completion messages into CanonicalMessage objects.
    Detects and parses <think> tags from DeepSeek/Groq responses and isolates reasoning_content.
    """
    results: List[CanonicalMessage] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")

        thinking = None
        if isinstance(content, str):
            clean_content, thinking = extract_thinking_tags(content)
        else:
            clean_content = str(content)

        # Check for reasoning_content in raw payload
        if m.get("reasoning_content"):
            thinking = m.get("reasoning_content")

        results.append(CanonicalMessage(
            role=role,
            content=clean_content,
            thinking_content=thinking,
            model=model,
            provider=provider
        ))

    return results


def canonical_to_arena(
    canonical_messages: List[CanonicalMessage],
    target_model: str = "coding-router-preview",
    format_type: str = "chat_completions"
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Translates CanonicalMessages into Arena format.
    Supports standard 'chat_completions' (OpenAI format) or 'session_transcript' (paired turns).
    """
    if format_type == "chat_completions":
        return canonical_to_openai(canonical_messages, target_model=target_model, target_provider="arena")

    # Format as paired rounds for session transcript
    turns: List[Dict[str, Any]] = []
    user_buf: Optional[str] = None

    for cm in canonical_messages:
        if cm.role == CanonicalRole.USER.value:
            user_buf = cm.content
        elif cm.role == CanonicalRole.ASSISTANT.value:
            turns.append({
                "turn_id": len(turns) + 1,
                "user_prompt": user_buf or "",
                "assistant_response": cm.content,
                "model": cm.model or target_model,
                "provider": cm.provider or "arena",
                "thinking_content": cm.thinking_content,
                "latency_ms": cm.latency_ms or 0,
                "created_at": cm.created_at
            })
            user_buf = None

    # Trailing unmatched user message
    if user_buf:
        turns.append({
            "turn_id": len(turns) + 1,
            "user_prompt": user_buf,
            "assistant_response": "",
            "model": target_model,
            "provider": "arena"
        })

    return {"turns": turns}


def arena_to_canonical(
    arena_data: Union[List[Dict[str, Any]], Dict[str, Any]],
    model: Optional[str] = None
) -> List[CanonicalMessage]:
    """
    Translates Arena payload (chat completions list or session transcript dict) to CanonicalMessage objects.
    """
    if isinstance(arena_data, list):
        return openai_to_canonical(arena_data, model=model, provider="arena")

    results: List[CanonicalMessage] = []
    turns = arena_data.get("turns", [])
    for t in turns:
        u_text = t.get("user_prompt", "")
        a_text = t.get("assistant_response", "")
        m_used = t.get("model", model)
        p_used = t.get("provider", "arena")
        thinking = t.get("thinking_content")

        if u_text:
            results.append(CanonicalMessage(
                role=CanonicalRole.USER.value,
                content=u_text,
                model=m_used,
                provider=p_used
            ))
        if a_text or thinking:
            results.append(CanonicalMessage(
                role=CanonicalRole.ASSISTANT.value,
                content=a_text,
                thinking_content=thinking,
                model=m_used,
                provider=p_used,
                latency_ms=t.get("latency_ms")
            ))

    return results


def translate_messages(
    messages: Any,
    from_format: str,
    to_format: str,
    target_model: str = "",
    target_provider: str = "",
    default_system: Optional[str] = None
) -> Any:
    """
    Universal bridge function to convert messages between any two formats.
    Supported formats: 'canonical', 'anthropic', 'openai', 'arena', 'groq', 'deepinfra', 'openrouter'.
    """
    f_from = from_format.lower()
    f_to = to_format.lower()

    # Step 1: Ingest into Canonical
    if f_from == "canonical":
        if messages and isinstance(messages[0], dict):
            canonical = [CanonicalMessage.from_dict(m) for m in messages]
        else:
            canonical = messages
    elif f_from == "anthropic":
        if isinstance(messages, dict):
            canonical = anthropic_to_canonical(messages.get("messages", []), system=messages.get("system"))
        elif isinstance(messages, tuple):
            canonical = anthropic_to_canonical(messages[0] if isinstance(messages[0], list) else messages[1],
                                               system=messages[1] if isinstance(messages[0], list) else messages[0])
        else:
            canonical = anthropic_to_canonical(messages, system=default_system)
    elif f_from in ("openai", "openrouter", "groq", "deepinfra"):
        canonical = openai_to_canonical(messages, model=target_model, provider=f_from)
    elif f_from == "arena":
        canonical = arena_to_canonical(messages, model=target_model)
    else:
        raise ValueError(f"Unsupported source format: {from_format}")

    # Step 2: Export from Canonical to target format
    if f_to == "canonical":
        return canonical
    elif f_to == "anthropic":
        system_str, msgs = canonical_to_anthropic(
            canonical,
            default_system=default_system,
            target_model=target_model
        )
        return {"system": system_str, "messages": msgs}
    elif f_to in ("openai", "groq", "cerebras", "deepinfra", "openrouter"):
        return canonical_to_openai(
            canonical,
            target_model=target_model,
            target_provider=f_to if f_to != "openai" else target_provider or "openai",
            default_system=default_system
        )
    elif f_to == "arena":
        return canonical_to_arena(canonical, target_model=target_model)
    else:
        raise ValueError(f"Unsupported target format: {to_format}")


# ============================================================================
# High-Level Bridge APIs (Database-Backed)
# ============================================================================

def save_conversation(
    conversation_id: Optional[str] = None,
    title: Optional[str] = None,
    default_provider: Optional[str] = None,
    default_model: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> str:
    """Creates a new conversation thread or updates an existing one (UPSERT semantics)."""
    return db.create_conversation(
        conv_id=conversation_id,
        title=title,
        default_provider=default_provider,
        default_model=default_model,
        metadata=metadata
    )


def save_message(
    conversation_id: str,
    role: str,
    content: str,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    thinking_content: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> int:
    """
    Appends a new message turn to a conversation thread.
    Automatically creates conversation if it does not exist.
    Derives title from first user prompt if title was placeholder.
    """
    if role not in db.VALID_ROLES:
        raise ValueError(f"Invalid message role '{role}'. Must be one of {db.VALID_ROLES}")

    return db.create_message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        provider=provider,
        model=model,
        thinking_content=thinking_content,
        metadata=metadata
    )


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves conversation metadata and message count by ID."""
    conv = db.get_conversation(conversation_id)
    if not conv:
        return None
    conv["session_id"] = conv["id"]
    return conv


def get_conversation_messages(
    conversation_id: str,
    target_format: str = "canonical"
) -> Union[List[Dict[str, Any]], Tuple[List[Dict[str, Any]], Optional[str]], Dict[str, Any]]:
    """
    Retrieves all messages for a conversation in chronological order.
    Target formats:
      - 'canonical': List of canonical message dicts with session_id alias.
      - 'openai': List of OpenAI-compatible dicts [{"role": ..., "content": ...}].
      - 'anthropic': Tuple of (messages_list, system_prompt_string).
      - 'arena': List of turns or session transcript dict.
    """
    raw_msgs = db.get_messages_by_conversation(conversation_id, order="ASC")
    canonical = [CanonicalMessage.from_dict(m) for m in raw_msgs]

    tf = target_format.lower()
    if tf == "canonical":
        return [m.to_dict() for m in canonical]

    if tf == "openai":
        return canonical_to_openai(canonical)

    if tf == "anthropic":
        sys_prompt, msgs = canonical_to_anthropic(canonical)
        return msgs, sys_prompt

    if tf == "arena":
        return canonical_to_arena(canonical)

    raise ValueError(f"Unsupported target format: {target_format}")


def list_conversations(
    limit: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """Lists conversations ordered by updated_at DESC with pagination."""
    convs = db.list_conversations(limit=limit, offset=offset)
    for c in convs:
        c["session_id"] = c["id"]
    return convs


def export_conversation_for_provider(
    conversation_id: str,
    provider: str,
    model: str,
    max_tokens: int = 4096,
    temperature: Optional[float] = None,
    reasoning_effort: Optional[str] = None
) -> Dict[str, Any]:
    """
    Bundles the complete multi-turn conversation into a turnkey payload
    ready to be sent directly to the destination provider's HTTP endpoint.
    """
    prov_clean = (provider or "openai").lower()

    if prov_clean in ("anthropic", "claude"):
        msgs, sys_prompt = get_conversation_messages(conversation_id, target_format="anthropic")
        payload: Dict[str, Any] = {
            "model": model,
            "messages": msgs,
            "max_tokens": max_tokens
        }
        if sys_prompt:
            payload["system"] = sys_prompt
        if temperature is not None:
            payload["temperature"] = temperature
        return payload

    # OpenAI, Groq, DeepInfra, OpenRouter, Arena
    raw_msgs = db.get_messages_by_conversation(conversation_id, order="ASC")
    canonical = [CanonicalMessage.from_dict(m) for m in raw_msgs]

    formatted_msgs = canonical_to_openai(
        canonical,
        target_model=model,
        target_provider=prov_clean
    )

    is_reasoning = any(r in model.lower() for r in ("o1", "o3"))
    token_field = "max_completion_tokens" if (is_reasoning and prov_clean == "openai") else "max_tokens"

    payload = {
        "model": model,
        "messages": formatted_msgs,
        token_field: max_tokens
    }
    if temperature is not None and not is_reasoning:
        payload["temperature"] = temperature
    if reasoning_effort and is_reasoning:
        payload["reasoning_effort"] = reasoning_effort

    return payload


def delete_conversation(conversation_id: str) -> bool:
    """Deletes a conversation and cascades deletion to all child messages."""
    return db.delete_conversation(conversation_id)
