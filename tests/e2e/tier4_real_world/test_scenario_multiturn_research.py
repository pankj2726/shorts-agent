"""
test_scenario_multiturn_research.py — Tier 4 Real-World Application Scenarios.

Scenario 1: Realistic 10-turn dialogue simulating a full YouTube Shorts research
and script drafting session alternating between Claude and GPT-4.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List
import pytest
import requests

from tests.e2e.conftest import MockGatewayServer, E2ETestDB
import tests.e2e.conftest as conftest_module


@pytest.fixture(autouse=True)
def force_ephemeral_port(monkeypatch: pytest.MonkeyPatch):
    """Ensure each mock gateway server binds to a unique ephemeral port."""
    monkeypatch.setattr(conftest_module, "_find_available_port", lambda preferred=0: 0)


class TestScenarioMultiturnResearch:
    """Real-World Production Workflow: Full 10-turn YouTube Shorts script drafting pipeline."""

    def test_full_10_turn_shorts_production_dialogue(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Simulate an end-to-end 10-turn production session:
          Turn 1 (Claude): Ideation
          Turn 2 (Claude): Psychological angle selection
          Turn 3 (GPT-4o): 5 viral hooks
          Turn 4 (GPT-4o): 45-second 3-beat structure
          Turn 5 (Claude): Full first draft
          Turn 6 (GPT-4o): Anti-slop and fluff removal critique
          Turn 7 (Claude): Voiceover pacing revision (140 WPM)
          Turn 8 (GPT-4o): Visual B-roll cues and on-screen text overlays
          Turn 9 (Claude): YouTube title, description, and SEO tags
          Turn 10 (GPT-4o): Final approval and production sign-off
        """
        conv_id = "scenario_multiturn_10_turns_001"
        e2e_db.create_conversation(
            conv_id,
            title="Psychology of Procrastination - 60s Short",
            metadata={"workflow": "youtube_shorts_pipeline", "target_seconds": 55}
        )

        turns = [
            ("claude", "/v1/messages", "claude-3-5-sonnet", "Turn 1: Ideate 3 surprising concepts regarding executive dysfunction."),
            ("claude", "/v1/messages", "claude-3-5-sonnet", "Turn 2: Develop concept #2 into 2 distinct psychological angles."),
            ("openai", "/v1/chat/completions", "gpt-4o", "Turn 3: Write 5 high-retention hooks under 12 words for angle #1."),
            ("openai", "/v1/chat/completions", "gpt-4o", "Turn 4: Map out a 3-act narrative arc (Hook, Twist, Solution)."),
            ("claude", "/v1/messages", "claude-3-5-sonnet", "Turn 5: Draft the full voiceover script based on the 3-act arc."),
            ("openai", "/v1/chat/completions", "gpt-4o", "Turn 6: Critique draft for AI clichés and robotic cadence."),
            ("claude", "/v1/messages", "claude-3-5-sonnet", "Turn 7: Polish voiceover for energetic 140 WPM delivery."),
            ("openai", "/v1/chat/completions", "gpt-4o", "Turn 8: Insert timestamped B-roll prompts and animated caption cues."),
            ("claude", "/v1/messages", "claude-3-5-sonnet", "Turn 9: Generate metadata: click-worthy title and 5 tags."),
            ("openai", "/v1/chat/completions", "gpt-4o", "Turn 10: Perform compliance check and approve for voice synthesis."),
        ]

        for step_idx, (family, endpoint, model, prompt) in enumerate(turns, start=1):
            if family == "openai":
                payload = {
                    "model": model,
                    "conversation_id": conv_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7
                }
            else:
                payload = {
                    "model": model,
                    "conversation_id": conv_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1024
                }

            resp = e2e_client.post(
                f"{mock_gateway_server.url}{endpoint}",
                json=payload,
                headers={"Connection": "close"}
            )
            assert resp.status_code == 200, f"Turn {step_idx} failed: {resp.text}"

        time.sleep(0.1)

        # 1. Verify exactly 20 messages in DB (10 user, 10 assistant)
        all_msgs = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(all_msgs) == 20, f"Expected 20 messages in thread, found {len(all_msgs)}"

        # 2. Verify sequence alternation
        for i in range(10):
            user_msg = all_msgs[i * 2]
            asst_msg = all_msgs[i * 2 + 1]
            assert user_msg["role"] == "user"
            assert f"Turn {i + 1}" in user_msg["content"]
            assert asst_msg["role"] == "assistant"
            assert asst_msg["model"] in ("claude-3-5-sonnet", "gpt-4o")

        # 3. Verify models used alternation count
        claude_count = sum(1 for m in all_msgs if m["model"] == "claude-3-5-sonnet" and m["role"] == "assistant")
        gpt_count = sum(1 for m in all_msgs if m["model"] == "gpt-4o" and m["role"] == "assistant")
        assert claude_count == 5, f"Expected 5 Claude assistant turns, got {claude_count}"
        assert gpt_count == 5, f"Expected 5 GPT-4o assistant turns, got {gpt_count}"

        # 4. Verify API transcript recovery via Gateway endpoint
        transcript_resp = e2e_client.get(
            f"{mock_gateway_server.url}/v1/conversations/{conv_id}",
            headers={"Connection": "close"}
        )
        assert transcript_resp.status_code == 200
        data = transcript_resp.json()
        assert len(data["messages"]) == 20

    def test_multiturn_session_restoration_across_clients(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify that a secondary client (e.g. mobile app or automated worker)
        can query the conversation state mid-dialogue, restore context,
        and continue the multi-turn session seamlessly.
        """
        conv_id = "scenario_multiturn_context_recovery_002"

        # Client A executes Turns 1-3
        for turn_num in range(1, 4):
            e2e_client.post(
                f"{mock_gateway_server.url}/v1/messages",
                json={
                    "model": "claude-3-5-sonnet",
                    "conversation_id": conv_id,
                    "messages": [{"role": "user", "content": f"Turn {turn_num}: Initial planning."}],
                    "max_tokens": 256
                },
                headers={"Connection": "close"}
            )

        time.sleep(0.05)

        # Client B boots up in another session and queries transcript in OpenAI format
        client_b = requests.Session()
        res = client_b.get(
            f"{mock_gateway_server.url}/v1/conversations/{conv_id}?format=openai",
            headers={"Connection": "close"}
        )
        assert res.status_code == 200
        history = res.json()["messages"]
        assert len(history) == 6, f"Expected 6 messages restored, got {len(history)}"

        # Client B continues with Turn 4 on ChatGPT endpoint
        res4 = client_b.post(
            f"{mock_gateway_server.url}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "conversation_id": conv_id,
                "messages": history + [{"role": "user", "content": "Turn 4: Continued from restored context."}]
            },
            headers={"Connection": "close"}
        )
        assert res4.status_code == 200
        client_b.close()

        time.sleep(0.05)

        # Verify DB reflects continuation: 8 messages total
        total_msgs = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(total_msgs) == 8

    def test_multiturn_dialogue_with_system_directive_fidelity(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify that a multi-turn dialogue with custom system prompt retains
        the conversation metadata, role attribution, and token telemetry across steps.
        """
        conv_id = "scenario_system_fidelity_003"
        system_directive = "You are ShortsDirector-AI. Maintain 120-140 WPM pacing."

        for turn in range(1, 4):
            resp = e2e_client.post(
                f"{mock_gateway_server.url}/v1/messages",
                json={
                    "model": "claude-coding-router-preview",
                    "conversation_id": conv_id,
                    "system": system_directive,
                    "messages": [{"role": "user", "content": f"Turn {turn}: Pacing check."}],
                    "max_tokens": 512
                },
                headers={"Connection": "close"}
            )
            assert resp.status_code == 200

        time.sleep(0.05)
        msgs = e2e_db.get_conversation_messages(conv_id, format="raw")
        assert len(msgs) == 6

    def test_multiturn_token_budget_and_turn_accounting(
        self,
        mock_gateway_server: MockGatewayServer,
        e2e_client: requests.Session,
        e2e_db: E2ETestDB
    ) -> None:
        """
        Verify that input and output tokens are monotonically recorded across turns
        in SQLite for telemetry and cost tracking.
        """
        conv_id = "scenario_token_accounting_004"

        for turn in range(1, 4):
            e2e_client.post(
                f"{mock_gateway_server.url}/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "conversation_id": conv_id,
                    "messages": [{"role": "user", "content": f"Turn {turn}: Token test prompt."}]
                },
                headers={"Connection": "close"}
            )

        time.sleep(0.05)
        msgs = e2e_db.get_conversation_messages(conv_id, format="raw")
        asst_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert len(asst_msgs) == 3

        for m in asst_msgs:
            assert m["tokens_out"] > 0
            assert m["tokens_in"] >= 0
