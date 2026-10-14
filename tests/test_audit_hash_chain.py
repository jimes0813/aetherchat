"""F0: platform audit hash-chain + archive-on-prune."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

os.environ.setdefault("AETHERCHAT_DATA_DIR", tempfile.mkdtemp(prefix="aetherchat-audit-"))

from app.services.platform_governance import PlatformGovernanceService  # noqa: E402


@pytest.fixture()
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(tmp_path))
    return PlatformGovernanceService()


def test_append_sets_prev_hash_and_hash(service: PlatformGovernanceService):
    first = service.append_audit(
        action="test.first",
        actor_id=None,
        tenant_id="local",
        detail={"n": 1},
        prune=False,
    )
    assert first["prev_hash"] == ""
    assert len(first["hash"]) == 64
    second = service.append_audit(
        action="test.second",
        actor_id=None,
        tenant_id="local",
        detail={"n": 2},
        prune=False,
    )
    assert second["prev_hash"] == first["hash"]
    verified = service.verify_audit_chain()
    assert verified["ok"] is True
    assert verified["checked"] >= 2


def test_tamper_breaks_verify(service: PlatformGovernanceService):
    service.append_audit(action="test.a", actor_id=None, tenant_id="local", prune=False)
    service.append_audit(action="test.b", actor_id=None, tenant_id="local", prune=False)
    lines = service.audit_path.read_text(encoding="utf-8").splitlines()
    item = json.loads(lines[-1])
    item["detail"] = {"tampered": True}
    lines[-1] = json.dumps(item, ensure_ascii=False)
    service.audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    verified = service.verify_audit_chain()
    assert verified["ok"] is False
    assert verified["errors"]


def test_prune_archives_without_silent_rewrite(service: PlatformGovernanceService):
    for index in range(12):
        service.append_audit(
            action=f"test.event.{index}",
            actor_id=None,
            tenant_id="local",
            project_id="default",
            detail={"i": index},
            prune=False,
        )
    result = service.prune_audit(tenant_id="local", retention_days=3650, max_events=5)
    assert result["removed"] >= 1
    assert result["archived"] >= 1
    archive = Path(result["archive_path"])
    assert archive.is_file()
    assert archive.read_text(encoding="utf-8").strip()
    verified = service.verify_audit_chain()
    assert verified["ok"] is True
