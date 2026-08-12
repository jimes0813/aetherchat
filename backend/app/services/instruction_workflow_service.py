"""Minimal instruction workflows: interval + webhook triggers for Skills.

Interval workflows create a linked automation schedule (reuses durable scheduler).
Webhook workflows fire by secret token and can dry-run or enqueue a background job.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .instruction_dry_run import dry_run_instructions
from .skills_rules_service import skills_rules_service


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _data_root() -> Path:
    root = Path(
        os.environ.get(
            "AETHERCHAT_DATA_DIR",
            Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AetherChat",
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=80)
    skill_id: str | None = None
    prompt_template: str = Field(
        default="请按当前技能与规则处理以下请求：\n{{message}}",
        min_length=1,
        max_length=20_000,
    )
    message: str = Field(default="", max_length=8_000)
    targets: list[str] = Field(default_factory=lambda: ["all"], min_length=1, max_length=20)
    trigger_type: Literal["interval", "webhook", "manual"] = "manual"
    interval_minutes: int | None = Field(default=None, ge=5, le=43_200)
    execution_mode: Literal["dry_run", "automation"] = "dry_run"
    enabled: bool = True
    max_budget_usd: float = Field(default=0.5, ge=0.01, le=50.0)


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    skill_id: str | None = None
    prompt_template: str | None = Field(default=None, min_length=1, max_length=20_000)
    message: str | None = Field(default=None, max_length=8_000)
    targets: list[str] | None = Field(default=None, min_length=1, max_length=20)
    interval_minutes: int | None = Field(default=None, ge=5, le=43_200)
    execution_mode: Literal["dry_run", "automation"] | None = None
    enabled: bool | None = None


class InstructionWorkflowService:
    def __init__(self) -> None:
        self.path = _data_root() / "instruction_workflows.json"
        self.workflows: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(rows, list):
            self.workflows = {
                str(item["id"]): item
                for item in rows
                if isinstance(item, dict) and item.get("id")
            }

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(list(self.workflows.values()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_workflows(self, session_id: str | None = None) -> list[dict[str, Any]]:
        rows = list(self.workflows.values())
        if session_id:
            rows = [item for item in rows if item.get("session_id") == session_id]
        return sorted(rows, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def get(self, workflow_id: str) -> dict[str, Any] | None:
        return self.workflows.get(workflow_id)

    def find_by_webhook_token(self, token: str) -> dict[str, Any] | None:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        for item in self.workflows.values():
            if item.get("trigger_type") != "webhook":
                continue
            if item.get("webhook_token_hash") == digest:
                return item
        return None

    def _build_prompt(self, workflow: dict[str, Any], message: str | None = None) -> str:
        skill_name = ""
        skill_body = ""
        skill_id = workflow.get("skill_id")
        if skill_id:
            skill = skills_rules_service.get_skill(str(skill_id))
            if skill:
                view = skills_rules_service._published_skill_view(skill)
                skill_name = str(view.get("name") or "")
                skill_body = str(view.get("body") or "")
        user_message = (message if message is not None else workflow.get("message") or "").strip()
        template = str(workflow.get("prompt_template") or "{{message}}")
        filled = (
            template
            .replace("{{message}}", user_message)
            .replace("{{skill_name}}", skill_name)
            .replace("{{skill_body}}", skill_body)
            .replace("{{workflow_name}}", str(workflow.get("name") or ""))
        )
        if skill_body and "{{skill_body}}" not in template:
            filled = (
                f"### 技能：{skill_name}\n{skill_body}\n\n"
                f"### 任务\n{filled}"
            )
        return filled.strip()

    def create(self, payload: WorkflowCreate) -> dict[str, Any]:
        if payload.trigger_type == "interval" and not payload.interval_minutes:
            raise ValueError("interval 触发必须设置 interval_minutes")
        if payload.skill_id and not skills_rules_service.get_skill(payload.skill_id):
            raise ValueError("skill_id 不存在")
        now = _iso()
        workflow_id = str(uuid.uuid4())
        item: dict[str, Any] = {
            "id": workflow_id,
            "name": payload.name.strip(),
            "session_id": payload.session_id,
            "skill_id": payload.skill_id,
            "prompt_template": payload.prompt_template.strip(),
            "message": (payload.message or "").strip(),
            "targets": list(payload.targets)[:20] or ["all"],
            "trigger_type": payload.trigger_type,
            "interval_minutes": payload.interval_minutes,
            "execution_mode": payload.execution_mode,
            "enabled": payload.enabled,
            "max_budget_usd": float(payload.max_budget_usd),
            "schedule_id": None,
            "webhook_token": None,
            "webhook_token_hash": None,
            "last_run_at": None,
            "last_status": None,
            "last_error": None,
            "last_result_summary": None,
            "created_at": now,
            "updated_at": now,
        }
        if payload.trigger_type == "webhook":
            token = secrets.token_urlsafe(24)
            item["webhook_token"] = token  # shown once on create
            item["webhook_token_hash"] = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if payload.trigger_type == "interval":
            schedule = self._create_linked_schedule(item)
            item["schedule_id"] = schedule["id"]
            # do not expose raw token fields for interval
        self.workflows[workflow_id] = item
        self._save()
        return item

    def _create_linked_schedule(self, workflow: dict[str, Any]) -> dict[str, Any]:
        from .chat_database import chat_database

        prompt = self._build_prompt(workflow)
        next_run = _now() + timedelta(minutes=int(workflow.get("interval_minutes") or 60))
        return chat_database.create_automation_schedule(
            session_id=str(workflow["session_id"]),
            name=f"[workflow] {workflow['name']}"[:200],
            prompt=prompt,
            mode="project",
            targets=list(workflow.get("targets") or ["all"]),
            trigger_type="interval",
            interval_minutes=int(workflow.get("interval_minutes") or 60),
            timezone="UTC",
            next_run_at=_iso(next_run),
            enabled=bool(workflow.get("enabled", True)),
            max_budget_usd=float(workflow.get("max_budget_usd") or 0.5),
            permission_snapshot={
                "policy_version": 1,
                "mode": "read_only",
                "allowed_tools": [],
                "captured_at": _iso(),
                "workflow_id": workflow["id"],
            },
        )

    def update(self, workflow_id: str, payload: WorkflowUpdate) -> dict[str, Any] | None:
        item = self.workflows.get(workflow_id)
        if not item:
            return None
        data = payload.model_dump(exclude_unset=True)
        if "skill_id" in data and data["skill_id"]:
            if not skills_rules_service.get_skill(str(data["skill_id"])):
                raise ValueError("skill_id 不存在")
        for key in ("name", "prompt_template", "message"):
            if key in data and data[key] is not None:
                item[key] = str(data[key]).strip()
        if "skill_id" in data:
            item["skill_id"] = data["skill_id"]
        if "targets" in data and data["targets"] is not None:
            item["targets"] = list(data["targets"])[:20] or ["all"]
        if "interval_minutes" in data and data["interval_minutes"] is not None:
            item["interval_minutes"] = int(data["interval_minutes"])
        if "execution_mode" in data and data["execution_mode"] is not None:
            item["execution_mode"] = data["execution_mode"]
        if "enabled" in data and data["enabled"] is not None:
            item["enabled"] = bool(data["enabled"])
        item["updated_at"] = _iso()
        # Sync interval schedule prompt/enable when linked
        if item.get("schedule_id") and item.get("trigger_type") == "interval":
            from .chat_database import chat_database

            chat_database.update_automation_schedule(
                str(item["schedule_id"]),
                str(item["session_id"]),
                {
                    "prompt": self._build_prompt(item),
                    "enabled": bool(item.get("enabled")),
                    "interval_minutes": item.get("interval_minutes"),
                    "targets_json": json.dumps(item.get("targets") or ["all"], ensure_ascii=False),
                },
            )
        self._save()
        return item

    def delete(self, workflow_id: str) -> bool:
        item = self.workflows.pop(workflow_id, None)
        if not item:
            return False
        schedule_id = item.get("schedule_id")
        if schedule_id:
            try:
                from .chat_database import chat_database

                chat_database.delete_automation_schedule(
                    str(schedule_id),
                    str(item["session_id"]),
                )
            except Exception:
                pass
        self._save()
        return True

    def fire(
        self,
        workflow: dict[str, Any],
        *,
        message: str | None = None,
        trigger: str = "manual",
    ) -> dict[str, Any]:
        if not workflow.get("enabled", True) and trigger != "manual":
            raise ValueError("工作流已停用")
        prompt = self._build_prompt(workflow, message=message)
        user_message = (message if message is not None else workflow.get("message") or "").strip()
        dry = dry_run_instructions(
            user_message=user_message or prompt[:500],
            session_id=str(workflow.get("session_id") or "") or None,
        )
        result: dict[str, Any] = {
            "workflow_id": workflow["id"],
            "trigger": trigger,
            "execution_mode": workflow.get("execution_mode") or "dry_run",
            "prompt": prompt,
            "dry_run": dry,
            "job": None,
        }
        mode = workflow.get("execution_mode") or "dry_run"
        if mode == "automation":
            job = self._enqueue_job(workflow, prompt=prompt, trigger=trigger)
            result["job"] = {
                "id": job.get("id"),
                "status": job.get("status"),
                "trigger_type": job.get("trigger_type"),
            }
            status = "queued"
        else:
            status = dry.get("status") or "ok"
        workflow["last_run_at"] = _iso()
        workflow["last_status"] = status
        workflow["last_error"] = None
        workflow["last_result_summary"] = (
            f"mode={mode}; layers={dry.get('layer_count')}; "
            f"permissions={dry.get('summary', {}).get('permission_count')}"
        )
        workflow["updated_at"] = _iso()
        # Hide token on subsequent reads except create
        if "webhook_token" in workflow and trigger != "create":
            workflow = dict(workflow)
            # keep stored secret hash only
            stored = self.workflows.get(str(workflow["id"]))
            if stored is not None:
                stored["last_run_at"] = workflow["last_run_at"]
                stored["last_status"] = workflow["last_status"]
                stored["last_error"] = workflow["last_error"]
                stored["last_result_summary"] = workflow["last_result_summary"]
                stored["updated_at"] = workflow["updated_at"]
                # never persist plaintext token after first save if already hashed
                if stored.get("webhook_token") and stored.get("webhook_token_hash"):
                    # keep token only until first successful load by UI - strip after fire
                    pass
        self._save()
        return result

    def _enqueue_job(
        self,
        workflow: dict[str, Any],
        *,
        prompt: str,
        trigger: str,
    ) -> dict[str, Any]:
        from .automation_service import automation_service
        from .chat_database import chat_database

        action = {
            "type": "post_message",
            "content": prompt,
            "targets": list(workflow.get("targets") or ["all"]),
            "mode": "project",
            "execution_strategy": "direct",
            "plan_confirmation_required": False,
            "max_revision_cycles": 0,
        }
        schedule_id = workflow.get("schedule_id")
        if schedule_id and trigger in {"manual", "interval"}:
            try:
                return automation_service.enqueue_schedule_now(
                    str(schedule_id),
                    str(workflow["session_id"]),
                )
            except KeyError:
                pass
        job = chat_database.create_background_job(
            schedule_id=str(schedule_id) if schedule_id else None,
            session_id=str(workflow["session_id"]),
            trigger_type="webhook" if trigger == "webhook" else "manual",
            action=action,
            permission_snapshot={
                "policy_version": 1,
                "mode": "read_only",
                "allowed_tools": [],
                "captured_at": _iso(),
                "workflow_id": workflow["id"],
            },
            max_budget_usd=float(workflow.get("max_budget_usd") or 0.5),
            scheduled_for=_iso(),
        )
        automation_service.wake()
        return job

    def public_view(self, item: dict[str, Any], *, include_token: bool = False) -> dict[str, Any]:
        row = dict(item)
        if not include_token:
            row.pop("webhook_token", None)
        row.pop("webhook_token_hash", None)
        row["has_webhook"] = item.get("trigger_type") == "webhook"
        row["webhook_path"] = (
            f"/instructions/workflows/hooks/{item.get('webhook_token') or '{token}'}"
            if item.get("trigger_type") == "webhook"
            else None
        )
        return row


instruction_workflow_service = InstructionWorkflowService()
