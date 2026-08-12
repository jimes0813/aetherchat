"""WP-8 platform governance scaffold (Phase 3 start).

Local-first multi-tenant / RBAC / allowlist / audit foundations.
Desktop default is a single local tenant; structures are ready for team spaces.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _truthy_env(name: str, default: str = "1") -> bool:
    raw = os.environ.get(name, default).strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return raw in {"1", "true", "yes", "on", ""}


def policy_gate_enabled() -> bool:
    """Runtime tool/model policy gate (default on; set AETHERCHAT_PLATFORM_POLICY_GATE=0 to skip)."""
    return _truthy_env("AETHERCHAT_PLATFORM_POLICY_GATE", default="1")


def _data_root() -> Path:
    root = Path(
        os.environ.get(
            "AETHERCHAT_DATA_DIR",
            Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AetherChat",
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


RoleName = Literal["owner", "admin", "member", "viewer"]
EnvName = Literal["dev", "test", "prod"]


class PrincipalCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=200)
    role: RoleName = "member"
    tenant_id: str | None = None


class PolicyPatch(BaseModel):
    environment: EnvName | None = None
    enforce_tool_allowlist: bool | None = None
    enforce_model_allowlist: bool | None = None
    allowed_tools: list[str] | None = None
    allowed_models: list[str] | None = None
    require_approval_for_high_risk: bool | None = None
    dlp_redact_secrets: bool | None = None
    audit_enabled: bool | None = None


class AuthorizeRequest(BaseModel):
    principal_id: str | None = None
    tool_name: str | None = None
    model_name: str | None = None
    action: str = "invoke_tool"


class PlatformGovernanceService:
    """File-backed governance store for local desktop + future multi-tenant."""

    def __init__(self) -> None:
        self.root = _data_root()
        self.store_path = self.root / "platform_governance.json"
        self.audit_path = self.root / "platform_audit.jsonl"
        self.state: dict[str, Any] = {}
        if not self._load():
            self._seed()
            self._save()

    def _load(self) -> bool:
        if not self.store_path.is_file():
            return False
        try:
            raw = json.loads(self.store_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(raw, dict):
            return False
        self.state = raw
        return bool(self.state.get("tenants"))

    def _save(self) -> None:
        self.store_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _seed(self) -> None:
        tenant_id = "local"
        owner_id = str(uuid.uuid4())
        now = _now()
        self.state = {
            "schema_version": 1,
            "phase": "platformization",
            "default_tenant_id": tenant_id,
            "tenants": {
                tenant_id: {
                    "id": tenant_id,
                    "name": "本机工作区",
                    "slug": "local",
                    "created_at": now,
                    "updated_at": now,
                    "policy": {
                        "environment": "dev",
                        "enforce_tool_allowlist": False,
                        "enforce_model_allowlist": False,
                        "allowed_tools": [
                            "write_artifact",
                            "upsert_memory_entity",
                            "create_task",
                        ],
                        "allowed_models": [],
                        "require_approval_for_high_risk": True,
                        "dlp_redact_secrets": True,
                        "audit_enabled": True,
                    },
                }
            },
            "principals": {
                owner_id: {
                    "id": owner_id,
                    "tenant_id": tenant_id,
                    "display_name": "本机所有者",
                    "email": None,
                    "role": "owner",
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                }
            },
            "roles": {
                "owner": {
                    "name": "owner",
                    "permissions": ["*"],
                    "description": "完全控制租户与策略",
                },
                "admin": {
                    "name": "admin",
                    "permissions": [
                        "policy.write",
                        "principal.write",
                        "audit.read",
                        "tool.invoke",
                        "model.use",
                    ],
                    "description": "管理成员与策略",
                },
                "member": {
                    "name": "member",
                    "permissions": ["tool.invoke", "model.use", "audit.read"],
                    "description": "日常协作",
                },
                "viewer": {
                    "name": "viewer",
                    "permissions": ["audit.read"],
                    "description": "只读",
                },
            },
            "created_at": now,
            "updated_at": now,
        }

    # --- Status ---

    def status(self) -> dict[str, Any]:
        tenant = self.get_tenant(self.state.get("default_tenant_id") or "local")
        principals = self.list_principals(tenant_id=tenant["id"] if tenant else None)
        policy = (tenant or {}).get("policy") or {}
        gate_on = policy_gate_enabled()
        return {
            "phase": "3-platformization",
            "phase_label": "第三阶段：平台化",
            "schema_version": self.state.get("schema_version", 1),
            "default_tenant_id": self.state.get("default_tenant_id"),
            "tenant_count": len(self.state.get("tenants") or {}),
            "principal_count": len(principals),
            "policy": policy,
            "runtime_gate": {
                "enabled": gate_on,
                "env": "AETHERCHAT_PLATFORM_POLICY_GATE",
                "tool_path": gate_on,
                "model_path": gate_on,
                "enforce_tool_allowlist": bool(policy.get("enforce_tool_allowlist")),
                "enforce_model_allowlist": bool(policy.get("enforce_model_allowlist")),
            },
            "capabilities": {
                "tenant_isolation": True,
                "rbac": True,
                "tool_allowlist": True,
                "model_allowlist": True,
                "audit_log": True,
                "audit_export": True,
                "runtime_policy_gate": gate_on,
                "sso_oidc": False,
                "scim": False,
                "team_spaces": "scaffold",
            },
            "notes": [
                "默认单租户本机模式；策略可强制工具/模型白名单。",
                "运行时门禁已接入工具调用与模型选择（可用 AETHERCHAT_PLATFORM_POLICY_GATE=0 关闭）。",
                "SSO/OIDC/SCIM 未启用，后续按客户需求接入。",
                "WP-0 公共发布仍 No-Go，与平台化并行。",
            ],
        }

    # --- Tenants ---

    def list_tenants(self) -> list[dict[str, Any]]:
        return sorted(
            (self.state.get("tenants") or {}).values(),
            key=lambda item: str(item.get("name") or ""),
        )

    def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        return (self.state.get("tenants") or {}).get(tenant_id)

    def create_tenant(self, name: str, slug: str | None = None) -> dict[str, Any]:
        clean = " ".join(name.split())
        if not clean:
            raise ValueError("租户名称不能为空")
        slug_value = (slug or clean.lower().replace(" ", "-"))[:64]
        for item in (self.state.get("tenants") or {}).values():
            if item.get("slug") == slug_value:
                raise ValueError(f"slug 已存在：{slug_value}")
        tenant_id = str(uuid.uuid4())
        now = _now()
        tenant = {
            "id": tenant_id,
            "name": clean,
            "slug": slug_value,
            "created_at": now,
            "updated_at": now,
            "policy": {
                "environment": "dev",
                "enforce_tool_allowlist": False,
                "enforce_model_allowlist": False,
                "allowed_tools": [],
                "allowed_models": [],
                "require_approval_for_high_risk": True,
                "dlp_redact_secrets": True,
                "audit_enabled": True,
            },
        }
        self.state.setdefault("tenants", {})[tenant_id] = tenant
        self.state["updated_at"] = now
        self._save()
        self.append_audit(
            action="tenant.create",
            actor_id=None,
            tenant_id=tenant_id,
            detail={"name": clean, "slug": slug_value},
        )
        return tenant

    def patch_policy(self, tenant_id: str, payload: PolicyPatch) -> dict[str, Any]:
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            raise KeyError(tenant_id)
        policy = dict(tenant.get("policy") or {})
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            if value is None:
                continue
            if key in {"allowed_tools", "allowed_models"} and isinstance(value, list):
                policy[key] = [str(item).strip() for item in value if str(item).strip()][:200]
            else:
                policy[key] = value
        tenant["policy"] = policy
        tenant["updated_at"] = _now()
        self.state["tenants"][tenant_id] = tenant
        self.state["updated_at"] = tenant["updated_at"]
        self._save()
        self.append_audit(
            action="policy.patch",
            actor_id=None,
            tenant_id=tenant_id,
            detail={"keys": list(data.keys())},
        )
        return tenant

    # --- Principals ---

    def list_principals(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        rows = list((self.state.get("principals") or {}).values())
        if tenant_id:
            rows = [item for item in rows if item.get("tenant_id") == tenant_id]
        return sorted(rows, key=lambda item: str(item.get("display_name") or ""))

    def create_principal(self, payload: PrincipalCreate) -> dict[str, Any]:
        tenant_id = payload.tenant_id or self.state.get("default_tenant_id") or "local"
        if not self.get_tenant(tenant_id):
            raise ValueError("租户不存在")
        principal_id = str(uuid.uuid4())
        now = _now()
        item = {
            "id": principal_id,
            "tenant_id": tenant_id,
            "display_name": payload.display_name.strip(),
            "email": (payload.email or "").strip() or None,
            "role": payload.role,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        self.state.setdefault("principals", {})[principal_id] = item
        self.state["updated_at"] = now
        self._save()
        self.append_audit(
            action="principal.create",
            actor_id=None,
            tenant_id=tenant_id,
            detail={"principal_id": principal_id, "role": payload.role},
        )
        return item

    def get_principal(self, principal_id: str) -> dict[str, Any] | None:
        return (self.state.get("principals") or {}).get(principal_id)

    def list_roles(self) -> list[dict[str, Any]]:
        return list((self.state.get("roles") or {}).values())

    # --- Authorization ---

    def _role_permissions(self, role: str) -> set[str]:
        item = (self.state.get("roles") or {}).get(role) or {}
        return {str(value) for value in (item.get("permissions") or [])}

    def authorize(
        self,
        payload: AuthorizeRequest,
        *,
        record_allow: bool = True,
    ) -> dict[str, Any]:
        tenant_id = self.state.get("default_tenant_id") or "local"
        principal = None
        if payload.principal_id:
            principal = self.get_principal(payload.principal_id)
            if principal:
                tenant_id = principal.get("tenant_id") or tenant_id
        tenant = self.get_tenant(str(tenant_id))
        if not tenant:
            return {
                "allowed": False,
                "reason": "tenant_missing",
                "message": "租户不存在",
            }
        policy = tenant.get("policy") or {}
        role = str((principal or {}).get("role") or "member")
        perms = self._role_permissions(role)
        action = (payload.action or "invoke_tool").strip() or "invoke_tool"

        if "*" not in perms:
            if action == "invoke_tool" and "tool.invoke" not in perms:
                decision = {
                    "allowed": False,
                    "reason": "rbac_denied",
                    "message": f"角色 {role} 无权调用工具",
                    "role": role,
                }
                self.append_audit(
                    action="authorize.deny",
                    actor_id=(principal or {}).get("id"),
                    tenant_id=str(tenant_id),
                    detail=decision,
                )
                return decision
            if action == "use_model" and "model.use" not in perms:
                decision = {
                    "allowed": False,
                    "reason": "rbac_denied",
                    "message": f"角色 {role} 无权使用模型",
                    "role": role,
                }
                self.append_audit(
                    action="authorize.deny",
                    actor_id=(principal or {}).get("id"),
                    tenant_id=str(tenant_id),
                    detail=decision,
                )
                return decision

        tool_name = (payload.tool_name or "").strip()
        if tool_name and policy.get("enforce_tool_allowlist"):
            allowed_tools = {str(item) for item in (policy.get("allowed_tools") or [])}
            if tool_name not in allowed_tools:
                decision = {
                    "allowed": False,
                    "reason": "tool_not_allowlisted",
                    "message": f"工具不在白名单：{tool_name}",
                    "tool_name": tool_name,
                }
                self.append_audit(
                    action="authorize.deny",
                    actor_id=(principal or {}).get("id"),
                    tenant_id=str(tenant_id),
                    detail=decision,
                )
                return decision

        model_name = (payload.model_name or "").strip()
        if model_name and policy.get("enforce_model_allowlist"):
            allowed_models = {str(item) for item in (policy.get("allowed_models") or [])}
            if model_name not in allowed_models:
                decision = {
                    "allowed": False,
                    "reason": "model_not_allowlisted",
                    "message": f"模型不在白名单：{model_name}",
                    "model_name": model_name,
                }
                self.append_audit(
                    action="authorize.deny",
                    actor_id=(principal or {}).get("id"),
                    tenant_id=str(tenant_id),
                    detail=decision,
                )
                return decision

        decision = {
            "allowed": True,
            "reason": "ok",
            "message": "允许",
            "role": role,
            "environment": policy.get("environment"),
            "require_approval_for_high_risk": bool(policy.get("require_approval_for_high_risk")),
        }
        if record_allow and policy.get("audit_enabled"):
            self.append_audit(
                action="authorize.allow",
                actor_id=(principal or {}).get("id"),
                tenant_id=str(tenant_id),
                detail={
                    "tool_name": tool_name or None,
                    "model_name": model_name or None,
                    "action": action,
                },
            )
        return decision

    def require_tool_invoke(
        self,
        tool_name: str,
        *,
        principal_id: str | None = None,
    ) -> dict[str, Any]:
        """Runtime gate for tool paths. Raises PermissionError when denied."""
        if not policy_gate_enabled():
            return {"allowed": True, "reason": "gate_disabled", "skipped": True}
        clean = str(tool_name or "").strip()
        if not clean:
            raise PermissionError("工具名称为空，平台策略拒绝")
        decision = self.authorize(
            AuthorizeRequest(
                principal_id=principal_id,
                tool_name=clean,
                action="invoke_tool",
            ),
            # Runtime allows are high-volume; denials still audited above.
            record_allow=False,
        )
        if not decision.get("allowed"):
            raise PermissionError(
                str(decision.get("message") or f"平台策略拒绝：{decision.get('reason')}")
            )
        return decision

    def require_model_use(
        self,
        model_name: str,
        *,
        principal_id: str | None = None,
    ) -> dict[str, Any]:
        """Runtime gate for model selection. Raises PermissionError when denied."""
        if not policy_gate_enabled():
            return {"allowed": True, "reason": "gate_disabled", "skipped": True}
        clean = str(model_name or "").strip()
        if not clean:
            raise PermissionError("模型名称为空，平台策略拒绝")
        decision = self.authorize(
            AuthorizeRequest(
                principal_id=principal_id,
                model_name=clean,
                action="use_model",
            ),
            record_allow=False,
        )
        if not decision.get("allowed"):
            raise PermissionError(
                str(decision.get("message") or f"平台策略拒绝：{decision.get('reason')}")
            )
        return decision

    def filter_model_names(
        self,
        names: list[str],
        *,
        principal_id: str | None = None,
    ) -> list[str]:
        """Keep models that pass the runtime gate (order preserved)."""
        if not policy_gate_enabled():
            return list(names)
        allowed: list[str] = []
        for name in names:
            try:
                self.require_model_use(str(name), principal_id=principal_id)
            except PermissionError:
                continue
            allowed.append(str(name))
        return allowed

    # --- Audit ---

    def append_audit(
        self,
        *,
        action: str,
        actor_id: str | None,
        tenant_id: str | None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "id": str(uuid.uuid4()),
            "ts": _now(),
            "action": action,
            "actor_id": actor_id,
            "tenant_id": tenant_id,
            "detail": detail or {},
        }
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def list_audit(self, *, limit: int = 50, tenant_id: str | None = None) -> list[dict[str, Any]]:
        if not self.audit_path.is_file():
            return []
        lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        events: list[dict[str, Any]] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if tenant_id and item.get("tenant_id") != tenant_id:
                continue
            events.append(item)
            if len(events) >= max(1, min(500, limit)):
                break
        return events

    def export_audit(
        self,
        *,
        limit: int = 500,
        tenant_id: str | None = None,
        format: Literal["json", "jsonl"] = "json",
    ) -> dict[str, Any]:
        events = self.list_audit(limit=limit, tenant_id=tenant_id)
        exported_at = _now()
        if format == "jsonl":
            body = "\n".join(json.dumps(item, ensure_ascii=False) for item in events)
            if body:
                body += "\n"
            return {
                "format": "jsonl",
                "exported_at": exported_at,
                "count": len(events),
                "tenant_id": tenant_id,
                "body": body,
            }
        return {
            "format": "json",
            "exported_at": exported_at,
            "count": len(events),
            "tenant_id": tenant_id,
            "events": events,
        }


platform_governance = PlatformGovernanceService()
