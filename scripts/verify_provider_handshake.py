#!/usr/bin/env python3
"""
verify_provider_handshake.py — Provider Handshake & Credential Verification CLI.

Directive 6.1: Validates credential presence and bounded connectivity across 10 target providers
without exposing secrets or consuming paid generation credits.

Usage:
    python scripts/verify_provider_handshake.py
    python -m agent.main handshake
"""

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.modules.provider_handshake import ProviderHandshakeEngine, PROVIDER_SPECS


def main() -> None:
    print("=" * 75)
    print(" SHORTS AGENT — Directive 6.1: Provider Handshake & Secrets Verification")
    print("=" * 75)
    print("Security Policy: Zero Secrets Displayed | Bounded Safe Health Probes Only")
    print("-" * 75)
    print(f"{'Provider':<24} | {'Secret Name':<22} | {'Presence':<10} | {'Handshake':<12}")
    print("-" * 75)

    all_statuses = []
    for pid, spec in PROVIDER_SPECS.items():
        cred_info = ProviderHandshakeEngine.check_credential_status(pid)
        handshake_info = ProviderHandshakeEngine.execute_handshake(pid)

        presence = "SET" if cred_info["configured"] else "MISSING"
        if pid == "pollinations" and not cred_info["configured"]:
            presence = "FREE_PUBLIC"

        status = handshake_info["status"]
        all_statuses.append(status)

        status_display = {
            "PASSED": "[OK] PASS",
            "UNCONFIGURED": "[--] PENDING",
            "FAILED": "[X] FAIL",
            "DEGRADED": "[!] DEGRADED",
        }.get(status, status)

        print(f"{spec['display_name']:<24} | {spec['secret_name']:<22} | {presence:<10} | {status_display:<12}")

    print("-" * 75)
    passed_count = sum(1 for s in all_statuses if s in ("PASSED", "DEGRADED"))
    pending_count = sum(1 for s in all_statuses if s == "UNCONFIGURED")
    failed_count = sum(1 for s in all_statuses if s == "FAILED")

    print(f"Summary: {passed_count} Connected / Ready | {pending_count} Pending Setup | {failed_count} Failed")
    print("=" * 75)


if __name__ == "__main__":
    main()
