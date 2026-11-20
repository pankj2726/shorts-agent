#!/usr/bin/env python3
"""
health_check.py — Probe every provider endpoint and keep the registry live.

For each entry in `agents/agent-4-providers/registry/providers.json` this:
  1. Sends one lightweight probe request (or runs a local check).
  2. Records latency, HTTP status and error class.
  3. Updates the entry's `health` and `last_verified` fields in place.
  4. Writes a timestamped report under `agents/agent-4-providers/health/`.
  5. Sends one Telegram alert if any provider newly degraded or went down.

Rules (from AGENT.md):
  * A provider that cannot be probed — no credential configured, or
    `integration_status == not_started` — keeps its existing health value.
    Unknown stays unknown; it is never guessed.
  * Credentials are never written to the report or the log. Probe URLs are
    recorded with any key material stripped.

Usage:
  python scripts/health_check.py              # probe, update, report, alert
  python scripts/health_check.py --dry-run    # probe and report, change nothing
  python scripts/health_check.py --strict     # exit 1 if a live provider is down
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests

from agent.modules.config import load_env_file

logger = logging.getLogger("health_check")

REGISTRY_PATH = PROJECT_ROOT / "agents" / "agent-4-providers" / "registry" / "providers.json"
HEALTH_DIR = PROJECT_ROOT / "agents" / "agent-4-providers" / "health"

PROBE_TIMEOUT_S = 15

# Providers whose failure should be treated as serious, because the live
# pipeline depends on them.
CRITICAL_PROVIDERS = {"google-gemini"}

# provider id -> probe spec. `key_env` names the credential the probe needs;
# `header` is how that credential is sent. Providers not listed here are not
# probed and keep their existing health value.
PROBES: dict[str, dict] = {
    "google-gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/models",
        "key_env": "GEMINI_API_KEY",
        "header": "query_key",
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/models",
        "key_env": "GROQ_API_KEY",
        "header": "bearer",
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/models",
        "key_env": None,
        "header": None,
    },
    "pollinations-ai": {
        "url": "https://pollinations.ai/",
        "key_env": None,
        "header": None,
    },
    "edge-tts": {"local": "edge_tts_voices"},
}

# HTTP status -> health verdict.
DEGRADED_STATUSES = {402, 429}


def _redact(url: str) -> str:
    """Strip the query string so a key can never reach a report or a log."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _verdict(status: int) -> str:
    if 200 <= status < 300:
        return "ok"
    if status in DEGRADED_STATUSES:
        return "degraded"
    return "down"


