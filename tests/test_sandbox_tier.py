from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.sandbox.bwrap_executor import SandboxUnavailableError, bwrap_available  # noqa: E402
from app.sandbox.executor import sandbox_status, select_sandbox_tier  # noqa: E402


def test_strong_fails_closed_without_docker(monkeypatch):
    monkeypatch.setattr("app.sandbox.executor.docker_available", lambda: False)
    with pytest.raises(SandboxUnavailableError, match="strong"):
        select_sandbox_tier("strong")


def test_bwrap_tier(monkeypatch):
    if not bwrap_available():
        pytest.skip("bwrap missing")
    monkeypatch.setattr("app.sandbox.executor.docker_available", lambda: False)
    assert select_sandbox_tier("bwrap") == "bwrap"


def test_sandbox_status_shape(monkeypatch):
    monkeypatch.delenv("AETHERCHAT_SANDBOX_TIER", raising=False)
    status = sandbox_status()
    assert "tier" in status and "backend" in status
    assert "docker_available" in status and "bwrap_available" in status
