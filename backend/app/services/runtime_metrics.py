"""Minimal process-local counters for diagnostics (stdlib only).

Disable with AETHERCHAT_METRICS=0. Counters stay in-memory and are exposed via
the existing diagnostics snapshot (loopback-authenticated).
"""

from __future__ import annotations

import os
import threading
from typing import Any

_LOCK = threading.Lock()
_COUNTERS: dict[str, int] = {
    "ws_connections": 0,
    "ws_disconnects": 0,
    "run_failures": 0,
    "sandbox_failures": 0,
    "mcp_reapprovals": 0,
    "computer_use_rejects": 0,
}


def metrics_enabled() -> bool:
    raw = os.environ.get("AETHERCHAT_METRICS", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def incr(name: str, amount: int = 1) -> None:
    if not metrics_enabled():
        return
    key = str(name)
    with _LOCK:
        _COUNTERS[key] = int(_COUNTERS.get(key, 0)) + int(amount)


def set_gauge(name: str, value: int) -> None:
    if not metrics_enabled():
        return
    with _LOCK:
        _COUNTERS[str(name)] = int(value)


def snapshot() -> dict[str, Any]:
    with _LOCK:
        counters = dict(_COUNTERS)
    return {
        "enabled": metrics_enabled(),
        "counters": counters,
        "note": "Disable with AETHERCHAT_METRICS=0",
    }


def reset_for_tests() -> None:
    with _LOCK:
        for key in list(_COUNTERS):
            _COUNTERS[key] = 0
