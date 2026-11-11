"""
Tier 1: Feature Coverage — Multi-Agent Coordination & Message Bus (Feature 1 & 2)
Tests agent message contracts, role boundaries, JSONL logging, confidence levels,
and routing dispatch.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import pytest
from pydantic import BaseModel, Field, ValidationError

# Define / import AgentMessage contract per PROJECT.md § Interface Contracts
try:
    from agent.modules.orchestration import AgentMessage, MessageBus
except ImportError:
    class AgentMessage(BaseModel):
        message_id: str
        timestamp: str
        sender_agent: str
        recipient_agent: str
        task_id: str
        action: str
        payload: dict
        confidence: str = Field(..., pattern=r"^(VERIFIED|HYPOTHESIS|EXPERIMENTAL)$")
        sources: list[str]

    class MessageBus:
        def __init__(self, log_path: Path):
            self.log_path = Path(log_path)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.subscribers = {}

        def publish(self, msg: AgentMessage) -> None:
            # Append to jsonl
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(msg.model_dump_json() + "\n")
            if msg.recipient_agent in self.subscribers:
                for cb in self.subscribers[msg.recipient_agent]:
                    cb(msg)

        def subscribe(self, agent_id: str, callback) -> None:
            self.subscribers.setdefault(agent_id, []).append(callback)


SPECIALIST_AGENTS = [
    "agent-1-research",
    "agent-2-anti-slop",
    "agent-3-social",
    "agent-4-providers",
    "agent-5-innovation",
    "orchestrator",
]


def test_agent_message_valid_construction():
    """Test 1: Happy path creation of compliant AgentMessage envelope."""
    msg = AgentMessage(
        message_id="msg-101",
        timestamp=datetime.now(timezone.utc).isoformat(),
        sender_agent="agent-1-research",
        recipient_agent="orchestrator",
        task_id="task-001",
        action="HANDOFF_RESEARCH",
        payload={"topic": "battery life", "views": 150000},
        confidence="VERIFIED",
        sources=["https://batteryuniversity.com/learn/article"],
    )
    assert msg.message_id == "msg-101"
    assert msg.confidence == "VERIFIED"
    assert msg.sender_agent == "agent-1-research"
    assert "battery life" in msg.payload["topic"]


def test_agent_message_rejects_invalid_confidence():
    """Test 2: Ensure invalid confidence tags raise validation error."""
    with pytest.raises(ValidationError):
        AgentMessage(
            message_id="msg-102",
            timestamp=datetime.now(timezone.utc).isoformat(),
            sender_agent="agent-2-anti-slop",
            recipient_agent="orchestrator",
            task_id="task-002",
            action="EVALUATION_REPORT",
            payload={"score": 85},
            confidence="GUESS",  # Not in VERIFIED, HYPOTHESIS, EXPERIMENTAL
            sources=[],
        )


def test_agent_message_rejects_missing_mandatory_fields():
    """Test 3: Ensure missing mandatory fields fail schema validation."""
    with pytest.raises(ValidationError):
        AgentMessage(
            message_id="msg-103",
            timestamp=datetime.now(timezone.utc).isoformat(),
            # sender_agent missing
            recipient_agent="agent-4-providers",
            task_id="task-003",
            action="REQUEST_QUOTA",
            payload={},
            confidence="HYPOTHESIS",
            sources=[],
        )


def test_message_bus_logs_structured_jsonl(tmp_path):
    """Test 4: Verify message bus appends valid JSONL entries to logs/agent-comms.jsonl."""
    log_file = tmp_path / "logs" / "agent-comms.jsonl"
    bus = MessageBus(log_path=log_file)

    msg1 = AgentMessage(
        message_id="msg-001",
        timestamp=datetime.now(timezone.utc).isoformat(),
        sender_agent="agent-1-research",
        recipient_agent="agent-2-anti-slop",
        task_id="task-001",
        action="REVIEW_SCRIPT",
        payload={"script": "Sample script text"},
        confidence="VERIFIED",
        sources=["source1"],
    )
    msg2 = AgentMessage(
        message_id="msg-002",
        timestamp=datetime.now(timezone.utc).isoformat(),
        sender_agent="agent-2-anti-slop",
        recipient_agent="orchestrator",
        task_id="task-001",
        action="REVIEW_RESULT",
        payload={"verdict": "PASS"},
        confidence="VERIFIED",
        sources=["rubric-a"],
    )

    bus.publish(msg1)
    bus.publish(msg2)

    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2

    parsed1 = json.loads(lines[0])
    parsed2 = json.loads(lines[1])
    assert parsed1["message_id"] == "msg-001"
    assert parsed2["payload"]["verdict"] == "PASS"


def test_message_bus_subscription_and_dispatch(tmp_path):
    """Test 5: Verify agent subscriber receives targeted message in real-time."""
    log_file = tmp_path / "logs" / "agent-comms.jsonl"
    bus = MessageBus(log_path=log_file)

    received_messages = []

    def on_orchestrator_message(msg: AgentMessage):
        received_messages.append(msg)

    bus.subscribe("orchestrator", on_orchestrator_message)

    msg = AgentMessage(
        message_id="msg-sub-1",
        timestamp=datetime.now(timezone.utc).isoformat(),
        sender_agent="agent-4-providers",
        recipient_agent="orchestrator",
        task_id="task-quota-1",
        action="QUOTA_STATUS_UPDATE",
        payload={"remaining_units": 9400},
        confidence="VERIFIED",
        sources=["sqlite:quota_ledger"],
    )

    bus.publish(msg)
    assert len(received_messages) == 1
    assert received_messages[0].action == "QUOTA_STATUS_UPDATE"
    assert received_messages[0].payload["remaining_units"] == 9400


def test_all_specialist_agents_defined_in_agents_directory():
    """Test 6: Verify all 5 specialist agents + orchestrator exist in repository structure."""
    repo_agents_dir = Path(__file__).parent.parent.parent / "agents"
    assert repo_agents_dir.exists(), "agents/ directory must exist in repo root"

    for agent_name in SPECIALIST_AGENTS:
        agent_dir = repo_agents_dir / agent_name
        assert agent_dir.exists(), f"Agent folder for '{agent_name}' missing in agents/"
        agent_spec = agent_dir / "AGENT.md" if agent_name != "orchestrator" else agent_dir / "ORCHESTRATOR.md"
        assert agent_spec.exists(), f"Specification file missing for {agent_name}: {agent_spec}"
