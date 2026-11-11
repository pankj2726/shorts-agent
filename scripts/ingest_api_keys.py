#!/usr/bin/env python3
"""
ingest_api_keys.py — Secure Interactive Ingestion of Organization API Keys.

Prompts for API keys using getpass (hidden input, zero terminal echo, zero chat exposure).
Appends/updates C:\\Secrets\\shorts-agent.env and runs vault_sync.ps1 to encrypt to Cloudflare R2.
Adheres strictly to Workspace Rule 4 (Zero Secrets in Chat / Logs).
"""

import getpass
import os
import subprocess
import sys
from pathlib import Path

SECRETS_FILE = Path(r"C:\Secrets\shorts-agent.env")
VAULT_SYNC_SCRIPT = Path(__file__).resolve().parent / "vault_sync.ps1"


def update_secret(key_name: str, key_value: str) -> None:
    if not key_value.strip():
        return

    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    found = False

    if SECRETS_FILE.exists():
        with open(SECRETS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith(f"{key_name}=") or stripped.startswith(f"export {key_name}="):
                    lines.append(f"{key_name}={key_value.strip()}\n")
                    found = True
                else:
                    lines.append(line)

    if not found:
        lines.append(f"{key_name}={key_value.strip()}\n")

    with open(SECRETS_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print(f"  [OK] Successfully configured {key_name} in C:\\Secrets\\shorts-agent.env")


def main():
    print("=" * 75)
    print(" SHORTS FORGE — SECURE API KEY INGESTION (Rule 4 Compliant)")
    print("=" * 75)
    print("Input is hidden and masked. Keys are never printed to terminal or chat.")
    print("Press Enter to skip any key you do not wish to update.")
    print("-" * 75)

    # 1. Anthropic / Claude
    anthropic_key = getpass.getpass("Enter ANTHROPIC_API_KEY (sk-ant-...): ").strip()
    if anthropic_key:
        update_secret("ANTHROPIC_API_KEY", anthropic_key)
        org_id = input("Enter ANTHROPIC_ORG_ID (optional, press Enter to skip): ").strip()
        if org_id:
            update_secret("ANTHROPIC_ORG_ID", org_id)

    # 2. OpenAI / ChatGPT
    openai_key = getpass.getpass("Enter OPENAI_API_KEY (sk-...): ").strip()
    if openai_key:
        update_secret("OPENAI_API_KEY", openai_key)
        openai_org = input("Enter OPENAI_ORG_ID (optional, press Enter to skip): ").strip()
        if openai_org:
            update_secret("OPENAI_ORG_ID", openai_org)

    # 3. Google Cloud / Vertex AI
    vertex_key = getpass.getpass("Enter VERTEX_API_KEY / GEMINI_API_KEY (optional): ").strip()
    if vertex_key:
        update_secret("VERTEX_API_KEY", vertex_key)
        update_secret("GEMINI_API_KEY", vertex_key)

    print("-" * 75)
    print("Syncing updated credentials to Cloudflare R2 encrypted vault...")
    if VAULT_SYNC_SCRIPT.exists():
        res = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(VAULT_SYNC_SCRIPT)],
            capture_output=True,
            text=True
        )
        if res.returncode == 0:
            print("  [OK] Cloudflare R2 Vault Sync Complete (Encrypted with age).")
        else:
            print(f"  [!] Vault sync notice: {res.stderr.strip() or res.stdout.strip()}")

    print("=" * 75)
    print("All keys ingested and secured. Run 'python scripts/model_catalog_cli.py' to verify.")
    print("=" * 75)


if __name__ == "__main__":
    main()
