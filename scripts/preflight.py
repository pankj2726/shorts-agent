#!/usr/bin/env python3
"""
preflight.py — readiness checks for switching on the router and the state DB.

Run this before setting LLM_ROUTER=true or DB_BACKEND=postgres. It reports what
is configured, what is missing, and what would break, without changing anything
and without contacting a provider.

    python scripts/preflight.py                 # full report, exit 0
    python scripts/preflight.py --strict        # exit 1 on any blocking finding
    python scripts/preflight.py --json          # machine-readable output

Nothing here prints a credential. Secrets are reported as present/absent only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.modules.config import load_env_file


def _check_credentials() -> dict:
    """Which provider keys are configured. Never reports a value."""
    from agent import router

    configured, missing = {}, []
    for provider, env_name in sorted(router.PROVIDER_KEY_ENV.items()):
        present = bool(os.environ.get(env_name, "").strip())
        configured[provider] = {"env": env_name, "present": present}
        if not present:
            missing.append(provider)
    return {"providers": configured, "missing": missing}


def _check_routing(creds: dict) -> dict:
    """Resolve every task and record which ones cannot be served."""
    from agent import router
    from agent.router import ROUTING_TABLE, RoutingError

    resolved, unresolved = {}, {}
    for task in sorted(ROUTING_TABLE):
        try:
            decision = router.route(task)
            resolved[task] = {
                "provider": decision.provider,
                "model": decision.model,
                "compat_exceptions": decision.compat_exceptions,
            }
        except RoutingError as exc:
            unresolved[task] = str(exc)[:200]
    return {"resolved": resolved, "unresolved": unresolved}


def _check_registry() -> dict:
    from agent import router

    findings = router.validate_registry(policy=router.modality_policy())
    return {
        "policy": router.modality_policy(),
        "errors": [f for f in findings if f.get("severity") == "error"],
        "info": [f for f in findings if f.get("severity") != "error"],
    }


def _check_state_db() -> dict:
    """Report the configured backend. Connects only when explicitly asked."""
    from agent.modules import db

    backend = db.backend()
    report = {"backend": backend}
    if backend != "postgres":
        report["note"] = (
            "SQLite in use; no DATABASE_URL required. Set DB_BACKEND=postgres "
            "plus a DATABASE_URL secret to switch."
        )
        return report

    url = db.postgres_url()
    if not url:
        report["ok"] = False
        report["error"] = "DB_BACKEND=postgres but DATABASE_URL is not set"
        return report

    from agent.modules import db_pg

    report["url"] = db_pg.redact_url(url)
    try:
        findings = db_pg.validate_url(url)
        report["host"] = findings["host"]
        report["warnings"] = findings["warnings"]
    except ValueError as exc:
        report["ok"] = False
        report["error"] = str(exc)
        return report

    report["note"] = (
        "URL is well-formed. Use --connect to actually open a connection; this "
        "run did not touch the database."
    )
    report["ok"] = True
    return report


def _check_state_db_connected() -> dict:
    from agent.modules import db, db_pg

    if db.backend() != "postgres":
        return {"skipped": "DB_BACKEND is not postgres"}
    url = db.postgres_url()
    if not url:
        return {"ok": False, "error": "DATABASE_URL is not set"}
    return db_pg.diagnose(url)


def run(strict: bool = False, connect: bool = False) -> dict:
    load_env_file()  # credentials arrive via ENV_FILE in CI

    report = {
        "credentials": _check_credentials(),
        "registry": _check_registry(),
        "routing": None,
        "state_db": _check_state_db(),
    }
    report["routing"] = _check_routing(report["credentials"])

    if connect:
        report["state_db_connection"] = _check_state_db_connected()

    blocking = []
    if report["registry"]["errors"]:
        blocking.append(
            f"{len(report['registry']['errors'])} registry error(s): "
            + ", ".join(f["provider"] for f in report["registry"]["errors"])
        )
    if report["state_db"].get("ok") is False:
        blocking.append(report["state_db"].get("error", "state DB misconfigured"))
    if connect and report.get("state_db_connection", {}).get("ok") is False:
        blocking.append("database connection failed")

    # An unroutable task is only a blocker once the router is switched on, and
    # only for tasks the router is actually expected to serve. Local tooling and
    # unintegrated providers are reported but not blocking.
    from agent import router as _router

    if _router.router_enabled():
        llm_tasks = {
            task for task, providers in _router.ROUTING_TABLE.items()
            if any(p in _router._LLM_BACKENDS for p in providers)
        }
        broken = sorted(llm_tasks & set(report["routing"]["unresolved"]))
        if broken:
            blocking.append(
                "LLM_ROUTER is enabled but these LLM tasks cannot be served: "
                + ", ".join(broken)
            )

    report["blocking"] = blocking
    report["unroutable_tasks"] = sorted(report["routing"]["unresolved"])
    report["ok"] = not blocking
    return report


def print_human(report: dict) -> None:
    creds = report["credentials"]
    present = [p for p, v in creds["providers"].items() if v["present"]]
    print("Credentials")
    print(f"  present : {', '.join(sorted(present)) or '(none)'}")
    print(f"  missing : {', '.join(sorted(creds['missing'])) or '(none)'}")

    reg = report["registry"]
    print(f"\nRegistry (policy={reg['policy']})")
    print(f"  errors  : {len(reg['errors'])}")
    for f in reg["errors"]:
        print(f"    - {f['provider']}: {f['issue']} — {f['detail']}")
    print(f"  info    : {len(reg['info'])}")
    for f in reg["info"]:
        print(f"    - {f['provider']}: {f['issue']}")

    routing = report["routing"]
    print(f"\nRouting — {len(routing['resolved'])} resolved, "
          f"{len(routing['unresolved'])} unroutable")
    for task, info in sorted(routing["resolved"].items()):
        extras = ""
        if info["compat_exceptions"]:
            extras = f"  [{' '.join(info['compat_exceptions'])}]"
        print(f"  ok   {task:<20} -> {info['provider']} ({info['model']}){extras}")
    for task, why in sorted(routing["unresolved"].items()):
        print(f"  FAIL {task:<20} -> {why[:100]}")

    db_report = report["state_db"]
    print(f"\nState DB — backend={db_report['backend']}")
    for key in ("url", "host", "note", "error"):
        if db_report.get(key):
            print(f"  {key:<6}: {db_report[key]}")
    for warning in db_report.get("warnings", []):
        print(f"  warn  : {warning}")

    if report.get("state_db_connection"):
        conn = report["state_db_connection"]
        print(f"  connect ok={conn.get('ok')} tables={conn.get('tables')}")
        if conn.get("missing_tables"):
            print(f"  missing tables: {conn['missing_tables']}")

    print(f"\nBlocking issues: {len(report['blocking'])}")
    for item in report["blocking"]:
        print(f"  - {item}")
    print(f"\nRESULT: {'READY' if report['ok'] else 'NOT READY'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 if any blocking issue is found")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of a human report")
    parser.add_argument("--connect", action="store_true",
                        help="actually open the configured database connection")
    args = parser.parse_args()

    report = run(strict=args.strict, connect=args.connect)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_human(report)

    return 1 if (args.strict and not report["ok"]) else 0


if __name__ == "__main__":
    sys.exit(main())
