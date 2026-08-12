"""WP-8 platform governance scaffold (Phase 3 start).

Local-first multi-tenant / RBAC / allowlist / audit foundations.
Desktop default is a single local tenant; structures are ready for team spaces.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 2
DEFAULT_AUDIT_RETENTION_DAYS = 90
DEFAULT_AUDIT_MAX_EVENTS = 10_000


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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


def _default_policy() -> dict[str, Any]:
    return {
        "environment": "dev",
        "enforce_tool_allowlist": False,
        "enforce_model_allowlist": False,
        "allowed_tools": [],
        "allowed_models": [],
        "require_approval_for_high_risk": True,
        "dlp_redact_secrets": True,
        "audit_enabled": True,
        "audit_retention_days": DEFAULT_AUDIT_RETENTION_DAYS,
        "audit_max_events": DEFAULT_AUDIT_MAX_EVENTS,
    }


RoleName = Literal["owner", "admin", "member", "viewer"]
EnvName = Literal["dev", "test", "prod"]
ExportFormat = Literal["json", "jsonl", "csv"]


class PrincipalCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=200)
    role: RoleName = "member"
    tenant_id: str | None = None
    project_id: str | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=64)
    tenant_id: str | None = None
    description: str | None = Field(default=None, max_length=500)


class PolicyPatch(BaseModel):
    environment: EnvName | None = None
    enforce_tool_allowlist: bool | None = None
    enforce_model_allowlist: bool | None = None
    allowed_tools: list[str] | None = None
    allowed_models: list[str] | None = None
    require_approval_for_high_risk: bool | None = None
    dlp_redact_secrets: bool | None = None
    audit_enabled: bool | None = None
    audit_retention_days: int | None = Field(default=None, ge=1, le=3650)
    audit_max_events: int | None = Field(default=None, ge=100, le=1_000_000)


class AuthorizeRequest(BaseModel):
    principal_id: str | None = None
    project_id: str | None = None
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
        if not self.state.get("tenants"):
            return False
        self._migrate()
        return True

    def _save(self) -> None:
        self.store_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _migrate(self) -> None:
        """Bring older governance stores up to current schema (idempotent)."""
        changed = False
        version = int(self.state.get("schema_version") or 1)
        if "projects" not in self.state or not isinstance(self.state.get("projects"), dict):
            self.state["projects"] = {}
            changed = True
        default_tenant = str(self.state.get("default_tenant_id") or "local")
        for tenant in (self.state.get("tenants") or {}).values():
            if not isinstance(tenant, dict):
                continue
            policy = dict(tenant.get("policy") or {})
            for key, value in _default_policy().items():
                if key not in policy:
                    policy[key] = value
                    changed = True
            # Preserve seed allowlist if already present.
            tenant["policy"] = policy
        if not any(
            item.get("tenant_id") == default_tenant and item.get("slug") == "default"
            for item in (self.state.get("projects") or {}).values()
            if isinstance(item, dict)
        ):
            project_id = "default" if default_tenant == "local" else str(uuid.uuid4())
            now = _now()
            self.state["projects"][project_id] = {
                "id": project_id,
                "tenant_id": default_tenant,
                "name": "默认项目",
                "slug": "default",
                "description": "本机默认项目作用域",
                "status": "active",
                "created_at": now,
                "updated_at": now,
                "policy": {},  # inherit tenant policy
            }
            self.state["default_project_id"] = project_id
            changed = True
        if not self.state.get("default_project_id"):
            projects = self.list_projects(tenant_id=default_tenant)
            if projects:
                self.state["default_project_id"] = projects[0]["id"]
                changed = True
        if version < SCHEMA_VERSION:
            self.state["schema_version"] = SCHEMA_VERSION
            changed = True
        if changed:
            self.state["updated_at"] = _now()
            self._save()

    def _seed(self) -> None:
        tenant_id = "local"
        owner_id = str(uuid.uuid4())
        project_id = "default"
        now = _now()
        policy = _default_policy()
        policy["allowed_tools"] = [
            "write_artifact",
            "upsert_memory_entity",
            "create_task",
        ]
        self.state = {
            "schema_version": SCHEMA_VERSION,
            "phase": "platformization",
            "default_tenant_id": tenant_id,
            "default_project_id": project_id,
            "tenants": {
                tenant_id: {
                    "id": tenant_id,
                    "name": "本机工作区",
                    "slug": "local",
                    "created_at": now,
                    "updated_at": now,
                    "policy": policy,
                }
            },
            "projects": {
                project_id: {
                    "id": project_id,
                    "tenant_id": tenant_id,
                    "name": "默认项目",
                    "slug": "default",
                    "description": "本机默认项目作用域",
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                    "policy": {},
                }
            },
            "principals": {
                owner_id: {
                    "id": owner_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
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
        tenant_id = tenant["id"] if tenant else None
        principals = self.list_principals(tenant_id=tenant_id)
        projects = self.list_projects(tenant_id=tenant_id)
        default_project_id = self.state.get("default_project_id")
        policy = self.effective_policy(tenant_id=tenant_id, project_id=default_project_id)
        gate_on = policy_gate_enabled()
        return {
            "phase": "3-platformization",
            "phase_label": "第三阶段：平台化",
            "schema_version": self.state.get("schema_version", SCHEMA_VERSION),
            "default_tenant_id": self.state.get("default_tenant_id"),
            "default_project_id": default_project_id,
            "tenant_count": len(self.state.get("tenants") or {}),
            "project_count": len(self.state.get("projects") or {}),
            "principal_count": len(principals),
            "policy": policy,
            "projects": projects[:20],
            "runtime_gate": {
                "enabled": gate_on,
                "env": "AETHERCHAT_PLATFORM_POLICY_GATE",
                "tool_path": gate_on,
                "model_path": gate_on,
                "enforce_tool_allowlist": bool(policy.get("enforce_tool_allowlist")),
                "enforce_model_allowlist": bool(policy.get("enforce_model_allowlist")),
            },
            "audit_retention": {
                "days": int(policy.get("audit_retention_days") or DEFAULT_AUDIT_RETENTION_DAYS),
                "max_events": int(policy.get("audit_max_events") or DEFAULT_AUDIT_MAX_EVENTS),
            },
            "capabilities": {
                "tenant_isolation": True,
                "project_scope": True,
                "session_project_binding": True,
                "rbac": True,
                "tool_allowlist": True,
                "model_allowlist": True,
                "audit_log": True,
                "audit_export": True,
                "audit_export_csv": True,
                "audit_retention": True,
                "runtime_policy_gate": gate_on,
                "sso_oidc": False,
                "scim": False,
                "team_spaces": "scaffold",
            },
            "notes": [
                "默认单租户本机模式；租户下可建项目并覆盖策略。",
                "会话/Agent Run 绑定 project_id；工具与模型门禁按项目生效策略。",
                "运行时门禁已接入工具调用与模型选择（可用 AETHERCHAT_PLATFORM_POLICY_GATE=0 关闭）。",
                "审计支持 JSON/JSONL/CSV 导出与按天/条数保留裁剪。",
                "SSO/OIDC/SCIM 未启用，后续按客户需求接入。",
                "WP-0 公共发布仍 No-Go，与平台化并行。",
            ],
        }

    # --- Policy helpers ---

    def _apply_policy_patch(self, policy: dict[str, Any], payload: PolicyPatch) -> dict[str, Any]:
        merged = dict(policy)
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            if value is None:
                continue
            if key in {"allowed_tools", "allowed_models"} and isinstance(value, list):
                merged[key] = [str(item).strip() for item in value if str(item).strip()][:200]
            else:
                merged[key] = value
        return merged

    def effective_policy(
        self,
        *,
        tenant_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Tenant policy merged with optional project overrides (project wins on set keys)."""
        resolved_tenant = tenant_id or self.state.get("default_tenant_id") or "local"
        tenant = self.get_tenant(str(resolved_tenant))
        base = dict(_default_policy())
        if tenant:
            base.update(tenant.get("policy") or {})
        if project_id:
            project = self.get_project(str(project_id))
            if project and str(project.get("tenant_id")) == str(resolved_tenant):
                overrides = project.get("policy") or {}
                if isinstance(overrides, dict):
                    for key, value in overrides.items():
                        if value is not None:
                            base[key] = value
        return base

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
            "policy": _default_policy(),
        }
        self.state.setdefault("tenants", {})[tenant_id] = tenant
        # Auto-create default project under new tenant.
        project_id = str(uuid.uuid4())
        self.state.setdefault("projects", {})[project_id] = {
            "id": project_id,
            "tenant_id": tenant_id,
            "name": "默认项目",
            "slug": "default",
            "description": f"{clean} 默认项目",
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "policy": {},
        }
        self.state["updated_at"] = now
        self._save()
        self.append_audit(
            action="tenant.create",
            actor_id=None,
            tenant_id=tenant_id,
            project_id=project_id,
            detail={"name": clean, "slug": slug_value, "default_project_id": project_id},
        )
        return tenant

    def patch_policy(self, tenant_id: str, payload: PolicyPatch) -> dict[str, Any]:
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            raise KeyError(tenant_id)
        data = payload.model_dump(exclude_unset=True)
        tenant["policy"] = self._apply_policy_patch(dict(tenant.get("policy") or {}), payload)
        tenant["updated_at"] = _now()
        self.state["tenants"][tenant_id] = tenant
        self.state["updated_at"] = tenant["updated_at"]
        self._save()
        self.append_audit(
            action="policy.patch",
            actor_id=None,
            tenant_id=tenant_id,
            detail={"keys": list(data.keys()), "scope": "tenant"},
        )
        return tenant

    # --- Projects ---

    def list_projects(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        rows = list((self.state.get("projects") or {}).values())
        if tenant_id:
            rows = [item for item in rows if item.get("tenant_id") == tenant_id]
        return sorted(rows, key=lambda item: str(item.get("name") or ""))

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        return (self.state.get("projects") or {}).get(project_id)

    def create_project(self, payload: ProjectCreate) -> dict[str, Any]:
        tenant_id = payload.tenant_id or self.state.get("default_tenant_id") or "local"
        if not self.get_tenant(tenant_id):
            raise ValueError("租户不存在")
        clean = " ".join(payload.name.split())
        if not clean:
            raise ValueError("项目名称不能为空")
        slug_value = (payload.slug or clean.lower().replace(" ", "-"))[:64]
        for item in self.list_projects(tenant_id=tenant_id):
            if item.get("slug") == slug_value:
                raise ValueError(f"项目 slug 已存在：{slug_value}")
        project_id = str(uuid.uuid4())
        now = _now()
        project: dict[str, Any] = {
            "id": project_id,
            "tenant_id": tenant_id,
            "name": clean,
            "slug": slug_value,
            "description": (payload.description or "").strip() or None,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "policy": {},
        }
        self.state.setdefault("projects", {})[project_id] = project
        self.state["updated_at"] = now
        self._save()
        self.append_audit(
            action="project.create",
            actor_id=None,
            tenant_id=tenant_id,
            project_id=project_id,
            detail={"name": clean, "slug": slug_value},
        )
        return project

    def patch_project_policy(self, project_id: str, payload: PolicyPatch) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise KeyError(project_id)
        data = payload.model_dump(exclude_unset=True)
        project["policy"] = self._apply_policy_patch(dict(project.get("policy") or {}), payload)
        project["updated_at"] = _now()
        self.state["projects"][project_id] = project
        self.state["updated_at"] = project["updated_at"]
        self._save()
        self.append_audit(
            action="policy.patch",
            actor_id=None,
            tenant_id=str(project.get("tenant_id")),
            project_id=project_id,
            detail={"keys": list(data.keys()), "scope": "project"},
        )
        return project

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
        project_id = payload.project_id or self.state.get("default_project_id")
        if project_id:
            project = self.get_project(project_id)
            if not project or project.get("tenant_id") != tenant_id:
                raise ValueError("项目不存在或不属于该租户")
        principal_id = str(uuid.uuid4())
        now = _now()
        item = {
            "id": principal_id,
            "tenant_id": tenant_id,
            "project_id": project_id,
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
            project_id=project_id,
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
        project_id = payload.project_id or self.state.get("default_project_id")
        principal = None
        if payload.principal_id:
            principal = self.get_principal(payload.principal_id)
            if principal:
                tenant_id = principal.get("tenant_id") or tenant_id
                project_id = principal.get("project_id") or project_id
        if payload.project_id:
            project_id = payload.project_id
        tenant = self.get_tenant(str(tenant_id))
        if not tenant:
            return {
                "allowed": False,
                "reason": "tenant_missing",
                "message": "租户不存在",
            }
        if project_id:
            project = self.get_project(str(project_id))
            if not project or str(project.get("tenant_id")) != str(tenant_id):
                return {
                    "allowed": False,
                    "reason": "project_missing",
                    "message": "项目不存在或不属于该租户",
                }
        policy = self.effective_policy(tenant_id=str(tenant_id), project_id=str(project_id) if project_id else None)
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
                    project_id=str(project_id) if project_id else None,
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
                    project_id=str(project_id) if project_id else None,
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
                    "project_id": project_id,
                }
                self.append_audit(
                    action="authorize.deny",
                    actor_id=(principal or {}).get("id"),
                    tenant_id=str(tenant_id),
                    project_id=str(project_id) if project_id else None,
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
                    "project_id": project_id,
                }
                self.append_audit(
                    action="authorize.deny",
                    actor_id=(principal or {}).get("id"),
                    tenant_id=str(tenant_id),
                    project_id=str(project_id) if project_id else None,
                    detail=decision,
                )
                return decision

        decision = {
            "allowed": True,
            "reason": "ok",
            "message": "允许",
            "role": role,
            "environment": policy.get("environment"),
            "project_id": project_id,
            "require_approval_for_high_risk": bool(policy.get("require_approval_for_high_risk")),
        }
        if record_allow and policy.get("audit_enabled"):
            self.append_audit(
                action="authorize.allow",
                actor_id=(principal or {}).get("id"),
                tenant_id=str(tenant_id),
                project_id=str(project_id) if project_id else None,
                detail={
                    "tool_name": tool_name or None,
                    "model_name": model_name or None,
                    "action": action,
                },
            )
        return decision

    def resolve_project_id(
        self,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> str | None:
        """Resolve governance project: explicit → session binding → default project."""
        if project_id and str(project_id).strip():
            return str(project_id).strip()
        if session_id:
            try:
                from .chat_database import chat_database

                row = chat_database.get_session(str(session_id)) or {}
                bound = str(row.get("project_id") or "").strip()
                if bound:
                    return bound
            except Exception:
                pass
        default = self.state.get("default_project_id")
        return str(default) if default else None

    def require_tool_invoke(
        self,
        tool_name: str,
        *,
        principal_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Runtime gate for tool paths. Raises PermissionError when denied."""
        if not policy_gate_enabled():
            return {"allowed": True, "reason": "gate_disabled", "skipped": True}
        clean = str(tool_name or "").strip()
        if not clean:
            raise PermissionError("工具名称为空，平台策略拒绝")
        resolved_project = self.resolve_project_id(
            project_id=project_id,
            session_id=session_id,
        )
        decision = self.authorize(
            AuthorizeRequest(
                principal_id=principal_id,
                project_id=resolved_project,
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
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Runtime gate for model selection. Raises PermissionError when denied."""
        if not policy_gate_enabled():
            return {"allowed": True, "reason": "gate_disabled", "skipped": True}
        clean = str(model_name or "").strip()
        if not clean:
            raise PermissionError("模型名称为空，平台策略拒绝")
        resolved_project = self.resolve_project_id(
            project_id=project_id,
            session_id=session_id,
        )
        decision = self.authorize(
            AuthorizeRequest(
                principal_id=principal_id,
                project_id=resolved_project,
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
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """Keep models that pass the runtime gate (order preserved)."""
        if not policy_gate_enabled():
            return list(names)
        allowed: list[str] = []
        for name in names:
            try:
                self.require_model_use(
                    str(name),
                    principal_id=principal_id,
                    project_id=project_id,
                    session_id=session_id,
                )
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
        project_id: str | None = None,
        detail: dict[str, Any] | None = None,
        prune: bool = True,
    ) -> dict[str, Any]:
        event = {
            "id": str(uuid.uuid4()),
            "ts": _now(),
            "action": action,
            "actor_id": actor_id,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "detail": detail or {},
        }
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        if prune and action != "audit.prune":
            # Best-effort retention; ignore prune errors so append never fails.
            with contextlib.suppress(Exception):
                self.prune_audit(tenant_id=tenant_id)
        return event

    def list_audit(
        self,
        *,
        limit: int = 50,
        tenant_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
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
            if project_id and item.get("project_id") != project_id:
                continue
            events.append(item)
            if len(events) >= max(1, min(5000, limit)):
                break
        return events

    def prune_audit(
        self,
        *,
        tenant_id: str | None = None,
        retention_days: int | None = None,
        max_events: int | None = None,
    ) -> dict[str, Any]:
        """Drop events older than retention or beyond max count (newest kept)."""
        policy = self.effective_policy(tenant_id=tenant_id)
        days = int(
            retention_days
            if retention_days is not None
            else policy.get("audit_retention_days") or DEFAULT_AUDIT_RETENTION_DAYS
        )
        cap = int(
            max_events
            if max_events is not None
            else policy.get("audit_max_events") or DEFAULT_AUDIT_MAX_EVENTS
        )
        days = max(1, min(3650, days))
        # Allow small caps for tests/manual prune; policy defaults stay higher.
        cap = max(1, min(1_000_000, cap))
        if not self.audit_path.is_file():
            return {"removed": 0, "kept": 0, "retention_days": days, "max_events": cap}

        cutoff = datetime.now(UTC) - timedelta(days=days)
        raw_lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        kept_lines: list[str] = []
        removed = 0
        for line in raw_lines:
            text = line.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except json.JSONDecodeError:
                removed += 1
                continue
            if tenant_id and item.get("tenant_id") not in {None, tenant_id}:
                # Keep other tenants' events when pruning a single tenant.
                kept_lines.append(json.dumps(item, ensure_ascii=False))
                continue
            ts = _parse_ts(str(item.get("ts") or ""))
            if ts is not None and ts < cutoff:
                removed += 1
                continue
            kept_lines.append(json.dumps(item, ensure_ascii=False))

        if len(kept_lines) > cap:
            # Keep the newest N lines (file is append-only chronological).
            overflow = len(kept_lines) - cap
            kept_lines = kept_lines[overflow:]
            removed += overflow

        self.audit_path.write_text(
            ("\n".join(kept_lines) + ("\n" if kept_lines else "")),
            encoding="utf-8",
        )
        result = {
            "removed": removed,
            "kept": len(kept_lines),
            "retention_days": days,
            "max_events": cap,
        }
        if removed:
            self.append_audit(
                action="audit.prune",
                actor_id=None,
                tenant_id=tenant_id,
                detail=result,
                prune=False,
            )
        return result

    def export_audit(
        self,
        *,
        limit: int = 500,
        tenant_id: str | None = None,
        project_id: str | None = None,
        format: ExportFormat = "json",
    ) -> dict[str, Any]:
        events = self.list_audit(limit=limit, tenant_id=tenant_id, project_id=project_id)
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
                "project_id": project_id,
                "body": body,
            }
        if format == "csv":
            buffer = io.StringIO()
            writer = csv.DictWriter(
                buffer,
                fieldnames=["id", "ts", "action", "actor_id", "tenant_id", "project_id", "detail"],
            )
            writer.writeheader()
            for item in events:
                writer.writerow(
                    {
                        "id": item.get("id") or "",
                        "ts": item.get("ts") or "",
                        "action": item.get("action") or "",
                        "actor_id": item.get("actor_id") or "",
                        "tenant_id": item.get("tenant_id") or "",
                        "project_id": item.get("project_id") or "",
                        "detail": json.dumps(item.get("detail") or {}, ensure_ascii=False),
                    }
                )
            return {
                "format": "csv",
                "exported_at": exported_at,
                "count": len(events),
                "tenant_id": tenant_id,
                "project_id": project_id,
                "body": buffer.getvalue(),
            }
        return {
            "format": "json",
            "exported_at": exported_at,
            "count": len(events),
            "tenant_id": tenant_id,
            "project_id": project_id,
            "events": events,
        }


platform_governance = PlatformGovernanceService()
