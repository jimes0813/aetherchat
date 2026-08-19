from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypedDict
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..services.automation_service import automation_service

router = APIRouter()


class AutomationScheduleCreate(BaseModel):
    session_id: UUID
    name: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=20_000)
    mode: Literal["casual", "roundtable", "project"] = "project"
    targets: list[str] = Field(default_factory=lambda: ["all"], min_length=1, max_length=20)
    trigger_type: Literal["once", "interval"]
    interval_minutes: int | None = Field(default=None, ge=5, le=43_200)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    next_run_at: datetime
    enabled: bool = True
    max_budget_usd: float = Field(default=1.0, ge=0.01, le=50.0)
    permission_mode: Literal["read_only"] = "read_only"


class AutomationSchedulePatch(BaseModel):
    session_id: UUID
    name: str | None = Field(default=None, min_length=1, max_length=200)
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    mode: Literal["casual", "roundtable", "project"] | None = None
    targets: list[str] | None = Field(default=None, min_length=1, max_length=20)
    trigger_type: Literal["once", "interval"] | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=43_200)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    next_run_at: datetime | None = None
    enabled: bool | None = None
    max_budget_usd: float | None = Field(default=None, ge=0.01, le=50.0)


class JobSessionRequest(BaseModel):
    session_id: UUID


class NormalizedSchedule(TypedDict):
    session_id: str
    name: str
    prompt: str
    mode: str
    targets: list[str]
    trigger_type: str
    interval_minutes: int | None
    timezone: str
    next_run_at: str
    enabled: bool
    max_budget_usd: float
    permission_snapshot: dict[str, Any]


def _normalized_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("执行时间必须包含时区")
    normalized = value.astimezone(UTC)
    if normalized > datetime.now(UTC) + timedelta(days=366 * 5):
        raise ValueError("执行时间不能超过五年")
    return normalized.isoformat()


def _normalized_targets(values: list[str]) -> list[str]:
    targets = [" ".join(str(value).split())[:120] for value in values]
    targets = list(dict.fromkeys(value for value in targets if value))
    if not targets:
        raise ValueError("至少选择一个模型目标")
    if "all" in targets:
        return ["all"]
    return targets[:20]


def _validated_schedule(payload: AutomationScheduleCreate) -> NormalizedSchedule:
    if payload.trigger_type == "interval" and payload.interval_minutes is None:
        raise ValueError("循环任务必须设置间隔分钟数")
    if payload.trigger_type == "once" and payload.interval_minutes is not None:
        raise ValueError("单次任务不能设置循环间隔")
    name = " ".join(payload.name.split())
    prompt = payload.prompt.strip()
    timezone_name = " ".join(payload.timezone.split())
    if not name or not prompt or not timezone_name:
        raise ValueError("名称、执行指令和时区不能为空")
    return {
        "session_id": str(payload.session_id),
        "name": name,
        "prompt": prompt,
        "mode": payload.mode,
        "targets": _normalized_targets(payload.targets),
        "trigger_type": payload.trigger_type,
        "interval_minutes": payload.interval_minutes,
        "timezone": timezone_name,
        "next_run_at": _normalized_datetime(payload.next_run_at),
        "enabled": payload.enabled,
        "max_budget_usd": round(float(payload.max_budget_usd), 4),
        "permission_snapshot": {
            "policy_version": 1,
            "mode": payload.permission_mode,
            "allowed_tools": [],
            "captured_at": datetime.now(UTC).isoformat(),
        },
    }


@router.get("/status")
async def scheduler_status():
    return automation_service.status()


