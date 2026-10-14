from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.services.computer_use_consent import (  # noqa: E402
    ComputerUseConsentError,
    require_computer_use_consent,
)
from app.services.computer_use_policy import ComputerUsePolicy  # noqa: E402


def test_default_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("AETHERCHAT_COMPUTER_USE_ENABLED", raising=False)
    monkeypatch.delenv("AETHERCHAT_DISABLE_COMPUTER_USE", raising=False)
    policy = ComputerUsePolicy(path=tmp_path / "computer_use_policy.json")
    assert policy.is_enabled() is False


def test_hard_disable_blocks_enable(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHERCHAT_DISABLE_COMPUTER_USE", "1")
    policy = ComputerUsePolicy(path=tmp_path / "computer_use_policy.json")
    assert policy.is_enabled() is False
    with pytest.raises(PermissionError):
        policy.set_enabled(True)


def test_consent_rejects_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("AETHERCHAT_COMPUTER_USE_ENABLED", raising=False)
    monkeypatch.delenv("AETHERCHAT_DISABLE_COMPUTER_USE", raising=False)
    monkeypatch.delenv("AETHERCHAT_TEST_AUTO_APPROVE_COMPUTER_USE", raising=False)
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(tmp_path))
    # Force reload of singleton path via new policy on consent import path
    from app.services import computer_use_policy as policy_mod

    policy_mod.computer_use_policy = ComputerUsePolicy(path=tmp_path / "computer_use_policy.json")
    with pytest.raises(ComputerUseConsentError, match="未启用"):
        asyncio.run(require_computer_use_consent("click", "test"))


def test_hard_disable_beats_test_auto_approve(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHERCHAT_DISABLE_COMPUTER_USE", "1")
    monkeypatch.setenv("AETHERCHAT_TEST_AUTO_APPROVE_COMPUTER_USE", "1")
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(tmp_path))
    from app.services import computer_use_policy as policy_mod

    policy_mod.computer_use_policy = ComputerUsePolicy(path=tmp_path / "computer_use_policy.json")
    with pytest.raises(ComputerUseConsentError, match="企业策略"):
        asyncio.run(require_computer_use_consent("click", "test"))
