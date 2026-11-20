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
from typing import Iterable

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


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
