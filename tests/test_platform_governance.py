"""Phase 3 WP-8 platform governance scaffold tests."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

os.environ["AETHERCHAT_DATA_DIR"] = tempfile.mkdtemp(prefix="aetherchat-platform-")

from app.services.platform_governance import (  # noqa: E402
    AuthorizeRequest,
    PolicyPatch,
    PrincipalCreate,
    PlatformGovernanceService,
)


@pytest.fixture()
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(tmp_path))
    return PlatformGovernanceService()


def test_seed_has_local_tenant_and_owner(service: PlatformGovernanceService):
    status = service.status()
    assert status["phase"] == "3-platformization"
    assert status["tenant_count"] >= 1
    assert status["principal_count"] >= 1
    assert status["capabilities"]["rbac"] is True
    tenants = service.list_tenants()
    assert any(item["id"] == "local" for item in tenants)


def test_tool_allowlist_enforced_when_enabled(service: PlatformGovernanceService):
    tenant = service.get_tenant("local")
    assert tenant
    service.patch_policy(
        "local",
        PolicyPatch(
            enforce_tool_allowlist=True,
            allowed_tools=["write_artifact"],
        ),
    )
    deny = service.authorize(AuthorizeRequest(tool_name="run_command", action="invoke_tool"))
    assert deny["allowed"] is False
    assert deny["reason"] == "tool_not_allowlisted"

    allow = service.authorize(AuthorizeRequest(tool_name="write_artifact", action="invoke_tool"))
    assert allow["allowed"] is True


def test_viewer_cannot_invoke_tools(service: PlatformGovernanceService):
    principal = service.create_principal(
        PrincipalCreate(display_name="只读成员", role="viewer", tenant_id="local"),
    )
    decision = service.authorize(
        AuthorizeRequest(
            principal_id=principal["id"],
            tool_name="write_artifact",
            action="invoke_tool",
        )
    )
    assert decision["allowed"] is False
    assert decision["reason"] == "rbac_denied"


def test_audit_records_authorization(service: PlatformGovernanceService):
    service.authorize(AuthorizeRequest(tool_name="create_task", action="invoke_tool"))
    events = service.list_audit(limit=20, tenant_id="local")
    assert any(item.get("action", "").startswith("authorize.") for item in events)