def probe_http(spec: dict) -> dict:
    """Send one probe request and classify the result."""
    url = spec["url"]
    headers = {"User-Agent": "shorts-agent-health-check/1.0"}
    key_env = spec.get("key_env")
    key = os.environ.get(key_env, "").strip() if key_env else ""

    if key_env and not key:
        return {
            "probed": False,
            "health": None,
            "reason": f"{key_env} not configured",
            "endpoint": _redact(url),
        }

    target = url
    if spec.get("header") == "query_key" and key:
        target = f"{url}?key={key}"
    elif spec.get("header") == "bearer" and key:
        headers["Authorization"] = f"Bearer {key}"

    started = time.perf_counter()
    try:
        resp = requests.get(target, headers=headers, timeout=PROBE_TIMEOUT_S)
    except requests.exceptions.Timeout:
        return {
            "probed": True,
            "health": "down",
            "reason": "timeout",
            "endpoint": _redact(url),
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    except requests.exceptions.RequestException as exc:
        return {
            "probed": True,
            "health": "down",
            "reason": type(exc).__name__,
            "endpoint": _redact(url),
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "probed": True,
        "health": _verdict(resp.status_code),
        "status_code": resp.status_code,
        "reason": "" if 200 <= resp.status_code < 300 else f"HTTP {resp.status_code}",
        "endpoint": _redact(url),
        "latency_ms": latency_ms,
    }


def probe_edge_tts() -> dict:
    """edge-tts is local code but needs network to list voices."""
    started = time.perf_counter()
    try:
        import asyncio

        import edge_tts

        voices = asyncio.run(edge_tts.list_voices())
    except ImportError:
        return {"probed": False, "health": None, "reason": "edge_tts not installed"}
    except Exception as exc:  # noqa: BLE001 - voice list needs network
        return {
            "probed": True,
            "health": "down",
            "reason": type(exc).__name__,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    return {
        "probed": True,
        "health": "ok",
        "reason": "",
        "voices": len(voices),
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


def probe_ffmpeg() -> dict:
    """ffmpeg is the local render fallback; verify the binary responds."""
    started = time.perf_counter()
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"probed": True, "health": "down", "reason": type(exc).__name__}
    return {
        "probed": True,
        "health": "ok" if result.returncode == 0 else "down",
        "reason": "" if result.returncode == 0 else f"exit {result.returncode}",
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


def probe_provider(provider: str) -> dict:
    """Dispatch to the right probe for one provider."""
    spec = PROBES.get(provider)
    if spec is None:
        return {"probed": False, "health": None, "reason": "no probe defined"}
    if spec.get("local") == "edge_tts_voices":
        return probe_edge_tts()
    if spec.get("local") == "ffmpeg":
        return probe_ffmpeg()
    return probe_http(spec)


def run(dry_run: bool = False, strict: bool = False) -> int:
    """Probe every provider, update the registry, report, and alert."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )

    # Credentials arrive via the ENV_FILE secret, which the workflow writes to
    # .env. Individual secrets such as GEMINI_API_KEY are usually empty, so
    # without this the keyed probes report "not configured" even though the key
    # is present. Existing environment values still take precedence.
    loaded = load_env_file()
    if loaded is not None:
        logger.info("loaded environment from %s", loaded)

    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as fh:
            registry = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("cannot read registry %s: %s", REGISTRY_PATH, exc)
        return 2

    today = datetime.now(timezone.utc).date().isoformat()
    results: list[dict] = []
    transitions: list[str] = []
    critical_down: list[str] = []

    for entry in registry:
        provider = entry.get("provider", "?")
        previous = entry.get("health")

        if entry.get("integration_status") == "not_started":
            results.append(
                {"provider": provider, "probed": False, "health": previous,
                 "reason": "integration_status=not_started"}
            )
            logger.info("%-18s skipped (not_started)", provider)
            continue

        result = probe_provider(provider)
        result["provider"] = provider
        result["previous_health"] = previous
        results.append(result)

        new_health = result.get("health")
        if new_health is None:
            logger.info("%-18s not probed (%s)", provider, result.get("reason"))
            continue

        logger.info(
            "%-18s %-9s %sms %s",
            provider,
            new_health,
            result.get("latency_ms", "-"),
            result.get("reason", ""),
        )

        if new_health != previous:
            transitions.append(f"{provider}: {previous} -> {new_health}")
        if provider in CRITICAL_PROVIDERS and new_health == "down":
            critical_down.append(provider)

        if not dry_run:
            entry["health"] = new_health
            entry["last_verified"] = today

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "probe_timeout_s": PROBE_TIMEOUT_S,
        "results": results,
        "transitions": transitions,
    }

    if not dry_run:
        try:
            with open(REGISTRY_PATH, "w", encoding="utf-8") as fh:
                json.dump(registry, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
        except OSError as exc:
            logger.error("cannot write registry: %s", exc)
            return 2

        try:
            HEALTH_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            report_path = HEALTH_DIR / f"health-{stamp}.json"
            with open(report_path, "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2, ensure_ascii=False)
            # Rolling copy so dashboards have one stable path to read.
            with open(HEALTH_DIR / "health-latest.json", "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2, ensure_ascii=False)
            try:
                shown: Path = report_path.relative_to(PROJECT_ROOT)
            except ValueError:
                shown = report_path
            logger.info("report written to %s", shown)
        except OSError as exc:
            logger.warning("cannot write report: %s", exc)

    if transitions:
        _alert(transitions, critical_down)

    probed = [r for r in results if r.get("probed")]
    print()
    print(f"Probed {len(probed)}/{len(results)} providers; "
          f"{len(transitions)} health change(s).")
    for line in transitions:
        print(f"  {line}")
    if critical_down:
        print(f"  CRITICAL down: {', '.join(critical_down)}")

    if strict and critical_down:
        return 1
    return 0


def _alert(transitions: list[str], critical_down: list[str]) -> None:
    """Send one Telegram alert summarising health changes."""
    try:
        from agent.modules.notify import notify
    except ImportError as exc:
        logger.warning("notify unavailable: %s", exc)
        return
    level = "error" if critical_down else "warn"
    body = "Provider health changes:\n" + "\n".join(f"  {t}" for t in transitions)
    if critical_down:
        body += f"\nCRITICAL: {', '.join(critical_down)} is down."
    try:
        notify("PROVIDER_HEALTH", body, level=level)
    except Exception as exc:  # noqa: BLE001 - alerting must never break the run
        logger.warning("notify failed: %s", exc)


def main() -> int:
    global PROBE_TIMEOUT_S
    parser = argparse.ArgumentParser(description="Probe provider endpoints.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="probe and report without modifying the registry",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="exit 1 when a provider the live pipeline needs is down",
    )
    parser.add_argument(
        "--timeout", type=int, default=None,
        help=f"per-probe timeout in seconds (default {PROBE_TIMEOUT_S})",
    )
    args = parser.parse_args()

    if args.timeout:
        PROBE_TIMEOUT_S = max(1, args.timeout)

    return run(dry_run=args.dry_run, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
