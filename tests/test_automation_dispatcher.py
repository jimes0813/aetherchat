from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))


from app.services.agent_run_service import AgentRunService  # noqa: E402
from app.services.automation_service import (  # noqa: E402
    AutomationExecutionResult,
    AutomationService,
)
from app.services.chat_database import ChatDatabase  # noqa: E402
from app.services.run_dispatcher import RunDispatcher  # noqa: E402


async def _wait_until(predicate, *, attempts: int = 100) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached")


def _create_job(database: ChatDatabase, session_id: str) -> dict:
    database.create_session(session_id, "后台调度测试", "project")
    return database.create_background_job(
        schedule_id=None,
        session_id=session_id,
        trigger_type="manual",
        action={"content": "后台执行", "targets": ["test"], "mode": "project"},
        permission_snapshot={"mode": "read_only", "allowed_tools": []},
        max_budget_usd=1.0,
        scheduled_for=datetime.now(UTC).isoformat(),
    )


@pytest.mark.asyncio
async def test_background_job_keeps_database_lease_while_dispatch_queued(tmp_path):
    database = ChatDatabase(tmp_path / "background-admission.sqlite3")
    dispatcher = RunDispatcher(capacity=1)
    blocker = await dispatcher.submit(
        owner_id="interactive",
        session_id=str(uuid4()),
        kind="interactive",
        resources=("provider:test",),
    )
    session_id = str(uuid4())
    job = _create_job(database, session_id)
    executed = asyncio.Event()

    async def executor(_job):
        executed.set()
        return AutomationExecutionResult(status="completed", summary="ok")

    service = AutomationService(
        database,
        AgentRunService(database),
        poll_interval=0.1,
        lease_seconds=6,
        concurrency=1,
        executor=executor,
        dispatcher=dispatcher,
        resource_resolver=lambda _action: ("provider:test",),
    )
    await service.run_cycle()
    await _wait_until(lambda: dispatcher.snapshot().background_queued == 1)

    claimed = database.get_background_job(job["id"])
    assert claimed is not None
    assert claimed["status"] == "running"
    assert claimed["lease_owner"] == service.worker_id
    assert claimed["agent_run_id"] is None
    assert executed.is_set() is False
    assert service.liveness_status()["dispatch_queued"] == 1

    await blocker.release()
    await asyncio.gather(*list(service._active_tasks.values()))
    completed = database.get_background_job(job["id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["summary"] == "ok"
    assert executed.is_set() is True
    assert dispatcher.snapshot().background_running == 0
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_background_dispatch_queue_cancel_never_executes_job(tmp_path):
    database = ChatDatabase(tmp_path / "background-cancel.sqlite3")
    dispatcher = RunDispatcher(capacity=1)
    blocker = await dispatcher.submit(
        owner_id="interactive",
        session_id=str(uuid4()),
        kind="interactive",
    )
    session_id = str(uuid4())
    job = _create_job(database, session_id)
    executed = False

    async def executor(_job):
        nonlocal executed
        executed = True
        return AutomationExecutionResult(status="completed")

    service = AutomationService(
        database,
        AgentRunService(database),
        poll_interval=0.1,
        lease_seconds=6,
        concurrency=1,
        executor=executor,
        dispatcher=dispatcher,
        resource_resolver=lambda _action: (),
    )
    await service.run_cycle()
    await _wait_until(lambda: dispatcher.snapshot().background_queued == 1)

    cancelled = service.cancel_job(job["id"], session_id)
    assert cancelled["status"] == "cancel_requested"
    await asyncio.gather(
        *list(service._active_tasks.values()),
        return_exceptions=True,
    )
    persisted = database.get_background_job(job["id"])
    assert persisted is not None
    assert persisted["status"] == "cancelled"
    assert persisted["agent_run_id"] is None
    assert executed is False
    assert dispatcher.snapshot().background_queued == 0

    await blocker.release()
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_background_cancel_before_first_task_turn_finalizes_durable_job(tmp_path):
    database = ChatDatabase(tmp_path / "background-early-cancel.sqlite3")
    dispatcher = RunDispatcher(capacity=1)
    session_id = str(uuid4())
    job = _create_job(database, session_id)
    executed = False

    async def executor(_job):
        nonlocal executed
        executed = True
        return AutomationExecutionResult(status="completed")

    service = AutomationService(
        database,
        AgentRunService(database),
        poll_interval=0.1,
        lease_seconds=6,
        concurrency=1,
        executor=executor,
        dispatcher=dispatcher,
        resource_resolver=lambda _action: (),
    )
    await service.run_cycle()
    cancelled = service.cancel_job(job["id"], session_id)
    assert cancelled["status"] == "cancel_requested"
    await asyncio.gather(
        *list(service._active_tasks.values()),
        return_exceptions=True,
    )

    persisted = database.get_background_job(job["id"])
    assert persisted is not None
    assert persisted["status"] == "cancelled"
    assert persisted["lease_owner"] is None
    assert persisted["agent_run_id"] is None
    assert executed is False
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_expired_cancel_request_is_finalized_after_worker_restart(tmp_path):
    database = ChatDatabase(tmp_path / "background-stale-cancel.sqlite3")
    session_id = str(uuid4())
    job = _create_job(database, session_id)
    now = datetime.now(UTC)
    claimed = database.claim_background_job(
        worker_id="dead-worker",
        now=now.isoformat(),
        lease_expires_at=(now - timedelta(seconds=1)).isoformat(),
    )
    assert claimed is not None
    assert claimed["id"] == job["id"]
    assert database.request_background_job_cancel(
        job["id"],
        session_id,
    )["status"] == "cancel_requested"
    executed = False

    async def executor(_job):
        nonlocal executed
        executed = True
        return AutomationExecutionResult(status="completed")

    dispatcher = RunDispatcher(capacity=1)
    service = AutomationService(
        database,
        AgentRunService(database),
        poll_interval=0.1,
        lease_seconds=6,
        concurrency=1,
        executor=executor,
        dispatcher=dispatcher,
        resource_resolver=lambda _action: (),
    )
    await service.run_cycle(now + timedelta(seconds=1))
    persisted = database.get_background_job(job["id"])
    assert persisted is not None
    assert persisted["status"] == "cancelled"
    assert persisted["lease_owner"] is None
    assert persisted["completed_at"] is not None
    assert service._active_tasks == {}
    assert executed is False
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_background_dispatch_requeues_on_stop_and_recovers_once(tmp_path):
    database = ChatDatabase(tmp_path / "background-restart.sqlite3")
    session_id = str(uuid4())
    job = _create_job(database, session_id)
    calls: list[str] = []

    async def executor(claimed):
        calls.append(str(claimed["id"]))
        return AutomationExecutionResult(status="completed", summary="recovered")

    first_dispatcher = RunDispatcher(capacity=1)
    blocker = await first_dispatcher.submit(
        owner_id="interactive",
        session_id=session_id,
        kind="interactive",
    )
    first_service = AutomationService(
        database,
        AgentRunService(database),
        poll_interval=0.1,
        lease_seconds=6,
        concurrency=1,
        executor=executor,
        dispatcher=first_dispatcher,
        resource_resolver=lambda _action: (),
    )
    await first_service.run_cycle()
    await _wait_until(
        lambda: (database.get_background_job(job["id"]) or {}).get("status")
        == "running"
    )
    assert calls == []

    await first_service.stop()
    requeued = database.get_background_job(job["id"])
    assert requeued is not None
    assert requeued["status"] == "queued"
    assert requeued["lease_owner"] is None
    assert requeued["agent_run_id"] is None
    await blocker.release()
    await first_dispatcher.stop()

    second_dispatcher = RunDispatcher(capacity=1)
    second_service = AutomationService(
        database,
        AgentRunService(database),
        poll_interval=0.1,
        lease_seconds=6,
        concurrency=1,
        executor=executor,
        dispatcher=second_dispatcher,
        resource_resolver=lambda _action: (),
    )
    await second_service.run_cycle(datetime.now(UTC) + timedelta(seconds=1))
    await asyncio.gather(*list(second_service._active_tasks.values()))
    recovered = database.get_background_job(job["id"])
    assert recovered is not None
    assert recovered["status"] == "completed"
    assert recovered["summary"] == "recovered"
    assert recovered["attempt"] == 2
    assert calls == [job["id"]]
    await second_dispatcher.stop()
