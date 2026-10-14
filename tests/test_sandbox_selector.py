from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.sandbox.bwrap_executor import SandboxUnavailableError, bwrap_available  # noqa: E402
from app.sandbox.executor import execute_in_sandbox, select_sandbox_backend  # noqa: E402


def test_select_bwrap_when_forced(monkeypatch):
    monkeypatch.setenv("AETHERCHAT_SANDBOX_BACKEND", "bwrap")
    if not bwrap_available():
        pytest.skip("bwrap not installed")
    assert select_sandbox_backend() == "bwrap"


def test_fail_closed_when_backend_missing(monkeypatch):
    monkeypatch.setenv("AETHERCHAT_SANDBOX_BACKEND", "docker")
    monkeypatch.setattr("app.sandbox.executor.docker_available", lambda: False)
    with pytest.raises(SandboxUnavailableError):
        select_sandbox_backend(prefer="docker")


def test_bwrap_runs_echo(tmp_path, monkeypatch):
    if not bwrap_available():
        pytest.skip("bwrap not installed")
    monkeypatch.setenv("AETHERCHAT_SANDBOX_BACKEND", "bwrap")
    result = asyncio.run(
        execute_in_sandbox(
            "echo f1-sandbox",
            cwd=".",
            timeout=10,
            workspace_path=tmp_path,
            backend="bwrap",
            allow_net=False,
        )
    )
    assert result.get("backend") == "bwrap"
    assert result.get("exit_code") == 0
    assert "f1-sandbox" in (result.get("stdout") or "")