@router.get("/schedules")
async def list_schedules(
    session_id: UUID,
    limit: int = Query(default=200, ge=1, le=500),
):
    return await automation_service.database.run_in_thread(
        automation_service.database.list_automation_schedules,
        str(session_id),
        limit,
    )


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule(payload: AutomationScheduleCreate):
    try:
        schedule = await automation_service.database.run_in_thread(
            automation_service.database.create_automation_schedule,
            **_validated_schedule(payload),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    automation_service.wake()
    return schedule


@router.patch("/schedules/{schedule_id}")
async def patch_schedule(schedule_id: UUID, payload: AutomationSchedulePatch):
    current = await automation_service.database.run_in_thread(
        automation_service.database.get_automation_schedule,
        str(schedule_id),
        session_id=str(payload.session_id),
    )
    if current is None:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    patch = payload.model_dump(exclude_unset=True)
    patch.pop("session_id", None)
    candidate = {
        "session_id": payload.session_id,
        "name": patch.get("name", current["name"]),
        "prompt": patch.get("prompt", current["prompt"]),
        "mode": patch.get("mode", current["mode"]),
        "targets": patch.get("targets", current["targets"]),
        "trigger_type": patch.get("trigger_type", current["trigger_type"]),
        "interval_minutes": patch.get(
            "interval_minutes",
            current.get("interval_minutes"),
        ),
        "timezone": patch.get("timezone", current["timezone"]),
        "next_run_at": patch.get("next_run_at", current.get("next_run_at")),
        "enabled": patch.get("enabled", current["enabled"]),
        "max_budget_usd": patch.get("max_budget_usd", current["max_budget_usd"]),
        "permission_mode": str(
            (current.get("permission_snapshot") or {}).get("mode") or "read_only"
        ),
    }
    if candidate["next_run_at"] is None:
        if candidate["enabled"] or candidate["trigger_type"] != "once":
            raise HTTPException(
                status_code=422,
                detail="重新启用单次任务前必须设置下一次执行时间",
            )
        name = " ".join(str(candidate["name"]).split())
        prompt = str(candidate["prompt"]).strip()
        timezone_name = " ".join(str(candidate["timezone"]).split())
        if not name or not prompt or not timezone_name:
            raise HTTPException(status_code=422, detail="名称、执行指令和时区不能为空")
        try:
            targets = _normalized_targets(list(candidate["targets"]))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        updates = {
            "name": name,
            "prompt": prompt,
            "mode": candidate["mode"],
            "targets_json": json.dumps(targets, ensure_ascii=False),
            "trigger_type": "once",
            "interval_minutes": None,
            "timezone": timezone_name,
            "next_run_at": None,
            "enabled": 0,
            "max_budget_usd": round(float(candidate["max_budget_usd"]), 4),
        }
    else:
        try:
            validated_model = AutomationScheduleCreate.model_validate(candidate)
            normalized = _validated_schedule(validated_model)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        updates = {
            "name": normalized["name"],
            "prompt": normalized["prompt"],
            "mode": normalized["mode"],
            "targets_json": json.dumps(normalized["targets"], ensure_ascii=False),
            "trigger_type": normalized["trigger_type"],
            "interval_minutes": normalized["interval_minutes"],
            "timezone": normalized["timezone"],
            "next_run_at": normalized["next_run_at"],
            "enabled": 1 if normalized["enabled"] else 0,
            "max_budget_usd": normalized["max_budget_usd"],
        }
    updated = await automation_service.database.run_in_thread(
        automation_service.database.update_automation_schedule,
        str(schedule_id),
        str(payload.session_id),
        updates,
    )
    if updated is None:  # pragma: no cover - checked before update
        raise HTTPException(status_code=404, detail="定时任务不存在")
    automation_service.wake()
    return updated


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: UUID, session_id: UUID):
    deleted = await automation_service.database.run_in_thread(
        automation_service.database.delete_automation_schedule,
        str(schedule_id),
        str(session_id),
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return {"deleted": True, "id": str(schedule_id)}


@router.post("/schedules/{schedule_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_schedule_now(schedule_id: UUID, payload: JobSessionRequest):
    try:
        return automation_service.enqueue_schedule_now(
            str(schedule_id),
            str(payload.session_id),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


@router.get("/jobs")
async def list_jobs(
    session_id: UUID,
    limit: int = Query(default=200, ge=1, le=500),
    review_only: bool = False,
):
    return await automation_service.database.run_in_thread(
        automation_service.database.list_background_jobs,
        str(session_id),
        limit=limit,
        review_only=review_only,
    )


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: UUID, payload: JobSessionRequest):
    try:
        return automation_service.cancel_job(str(job_id), str(payload.session_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


@router.post("/jobs/{job_id}/review")
async def review_job(job_id: UUID, payload: JobSessionRequest):
    reviewed = await automation_service.database.run_in_thread(
        automation_service.database.mark_background_job_reviewed,
        str(job_id),
        str(payload.session_id),
    )
    if reviewed is None:
        raise HTTPException(
            status_code=404,
            detail="后台任务不存在或尚未完成",
        )
    return reviewed
