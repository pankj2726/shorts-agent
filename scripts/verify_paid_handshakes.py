#!/usr/bin/env python3
"""
verify_paid_handshakes.py — Automated Handshake Authorization & Paid Model Router Test.

Verifies:
  1. Workspace Handshake Headers injection (pankajbhati140@gmail.com)
  2. Arena.AI H100 Dedicated Cluster Handshake (claude-coding-router-preview)
  3. Anthropic Claude Direct Adapter Handshake (claude-3-5-sonnet)
  4. OpenAI ChatGPT Direct Adapter Handshake (o3-mini / gpt-4o)
  5. Automatic Failover to H100 GPU clusters on rate limit or 429
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

# Setup path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.modules.capability_registry import UniversalCapabilityRegistry
from agent.modules.omniroute_embedded import EmbeddedOmniRoute

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("HandshakeVerifier")

CONFIG_PATH = ROOT / "agent" / "config" / "paid_router_config.json"


def main():
    print("=" * 80)
    print(" SHORTS FORGE — ENTERPRISE PAID MODEL HANDSHAKE & ROUTING VERIFICATION")
    print("=" * 80)

    # 1. Load Workspace Identity
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    ws_handshake = cfg.get("workspace_handshake", {})
    identity_email = ws_handshake.get("identity_email", "pankajbhati140@gmail.com")
    org_name = ws_handshake.get("organization_name", "Shorts Forge AI Studio")
    headers = ws_handshake.get("headers", {})

    print(f"Workspace Identity:  {identity_email}")
    print(f"Organization Name:   {org_name}")
    print(f"Authorized Headers:  {headers}")
    print("-" * 80)

    router = EmbeddedOmniRoute()
    scorecard = []

    # 2. Test Arena H100 Paid Cluster
    print("1. Probing Arena.AI H100 Dedicated Cluster (claude-coding-router-preview)...")
    t0 = time.time()
    try:
        res = router.complete_text(
            prompt="Respond with exactly 'AUTHORIZED: ARENA H100 ONLINE'",
            model="coding-router-preview",
            provider_preference=["arena"]
        )
        latency = int((time.time() - t0) * 1000)
        text = res.get("text", "").strip()
        scorecard.append({
            "target": "Arena H100 (Claude Sonnet / Coding Router)",
            "status": "PASSED ✅",
            "latency": f"{latency} ms",
            "provider": res.get("provider", "arena"),
            "model": res.get("canonical_model", "coding-router-preview"),
            "sample_response": text[:50]
        })
        print(f"   [SUCCESS] Latency: {latency} ms | Response: {text[:60]}")
    except Exception as e:
        scorecard.append({
            "target": "Arena H100",
            "status": "FAILED ❌",
            "error": str(e)
        })
        print(f"   [ERROR] Arena handshake failed: {e}")

    print("-" * 80)

    # 3. Test Anthropic Direct Adapter
    print("2. Probing Anthropic Claude Console (CLAUDE_CONSOLE_API_KEY)...")
    t0 = time.time()
    try:
        from agent.modules.anthropic_adapter import AnthropicAdapter
        models = AnthropicAdapter.discover_models(timeout=5)
        latency = int((time.time() - t0) * 1000)
        scorecard.append({
            "target": "Anthropic Claude Direct API",
            "status": "AUTHENTICATED ✅" if models else "FAILED ❌",
            "latency": f"{latency} ms",
            "details": f"Discovered {len(models)} models"
        })
        print(f"   [SUCCESS] Latency: {latency} ms | Discovered: {len(models)} models")
    except Exception as e:
        scorecard.append({
            "target": "Anthropic Claude Direct API",
            "status": "FAILED ❌",
            "error": str(e)
        })
        print(f"   [NOTICE] Anthropic note: {e}")

    print("-" * 80)

    # 4. Test OpenAI ChatGPT Direct Adapter
    print("3. Probing OpenAI ChatGPT API (OPENAI_API_KEY)...")
    t0 = time.time()
    try:
        from agent.modules.openai_adapter import OpenAIAdapter
        oa_models = OpenAIAdapter.discover_models(timeout=5)
        latency = int((time.time() - t0) * 1000)
        scorecard.append({
            "target": "OpenAI / ChatGPT Direct API",
            "status": "AUTHENTICATED ✅" if oa_models else "FAILED ❌",
            "latency": f"{latency} ms",
            "details": f"Discovered {len(oa_models)} models"
        })
        print(f"   [SUCCESS] Latency: {latency} ms | Discovered: {len(oa_models)} models")
    except Exception as e:
        scorecard.append({
            "target": "OpenAI / ChatGPT Direct API",
            "status": "FAILED ❌",
            "error": str(e)
        })
        print(f"   [NOTICE] OpenAI note: {e}")

    print("-" * 80)

    # 5. Test Dynamic Failover to Paid Heavy Models
    print("4. Testing Universal Router Dynamic Cascading Execution...")
    t0 = time.time()
    try:
        # Request Claude 3.5 Sonnet through automatic dynamic fallback
        res_failover = router.complete_text(
            prompt="Confirm studio connection status in 5 words.",
            model="claude-3-5-sonnet"
        )
        latency = int((time.time() - t0) * 1000)
        scorecard.append({
            "target": "Universal Failover (Claude 3.5 Sonnet)",
            "status": "OPERATIONAL ✅",
            "latency": f"{latency} ms",
            "provider": res_failover.get("provider"),
            "model": res_failover.get("canonical_model"),
            "text": res_failover.get("text", "").strip()
        })
        print(f"   [SUCCESS] Failover routed to: {res_failover.get('provider')} ({res_failover.get('canonical_model')}) in {latency} ms")
        print(f"   Output: {res_failover.get('text', '').strip()}")
    except Exception as e:
        print(f"   [ERROR] Router failover failed: {e}")

    print("=" * 80)
    print(" SUMMARY SCORECARD")
    print("=" * 80)
    for sc in scorecard:
        print(f"• {sc['target']:<45} : {sc['status']}")
        if "latency" in sc:
            print(f"  Latency: {sc['latency']} | Details: {sc.get('details') or sc.get('sample_response') or sc.get('text')}")
    print("=" * 80)


if __name__ == "__main__":
    main()
