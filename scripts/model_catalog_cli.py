#!/usr/bin/env python3
"""
model_catalog_cli.py — CLI for Universal Model Freedom & Capability Inspection.

Demonstrates unified discovery, parameter translation, and ModelAccessPolicy evaluation.
Zero consumer subscription upselling; strictly honors organization credentials.
"""

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.modules.capability_registry import UniversalCapabilityRegistry
from agent.modules.access_policy import ModelAccessPolicy
from agent.modules.model_discovery import DynamicModelDiscovery


def main():
    print("=" * 80)
    print(" SHORTS FORGE — MODEL FREEDOM INITIATIVE (V3): Unified Model Discovery")
    print("=" * 80)
    print("Security Policy: Decoupled from Consumer Plans | Organization API Authenticated")
    print("-" * 80)

    # 1. Dynamic Discovery
    print("1. Running Dynamic Model Discovery across connected endpoints...")
    discovered = DynamicModelDiscovery.discover_all(force_refresh=False)
    print(f"   Discovered {len(discovered)} total models across connected providers.")
    print("-" * 80)

    # 2. Universal Model Catalog
    print(f"{'Canonical ID':<26} | {'Provider':<10} | {'Status':<12} | {'Context':<8} | {'Effort':<8}")
    print("-" * 80)

    models = UniversalCapabilityRegistry.get_all_models()
    for m in models:
        cid = m["canonical_id"]
        prov = m.get("primary_provider", "auto")
        stat = m["status"]
        caps = m.get("capabilities", {})
        ctx = str(m.get("context_window", "N/A"))
        effort = "YES" if caps.get("reasoning_effort", {}).get("supported") else "NO"
        bullet = "*" if m["is_active"] else " "

        print(f"{bullet} {cid:<24} | {prov:<10} | {stat:<12} | {ctx:<8} | {effort:<8}")

    print("-" * 80)
    active = UniversalCapabilityRegistry.get_active_model()
    effort = UniversalCapabilityRegistry.get_active_effort()
    print(f"Active Model: {active} | Active Effort: {effort.upper()}")

    # 3. ModelAccessPolicy Check
    policy_res = ModelAccessPolicy.evaluate(
        user_authenticated=True,
        provider_id="arena",
        model_id=active
    )
    print(f"Access Policy: {'ALLOWED' if policy_res.allowed else 'RESTRICTED'} ({policy_res.status_code})")
    print(f"Policy Output: {policy_res.message}")
    print("=" * 80)


if __name__ == "__main__":
    main()
