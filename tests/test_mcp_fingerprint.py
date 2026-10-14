from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

os.environ.setdefault("AETHERCHAT_DATA_DIR", tempfile.mkdtemp(prefix="aetherchat-mcp-"))

from app.services.mcp_registry import (  # noqa: E402
    MCPRegistry,
    mcp_config_fingerprint,
    _invalidate_grants_for_config_change,
)


def test_fingerprint_changes_with_command():
    base = {"transport": "stdio", "command": "npx", "args": ["-y", "demo"], "cwd": None, "url": None, "auth_type": "none"}
    a = mcp_config_fingerprint(base)
    b = mcp_config_fingerprint({**base, "command": "node"})
    assert a != b
    assert len(a) == 64


def test_invalidate_clears_allowed_tools_on_drift():
    connection = {
        "transport": "stdio",
        "command": "npx",
        "args": [],
        "cwd": None,
        "url": None,
        "auth_type": "none",
        "allowed_tools": ["search"],
        "granted_config_fingerprint": "deadbeef",
    }
    changed = _invalidate_grants_for_config_change(connection, reason="mcp_config_changed")
    assert changed is True
    assert connection["allowed_tools"] == []
    assert connection["reapproval_required"] is True
    assert connection["reapproval_reason"] == "mcp_config_changed"


def test_replace_launch_config_requires_reapproval(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(tmp_path))
    registry = MCPRegistry(base_path=tmp_path)
    registry._connections["demo"] = {
        "id": "demo",
        "name": "demo",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "x"],
        "cwd": None,
        "url": None,
        "auth_type": "none",
        "enabled": True,
        "token_ref": "MCP_TOKEN_DEMO",
        "allowed_tools": ["search"],
        "tools": [{"name": "search"}],
        "status": "ready",
        "config_fingerprint": mcp_config_fingerprint(
            {"transport": "stdio", "command": "npx", "args": ["-y", "x"], "cwd": None, "url": None, "auth_type": "none"}
        ),
        "granted_config_fingerprint": mcp_config_fingerprint(
            {"transport": "stdio", "command": "npx", "args": ["-y", "x"], "cwd": None, "url": None, "auth_type": "none"}
        ),
    }
    registry.replace_launch_config("demo", command="node", args=["server.js"])
    public = registry.get("demo")
    assert public is not None
    assert public["reapproval_required"] is True
    assert public["allowed_tools"] == []
