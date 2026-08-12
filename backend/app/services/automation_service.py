from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from .agent_run_service import AgentRunService, agent_run_service
from .chat_database import ChatDatabase, chat_database
from .run_admission import admission_resources_for_action
from .run_dispatcher import (
    RunDispatcher,
    RunDispatcherStopped,
    RunDispatchHandle,
    RunOwnershipConflict,
    RunQueueFull,
    run_dispatcher,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class AutomationExecutionResult:
    status: str
    summary: str = ""
    error: str | None = None
    budget_spent_usd: float = 0.0


AutomationExecutor = Callable[[dict[str, Any]], Awaitable[AutomationExecutionResult]]
RunAdmissionResourceResolver = Callable[[dict[str, Any]], tuple[str, ...]]


class AutomationService:
    """Durable local scheduler with SQLite leases and fail-closed permissions."""

    def __init__(
        self,
        database: ChatDatabase | None = None,
        run_service: AgentRunService | None = None,
        *,
        poll_interval: float = 2.0,
        lease_seconds: int = 30,
        concurrency: int | None = None,
        executor: AutomationExecutor | None = None,
        dispatcher: RunDispatcher | None = None,
        resource_resolver: RunAdmissionResourceResolver | None = None,
    ) -> None:
        self.database = database or chat_database
        self.run_service = run_service or agent_run_service
        self.dispatcher = dispatcher or run_dispatcher
        configured_concurrency = concurrency
        if configured_concurrency is None:
            try:
                configured_concurrency = int(
                    os.environ.get("AETHERCHAT_BACKGROUND_CONCURRENCY", "1")
                )
            except ValueError:
                configured_concurrency = 1
        self.concurrency = max(1, min(4, configured_concurrency))
        self.poll_interval = max(0.1, float(poll_interval))
        self.lease_seconds = max(6, int(lease_seconds))
        self.max_attempts = 3
        self.worker_id = f"local-{uuid4()}"
        self._executor = executor
        self._resource_resolver = (
            resource_resolver or admission_resources_for_action
        )
        self._poll_task: asyncio.Task[None] | None = None
        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        self._dispatch_handles: dict[str, RunDispatchHandle] = {}
        self._wake = asyncio.Event()
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._poll_task is not None and not self._poll_task.done()

    def liveness_status(self) -> dict[str, Any]:
        dispatch_states = [
            handle.status().state for handle in self._dispatch_handles.values()
        ]
        return {
            "scheduler_running": self.running,
            "worker_id": self.worker_id,
            "active_workers": len(self._active_tasks),
            "concurrency": self.concurrency,
            "dispatch_queued": dispatch_states.count("queued"),
            "dispatch_running": sum(
                state in {"running", "cancelling"}
                for state in dispatch_states
            ),
        }

    def status(self) -> dict[str, Any]:
        return {
            **self.liveness_status(),
            **self.database.automation_status(),
        }

    async def start(self) -> None:
        if self.running:
            return
        self._stopping = False
        # TestClient and embedded desktop restarts may provide a fresh event
        # loop. asyncio synchronization primitives must not be reused across
        # those loop lifetimes.
        self._wake = asyncio.Event()
        self.worker_id = f"local-{uuid4()}"
        self._poll_task = asyncio.create_task(
            self._poll_loop(),
            name="aetherchat-automation-scheduler",
        )

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._poll_task is not None:
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
            self._poll_task = None
        active = list(self._active_tasks.values())
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        # A task can be cancelled before its coroutine receives its first event
        # loop turn, so its own CancelledError handler may never run. Reconcile
        # every still-owned durable lease before dropping the in-memory map.
        for job_id in tuple(self._active_tasks):
            current = self.database.get_background_job(job_id)
            if current is None or current.get("lease_owner") != self.worker_id:
                continue
            if current.get("status") == "cancel_requested":
                self._finish_cancelled_claim(job_id, current)
            elif current.get("status") == "running":
                run_id = str(current.get("agent_run_id") or "")
                if run_id:
                    self.run_service.finish(run_id, disconnected=True)
                self.database.requeue_background_job(
                    job_id,
                    worker_id=self.worker_id,
                    error="后台服务停止；将在下次启动后恢复",
                )
        self._active_tasks.clear()
        self._dispatch_handles.clear()

    def wake(self) -> None:
        self._wake.set()

    async def _poll_loop(self) -> None:
        try:
            while not self._stopping:
                await self.run_cycle()
                self._wake.clear()
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._wake.wait(),
                        timeout=self.poll_interval,
                    )
        except asyncio.CancelledError:
            raise

    async def run_cycle(self, now: datetime | None = None) -> list[dict[str, Any]]:
        current = (now or _utc_now()).astimezone(UTC)
        materialized = self.database.materialize_due_automation_jobs(_iso(current))
        for job_id, task in list(self._active_tasks.items()):
            if task.done():
                await asyncio.gather(task, return_exceptions=True)
                self._active_tasks.pop(job_id, None)

        while len(self._active_tasks) < self.concurrency:
            claimed = self.database.claim_background_job(
                worker_id=self.worker_id,
                now=_iso(current),
                lease_expires_at=_iso(current + timedelta(seconds=self.lease_seconds)),
            )
            if claimed is None:
                break
            task = asyncio.create_task(
                self._run_claimed_job(claimed),
                name=f"aetherchat-background-job:{claimed['id']}",
            )
            self._active_tasks[str(claimed["id"])] = task
        return materialized

    def enqueue_schedule_now(
        self,
        schedule_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        schedule = self.database.get_automation_schedule(
            schedule_id,
            session_id=session_id,
        )
        if schedule is None:
            raise KeyError("定时任务不存在")
        action = {
            "type": "post_message",
            "content": schedule["prompt"],
            "targets": schedule["targets"],
            "mode": schedule["mode"],
            "execution_strategy": "direct",
            "plan_confirmation_required": False,
            "max_revision_cycles": 0,
        }
        job = self.database.create_background_job(
            schedule_id=schedule["id"],
            session_id=schedule["session_id"],
            trigger_type="manual",
            action=action,
            permission_snapshot=schedule["permission_snapshot"],
            max_budget_usd=float(schedule["max_budget_usd"]),
            scheduled_for=_iso(_utc_now()),
        )
        self.database.update_automation_schedule(
            schedule["id"],
            schedule["session_id"],
            {"last_status": "queued", "last_error": None},
        )
        self.wake()
        return job

    def cancel_job(self, job_id: str, session_id: str) -> dict[str, Any]:
        job = self.database.request_background_job_cancel(job_id, session_id)
        if job is None:
            raise KeyError("后台任务不存在")
        task = self._active_tasks.get(str(job_id))
        if task is not None and not task.done():
            task.add_done_callback(
                lambda _task: self._finish_cancelled_claim(str(job_id))
            )
            task.cancel()
        self.wake()
        return job

    def _finish_cancelled_claim(
        self,
        job_id: str,
        current: dict[str, Any] | None = None,
    ) -> bool:
        claimed = current or self.database.get_background_job(job_id)
        if (
            claimed is None
            or claimed.get("status") != "cancel_requested"
            or claimed.get("lease_owner") != self.worker_id
        ):
            return False
        run_id = str(claimed.get("agent_run_id") or "")
        if run_id:
            self.run_service.database.update_agent_run(
                run_id,
                "interrupted",
                error="后台任务被用户取消",
            )
            self.run_service.database.append_agent_event(
                run_id,
                "run_interrupted",
                {"reason": "background_job_cancelled", "job_id": job_id},
            )
        return self.database.finish_background_job(
            job_id,
            worker_id=self.worker_id,
            status="cancelled",
            error="用户取消",
        ) is not None

    async def _heartbeat(self, job_id: str, parent: asyncio.Task[None]) -> None:
        delay = max(2.0, self.lease_seconds / 3)
        try:
            while True:
                await asyncio.sleep(delay)
                now = _utc_now()
                renewed = self.database.heartbeat_background_job(
                    job_id,
                    worker_id=self.worker_id,
                    now=_iso(now),
                    lease_expires_at=_iso(now + timedelta(seconds=self.lease_seconds)),
                )
                if not renewed:
                    parent.cancel()
                    return
        except asyncio.CancelledError:
            raise

    async def _acquire_dispatch(
        self,
        job: dict[str, Any],
        parent: asyncio.Task[None],
    ) -> RunDispatchHandle:
        job_id = str(job["id"])
        session_id = str(job["session_id"])
        resources = self._resource_resolver(dict(job.get("action") or {}))
        while True:
            current = self.database.get_background_job(job_id)
            if current is None or current.get("status") in {
                "cancel_requested",
                "cancelled",
            }:
                raise asyncio.CancelledError
            try:
                handle = await self.dispatcher.submit(
                    owner_id=f"automation:{self.worker_id}:{job_id}",
                    session_id=session_id,
                    kind="background",
                    resources=resources,
                )
            except (RunOwnershipConflict, RunQueueFull):
                accepting = await self.dispatcher.wait_for_submission_capacity(
                    session_id,
                    timeout=max(0.1, self.poll_interval),
                )
                if not accepting:
                    raise RunDispatcherStopped("运行调度器正在关闭") from None
                continue

            self._dispatch_handles[job_id] = handle
            try:
                await handle.bind_task(parent)
                await handle.wait_until_started()
            except (Exception, asyncio.CancelledError):
                await handle.release(cancelled=True)
                self._dispatch_handles.pop(job_id, None)
                raise
            return handle

    async def _run_claimed_job(self, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        parent = asyncio.current_task()
        if parent is None:  # pragma: no cover - only possible outside an event loop
            raise RuntimeError("后台任务未绑定事件循环")
        heartbeat = asyncio.create_task(
            self._heartbeat(job_id, parent),
            name=f"aetherchat-background-heartbeat:{job_id}",
        )
        dispatch_handle: RunDispatchHandle | None = None
        dispatch_cancelled = False
        try:
            dispatch_handle = await self._acquire_dispatch(job, parent)
            current = self.database.get_background_job(job_id) or job
            if current.get("status") == "cancel_requested":
                raise asyncio.CancelledError
            if current.get("lease_owner") != self.worker_id:
                raise RuntimeError("后台任务租约已丢失")
            if int(job.get("attempt") or 0) > self.max_attempts:
                result = AutomationExecutionResult(
                    status="failed",
                    error="后台任务连续恢复次数超过安全上限",
                )
            elif self._executor is not None:
                result = await self._executor(job)
            else:
                result = await self._execute_agent_job(job)
            self.database.finish_background_job(
                job_id,
                worker_id=self.worker_id,
                status=result.status,
                summary=result.summary,
                error=result.error,
                budget_spent_usd=result.budget_spent_usd,
            )
        except RunDispatcherStopped as exc:
            dispatch_cancelled = True
            self.database.requeue_background_job(
                job_id,
                worker_id=self.worker_id,
                error=f"{exc}；将在调度器恢复后重试",
            )
        except asyncio.CancelledError:
            dispatch_cancelled = True
            current = self.database.get_background_job(job_id) or job
            run_id = str(current.get("agent_run_id") or "")
            if current.get("status") == "cancel_requested":
                self._finish_cancelled_claim(job_id, current)
            elif self._stopping:
                if run_id:
                    self.run_service.finish(run_id, disconnected=True)
                self.database.requeue_background_job(
                    job_id,
                    worker_id=self.worker_id,
                    error="后台服务停止；将在下次启动后恢复",
                )
            raise
        except Exception as exc:
            error = str(exc)[:4000]
            run_id = str((self.database.get_background_job(job_id) or {}).get("agent_run_id") or "")
            if run_id:
                self.run_service.database.update_agent_run(run_id, "failed", error=error)
                self.run_service.database.append_agent_event(
                    run_id,
                    "run_failed",
                    {"error": error, "background_job_id": job_id},
                )
            self.database.finish_background_job(
                job_id,
                worker_id=self.worker_id,
                status="failed",
                error=error,
            )
        finally:
            if dispatch_handle is not None:
                await dispatch_handle.release(cancelled=dispatch_cancelled)
            self._dispatch_handles.pop(job_id, None)
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _execute_agent_job(
        self,
        job: dict[str, Any],
    ) -> AutomationExecutionResult:
        # Imported lazily to avoid constructing provider registries when unit
        # tests exercise only the durable scheduler/database layer.
        from .orchestrator import SessionOrchestrator

        action = dict(job.get("action") or {})
        permission_snapshot = dict(job.get("permission_snapshot") or {})
        allowed_tools = [
            str(value)
            for value in permission_snapshot.get("allowed_tools") or []
            if str(value).strip()
        ][:50]
        action["_background_job_id"] = str(job["id"])
        action["_background_allowed_tools"] = allowed_tools
        action["_background_read_only"] = not allowed_tools

        run_id = str(job.get("agent_run_id") or "")
        if run_id:
            existing = self.run_service.database.get_agent_run(
                run_id,
                include_events=False,
            )
            if existing is None:
                run_id = ""
            elif existing["status"] in {"failed", "interrupted", "recoverable"}:
                action, existing = self.run_service.prepare_retry(
                    run_id,
                    str(job["session_id"]),
                )
                action["_background_job_id"] = str(job["id"])
                action["_background_allowed_tools"] = allowed_tools
                action["_background_read_only"] = not allowed_tools
            elif existing["status"] == "completed":
                return AutomationExecutionResult(
                    status="completed",
                    summary="关联 Agent Run 已在崩溃前完成；未重复执行。",
                )
            else:
                raise RuntimeError(f"关联 Agent Run 状态不可恢复：{existing['status']}")

        if not run_id:
            run = self.run_service.create(str(job["session_id"]), action)
            run_id = str(run["id"])
            attached = self.database.attach_background_job_run(
                str(job["id"]),
                worker_id=self.worker_id,
                run_id=run_id,
            )
            if attached is None:
                raise RuntimeError("后台任务租约已丢失")

        orchestrator = SessionOrchestrator(
            str(job["session_id"]),
            tenant_id="local-background",
            mode=str(action.get("mode") or "project"),
        )
        maximum_budget = max(0.01, float(job.get("max_budget_usd") or 1.0))
        orchestrator.budget_remaining = maximum_budget
        orchestrator.begin_agent_run(run_id, action.get("_pending_steers") or [])
        action["_agent_run_id"] = run_id
        summary = ""
        remaining_budget = maximum_budget
        try:
            async for event in orchestrator.stream_user_message(action):
                self.run_service.observe(run_id, event)
                if event.get("type") == "cost_update":
                    raw_remaining_budget = event.get("budget_remaining")
                    with suppress(TypeError, ValueError):
                        remaining_budget = max(
                            0.0,
                            float(str(raw_remaining_budget)),
                        )
                if event.get("type") == "message_complete":
                    message = event.get("message") or {}
                    if message.get("role") == "assistant" and not event.get("partial"):
                        summary = str(message.get("content") or "")[:20_000]
        finally:
            orchestrator.end_agent_run(run_id)

        completed = self.run_service.finish(run_id)
        spent = max(0.0, round(maximum_budget - remaining_budget, 6))
        if completed is None:
            return AutomationExecutionResult(
                status="failed",
                error="Agent Run 完成后无法读取状态",
                budget_spent_usd=spent,
            )
        if completed["status"] == "completed":
            return AutomationExecutionResult(
                status="completed",
                summary=summary or "后台 Agent Run 已完成。",
                budget_spent_usd=spent,
            )
        if completed["status"] in {"waiting_plan_confirmation", "waiting_approval"}:
            return AutomationExecutionResult(
                status="waiting_review",
                summary=summary,
                error="无人值守任务需要人工确认，已停止并进入待复核。",
                budget_spent_usd=spent,
            )
        return AutomationExecutionResult(
            status="failed",
            summary=summary,
            error=str(completed.get("error") or "后台 Agent Run 未完成")[:4000],
            budget_spent_usd=spent,
        )


automation_service = AutomationService()
