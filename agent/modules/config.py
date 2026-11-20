"""
config.py — Environment access that tolerates empty values.

GitHub Actions injects an *empty string* for an unset repository variable
(`env: FOO: ${{ vars.FOO }}`), so `os.environ.get(name, default)` returns `""`
rather than the intended default. That silently produces `int("")` crashes,
`"".split(",") == [""]` slot lists, and boolean flags that flip to False.

Every helper here treats an unset, empty, or whitespace-only value as
"not configured" and falls back to the default.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Searched in order; the first existing file wins. Kept identical to the list
# llm.py has always used so behaviour does not change for existing callers.
ENV_FILE_CANDIDATES = (
    Path(r"C:\Secrets\shorts-agent.env"),
    Path(r"C:\Secrets\.env"),
    REPO_ROOT / ".env",
    Path(".env"),
)


def load_env_file() -> Optional[Path]:
    """
    Load KEY=VALUE pairs from the first existing .env candidate.

    Values already present in the environment win, and an existing-but-empty
    variable is treated as unset so a blank injected by CI can be filled in.
    Returns the path that was loaded, or None when no candidate exists.
    """
    for candidate in ENV_FILE_CANDIDATES:
        if not candidate.exists():
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and not os.environ.get(key):
                        os.environ[key] = value
        except OSError as exc:
            logger.warning("config: cannot read %s: %s", candidate, exc)
        return candidate
    return None


def env_str(name: str, default: str) -> str:
    """Return the env var, or `default` when it is unset/blank."""
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def env_int(name: str, default: int) -> int:
    """Return the env var as an int, or `default` when unset/blank/invalid."""
    raw = env_str(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("config: %s=%r is not an integer; using %d", name, raw, default)
        return default


def env_bool(name: str, default: bool) -> bool:
    """Return the env var as a bool, or `default` when unset/blank."""
    raw = env_str(name, "")
    if not raw:
        return default
    return raw.lower() in _TRUTHY


def env_list(name: str, default: Iterable[str], sep: str = ",") -> list[str]:
    """
    Split the env var on `sep`, dropping empty parts.

    Returns `default` when the value is unset/blank or contains no non-empty
    parts, so a caller never receives `[""]`.
    """
    raw = env_str(name, "")
    fallback = list(default)
    if not raw:
        return fallback
    parts = [part.strip() for part in raw.split(sep)]
    return [part for part in parts if part] or fallback
