"""
Unit test suite for inter-agent communication, schemas, thread-safety, and message bus routing.
Milestone 1 / Requirement R1.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from pydantic import ValidationError

from agent.modules.orchestration import (
    AgentMessage,
    AgentID,
    RecipientID,
    ConfidenceLevel,
    AgentAction,
    MessageLogger,
    AgentCommsLogger,
    MessageBus,
    BaseAgent,
)


def test_envelope_valid_roundtrip(tmp_path):
    log_file = tmp_path / "test_comms.jsonl"
    logger = MessageLogger(log_path=log_file)

    msg = AgentMessage(
        message_id="msg_a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        timestamp="2026-11-10T12:00:00Z",
        sender=AgentID.AGENT_1_RESEARCH.value,
        recipient=RecipientID.AGENT_2_ANTI_SLOP.value,
        task_id="T008",
        action=AgentAction.SUBMIT_SCRIPT.value,
        payload={"hook": "Stop wasting hours...", "title": "Quick Shortcut"},
        confidence=ConfidenceLevel.VERIFIED.value,
        sources=["knowledge/youtube/shorts-platform-publishing-rules.md"],
    )

    logger.log(msg)
    messages = logger.read_messages(task_id="T008")
    assert len(messages) == 1
    loaded = messages[0]
    assert loaded.message_id == msg.message_id
    assert loaded.sender == AgentID.AGENT_1_RESEARCH.value
    assert loaded.recipient == AgentID.AGENT_2_ANTI_SLOP.value
    assert loaded.action == AgentAction.SUBMIT_SCRIPT.value
    assert loaded.payload["title"] == "Quick Shortcut"
    assert loaded.confidence == ConfidenceLevel.VERIFIED.value
    assert loaded.sources == ["knowledge/youtube/shorts-platform-publishing-rules.md"]


def test_envelope_missing_required_field():
    with pytest.raises(ValidationError):
        # task_id is required
        AgentMessage(
            sender=AgentID.AGENT_1_RESEARCH.value,
            recipient=RecipientID.AGENT_2_ANTI_SLOP.value,
            action=AgentAction.SUBMIT_SCRIPT.value,
        )


def test_envelope_invalid_iso_timestamp():
    with pytest.raises(ValidationError) as exc:
        AgentMessage(
            timestamp="2026-11-10 12:00:00",  # Missing 'T' and timezone
            sender=AgentID.AGENT_1_RESEARCH.value,
            recipient=RecipientID.AGENT_2_ANTI_SLOP.value,
            task_id="T001",
            action=AgentAction.TASK_CLAIM.value,
        )
    assert "not a valid ISO-8601 UTC string" in str(exc.value)


def test_envelope_invalid_sender_recipient():
    with pytest.raises(ValidationError):
        AgentMessage(
            sender="rogue-hacker-agent",
            recipient=RecipientID.ORCHESTRATOR.value,
            task_id="T001",
            action=AgentAction.TASK_CLAIM.value,
        )

    with pytest.raises(ValidationError):
        AgentMessage(
            sender=AgentID.ORCHESTRATOR.value,
            recipient="unregistered-target",
            task_id="T001",
            action=AgentAction.TASK_CLAIM.value,
        )


def test_verified_confidence_requires_sources():
    # VERIFIED with empty sources must raise ValidationError
    with pytest.raises(ValidationError) as exc:
        AgentMessage(
            message_id="msg_11112222-3333-4444-5555-666677778888",
            timestamp="2026-11-10T12:00:00Z",
            sender=AgentID.AGENT_4_PROVIDERS.value,
            recipient=RecipientID.ORCHESTRATOR.value,
            task_id="T001",
            action=AgentAction.SUBMIT_RESEARCH.value,
            payload={"provider": "gemini"},
            confidence=ConfidenceLevel.VERIFIED.value,
            sources=[],
        )
    assert "VERIFIED confidence messages must include at least one source citation" in str(exc.value)


def test_verified_confidence_with_sources_passes():
    msg = AgentMessage(
        sender=AgentID.AGENT_4_PROVIDERS.value,
        recipient=RecipientID.ORCHESTRATOR.value,
        task_id="T001",
        action=AgentAction.SUBMIT_RESEARCH.value,
        payload={"provider": "gemini"},
        confidence=ConfidenceLevel.VERIFIED.value,
        sources=["https://ai.google.dev/pricing"],
    )
    assert msg.confidence == "VERIFIED"
    assert len(msg.sources) == 1


def test_thread_safe_concurrent_logging(tmp_path):
    log_file = tmp_path / "concurrent_comms.jsonl"
    logger = AgentCommsLogger(log_path=log_file)

    total_threads = 20
    msgs_per_thread = 10

    def worker(thread_idx: int):
        for i in range(msgs_per_thread):
            msg = AgentMessage(
                sender=AgentID.AGENT_1_RESEARCH.value,
                recipient=RecipientID.ORCHESTRATOR.value,
                task_id=f"T_{thread_idx}",
                action=AgentAction.TASK_PROGRESS.value,
                payload={"thread": thread_idx, "seq": i},
            )
            logger.log(msg)

    with ThreadPoolExecutor(max_workers=total_threads) as executor:
        futures = [executor.submit(worker, t) for t in range(total_threads)]
        for f in futures:
            f.result()

    # Read back all lines and assert no corrupted lines
    with open(log_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    assert len(lines) == total_threads * msgs_per_thread
    for line in lines:
        data = json.loads(line)
        assert "message_id" in data
        assert "sender" in data
        assert "timestamp" in data


def test_comms_reader_filtering(tmp_path):
    log_file = tmp_path / "filter_test.jsonl"
    logger = MessageLogger(log_path=log_file)

    for i in range(30):
        t_id = "T001" if i % 2 == 0 else "T002"
        sender = AgentID.AGENT_1_RESEARCH.value if i < 15 else AgentID.AGENT_2_ANTI_SLOP.value
        msg = AgentMessage(
            sender=sender,
            recipient=RecipientID.ORCHESTRATOR.value,
            task_id=t_id,
            action=AgentAction.TASK_PROGRESS.value,
            payload={"i": i},
        )
        logger.log(msg)

    # Filter by task_id
    t001_msgs = logger.read_messages(task_id="T001")
    assert len(t001_msgs) == 15
    assert all(m.task_id == "T001" for m in t001_msgs)

    # Filter by sender
    a1_msgs = logger.read_messages(sender=AgentID.AGENT_1_RESEARCH.value)
    assert len(a1_msgs) == 15
    assert all(m.sender == AgentID.AGENT_1_RESEARCH.value for m in a1_msgs)

    # Filter with limit
    limited = logger.read_messages(limit=5)
    assert len(limited) == 5


def test_comms_reader_corrupt_line_recovery(tmp_path):
    log_file = tmp_path / "corrupt_test.jsonl"
    logger = MessageLogger(log_path=log_file)

    msg1 = AgentMessage(
        sender=AgentID.ORCHESTRATOR.value,
        recipient=RecipientID.AGENT_1_RESEARCH.value,
        task_id="T001",
        action=AgentAction.TASK_DELEGATE.value,
    )
    msg2 = AgentMessage(
        sender=AgentID.AGENT_1_RESEARCH.value,
        recipient=RecipientID.ORCHESTRATOR.value,
        task_id="T001",
        action=AgentAction.TASK_PROGRESS.value,
    )

    with open(log_file, "w", encoding="utf-8") as f:
        f.write(msg1.to_jsonl_line())
        f.write("CORRUPTED JUNK LINE {{{ Not JSON...\n")
        f.write(msg2.to_jsonl_line())

    # Reader should yield both valid messages and ignore the corrupt line
    messages = logger.read_messages()
    assert len(messages) == 2
    assert messages[0].action == AgentAction.TASK_DELEGATE.value
    assert messages[1].action == AgentAction.TASK_PROGRESS.value


def test_broadcast_dispatch(tmp_path):
    log_file = tmp_path / "bus_log.jsonl"
    logger = MessageLogger(log_path=log_file)
    bus = MessageBus(logger_instance=logger)

    class MockAgent(BaseAgent):
        def __init__(self, agent_id: str):
            super().__init__(agent_id=agent_id, name=f"Mock {agent_id}", role="Tester", bus=bus)
            self.handled = []

        def handle_message(self, message: AgentMessage):
            self.handled.append(message)
            return None

    a1 = MockAgent(AgentID.AGENT_1_RESEARCH.value)
    a2 = MockAgent(AgentID.AGENT_2_ANTI_SLOP.value)
    a3 = MockAgent(AgentID.AGENT_3_SOCIAL.value)

    bus.register_agent(a1)
    bus.register_agent(a2)
    bus.register_agent(a3)

    broadcast_msg = AgentMessage(
        sender=AgentID.AGENT_1_RESEARCH.value,
        recipient=RecipientID.BROADCAST.value,
        task_id="T_GLOBAL",
        action=AgentAction.HEARTBEAT.value,
    )
    bus.dispatch(broadcast_msg)

    # Sender a1 should not have handled its own broadcast
    assert len(a1.handled) == 0
    # Recipients a2 and a3 should both receive it
    assert len(a2.handled) == 1
    assert len(a3.handled) == 1


def test_dead_letters(tmp_path):
    log_file = tmp_path / "dead_log.jsonl"
    logger = MessageLogger(log_path=log_file)
    bus = MessageBus(logger_instance=logger)

    msg = AgentMessage(
        sender=AgentID.ORCHESTRATOR.value,
        recipient=RecipientID.AGENT_5_INNOVATION.value,
        task_id="T004",
        action=AgentAction.TASK_DELEGATE.value,
    )
    # Recipient not registered
    bus.dispatch(msg)
    assert len(bus.dead_letters) == 1
    assert bus.dead_letters[0].task_id == "T004"
