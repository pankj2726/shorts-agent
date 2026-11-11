#!/usr/bin/env python3
"""
test_provider_connections.py — Validates live connectivity for Claude and OpenAI.
Zero secret values are printed or logged (Rule 4 compliant).
"""

import sys
import time
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.modules.anthropic_adapter import AnthropicAdapter
from agent.modules.openai_adapter import OpenAIAdapter


def main():
    print("=" * 75)
    print(" SHORTS FORGE — LIVE PROVIDER HANDSHAKE & CONNECTION TEST")
    print("=" * 75)
    print("Testing connection to Anthropic (Claude) and OpenAI (ChatGPT)...")
    print("-" * 75)

    # 1. Anthropic Test
    print("1. Probing Anthropic Console / Claude API...")
    t0 = time.time()
    try:
        ant_models = AnthropicAdapter.discover_models(timeout=10)
        ant_latency = int((time.time() - t0) * 1000)
        if ant_models:
            print(f"   [SUCCESS] Anthropic API Connected!")
            print(f"   - Latency: {ant_latency} ms")
            print(f"   - Models Discovered: {len(ant_models)} models")
            sample_names = [m.get("id") for m in ant_models[:3]]
            print(f"   - Sample Endpoints: {sample_names}")
        else:
            # Try a lightweight completion test
            res = AnthropicAdapter.complete(
                model="claude-3-5-haiku-20241022",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                timeout=10
            )
            ant_latency = int((time.time() - t0) * 1000)
            if res.get("status") == "success":
                print(f"   [SUCCESS] Anthropic API Connected via Messages endpoint!")
                print(f"   - Latency: {ant_latency} ms")
            else:
                err = res.get("error", "Unknown error")
                print(f"   [NOTICE] Anthropic response: {err}")
    except Exception as e:
        print(f"   [ERROR] Anthropic connection failed: {e}")

    print("-" * 75)

    # 2. OpenAI Test
    print("2. Probing OpenAI / ChatGPT API...")
    t0 = time.time()
    try:
        oa_models = OpenAIAdapter.discover_models(timeout=10)
        oa_latency = int((time.time() - t0) * 1000)
        if oa_models:
            print(f"   [SUCCESS] OpenAI API Connected!")
            print(f"   - Latency: {oa_latency} ms")
            print(f"   - Models Discovered: {len(oa_models)} models")
            sample_names = [m.get("id") for m in oa_models[:4]]
            print(f"   - Sample Endpoints: {sample_names}")
        else:
            # Try a lightweight completion test
            res = OpenAIAdapter.complete(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                timeout=10
            )
            oa_latency = int((time.time() - t0) * 1000)
            if res.get("status") == "success":
                print(f"   [SUCCESS] OpenAI API Connected via Chat endpoint!")
                print(f"   - Latency: {oa_latency} ms")
            else:
                err = res.get("error", "Unknown error")
                print(f"   [NOTICE] OpenAI response: {err}")
    except Exception as e:
        print(f"   [ERROR] OpenAI connection failed: {e}")

    print("=" * 75)


if __name__ == "__main__":
    main()
