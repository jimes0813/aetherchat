import asyncio
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.models.schemas import ProviderConfig  # noqa: E402
from app.services.agent_run_service import AgentRunService  # noqa: E402
from app.services.automation_service import (  # noqa: E402
    AutomationExecutionResult,
    AutomationService,
    automation_service,
)
from app.services.chat_database import ChatDatabase  # noqa: E402
from app.services.orchestrator import SessionOrchestrator  # noqa: E402
from app.services.run_dispatcher import RunDispatcher  # noqa: E402
from app.services.tool_registry import tool_registry  # noqa: E402

# Split from tests/test_basic.py for domain isolation.
# Shared env/client fixtures live in tests/conftest.py.

class _FakeProvider:
    def __init__(self, name, *, cost_in=0.0, cost_out=0.0, behavior="normal", started=None, persona=None):
        self.config = ProviderConfig(
            name=name,
            type="openai_compat",
            model=f"{name}-model",
            api_key_ref=f"{name.upper()}_OFFLINE_KEY",
            cost_per_1k_input=cost_in,
            cost_per_1k_output=cost_out,
            persona=persona,
        )
        self.behavior = behavior
        self.started = started
        self.called = False
        self.received_messages = []

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.called = True
        self.received_messages = messages
        if self.started:
            self.started.set()
        if self.behavior == "error":
            if False:
                yield {}
            raise RuntimeError("offline provider failure")
        if self.behavior == "slow":
            for index in range(8):
                await asyncio.sleep(0.025)
                yield {"delta": f"{self.config.name}-{index}", "usage": None}
        else:
            await asyncio.sleep(0.01)
            yield {"delta": f"{self.config.name}-done", "usage": None}
        yield {"delta": "", "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}





def test_automation_api_crud_manual_run_and_review(client, monkeypatch):
    session_id = str(uuid4())

    async def fake_background_executor(job):
        assert job["permission_snapshot"]["mode"] == "read_only"
        assert job["permission_snapshot"]["allowed_tools"] == []
        return AutomationExecutionResult(
            status="completed",
            summary="后台摘要已生成",
            budget_spent_usd=0.0123,
        )

    monkeypatch.setattr(automation_service, "_executor", fake_background_executor)
    first_run = datetime.now(UTC) + timedelta(hours=2)
    payload = {
        "session_id": session_id,
        "name": "每小时只读摘要",
        "prompt": "只读取当前会话并生成摘要，不修改任何文件。",
        "mode": "project",
        "targets": ["all"],
        "trigger_type": "interval",
        "interval_minutes": 60,
        "timezone": "Asia/Chongqing",
        "next_run_at": first_run.isoformat(),
        "enabled": True,
        "max_budget_usd": 0.5,
        "permission_mode": "read_only",
    }
    created = client.post("/automations/schedules", json=payload)
    assert created.status_code == 201
    schedule = created.json()
    assert schedule["permission_snapshot"]["allowed_tools"] == []
    assert schedule["next_run_at"] == first_run.isoformat()

    rejected = client.post(
        "/automations/schedules",
        json={**payload, "name": "不安全任务", "permission_mode": "full_access"},
    )
    assert rejected.status_code == 422

    listed = client.get(
        "/automations/schedules",
        params={"session_id": session_id},
    )
    assert [item["id"] for item in listed.json()] == [schedule["id"]]

    paused = client.patch(
        f"/automations/schedules/{schedule['id']}",
        json={"session_id": session_id, "enabled": False},
    )
    assert paused.status_code == 200
    assert paused.json()["enabled"] is False

    queued = client.post(
        f"/automations/schedules/{schedule['id']}/run",
        json={"session_id": session_id},
    )
    assert queued.status_code == 202
    job_id = queued.json()["id"]

    completed_job = None
    for _ in range(100):
        completed_job = automation_service.database.get_background_job(
            job_id,
            session_id=session_id,
        )
        if completed_job["status"] == "completed":
            break
        time.sleep(0.02)
    assert completed_job is not None
    assert completed_job["status"] == "completed"
    assert completed_job["summary"] == "后台摘要已生成"
    assert completed_job["budget_spent_usd"] == pytest.approx(0.0123)
    assert completed_job["reviewed_at"] is None
    listed_jobs = client.get(
        "/automations/jobs",
        params={"session_id": session_id},
    )
    assert listed_jobs.status_code == 200
    assert listed_jobs.json()[0]["id"] == job_id

    reviewed = client.post(
        f"/automations/jobs/{job_id}/review",
        json={"session_id": session_id},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["reviewed_at"]
    assert client.delete(
        f"/automations/schedules/{schedule['id']}",
        params={"session_id": session_id},
    ).json()["deleted"] is True


@pytest.mark.asyncio
async def test_automation_scheduler_materializes_once_and_reclaims_expired_lease(tmp_path):
    database = ChatDatabase(tmp_path / "automations.sqlite3")
    dispatcher = RunDispatcher(capacity=1)
    session_id = str(uuid4())
    now = datetime.now(UTC).replace(microsecond=0)
    calls = []

    async def fake_executor(job):
        calls.append(job["id"])
        await asyncio.sleep(0)
        return AutomationExecutionResult(status="completed", summary="ok")

    schedule = database.create_automation_schedule(
        session_id=session_id,
        name="可恢复循环任务",
        prompt="生成只读摘要",
        mode="project",
        targets=["all"],
        trigger_type="interval",
        interval_minutes=30,
        timezone="UTC",
        next_run_at=(now - timedelta(minutes=95)).isoformat(),
        enabled=True,
        max_budget_usd=1.0,
        permission_snapshot={"policy_version": 1, "mode": "read_only", "allowed_tools": []},
    )
    service = AutomationService(
        database,
        AgentRunService(database),
        poll_interval=0.01,
        lease_seconds=6,
        concurrency=1,
        executor=fake_executor,
        dispatcher=dispatcher,
        resource_resolver=lambda _action: (),
    )
    materialized = await service.run_cycle(now)
    assert len(materialized) == 1
    await asyncio.gather(*list(service._active_tasks.values()))
    await service.run_cycle(now)
    first_jobs = database.list_background_jobs(session_id)
    assert len(first_jobs) == 1
    assert first_jobs[0]["status"] == "completed"
    assert first_jobs[0]["permission_snapshot"]["mode"] == "read_only"
    advanced = database.get_automation_schedule(schedule["id"], session_id=session_id)
    assert advanced is not None
    assert datetime.fromisoformat(advanced["next_run_at"]) > now

    stranded = database.create_background_job(
        schedule_id=None,
        session_id=session_id,
        trigger_type="manual",
        action={"content": "恢复我", "targets": ["all"], "mode": "project"},
        permission_snapshot={"mode": "read_only", "allowed_tools": []},
        max_budget_usd=1.0,
        scheduled_for=now.isoformat(),
    )
    claimed = database.claim_background_job(
        worker_id="dead-worker",
        now=now.isoformat(),
        lease_expires_at=(now - timedelta(seconds=1)).isoformat(),
    )
    assert claimed["id"] == stranded["id"]
    assert claimed["attempt"] == 1

    await service.run_cycle(now)
    await asyncio.gather(*list(service._active_tasks.values()))
    recovered = database.get_background_job(stranded["id"])
    assert recovered is not None
    assert recovered["status"] == "completed"
    assert recovered["attempt"] == 2
    assert calls.count(stranded["id"]) == 1
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_background_run_rejects_unapproved_side_effect_before_execution(tmp_path):
    database = ChatDatabase(tmp_path / "background-read-only.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "分析项目，并尝试写入一个文件",
        "targets": ["solo"],
        "mode": "casual",
        "_background_job_id": str(uuid4()),
        "_background_allowed_tools": [],
        "_background_read_only": True,
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class BackgroundProvider(_FakeProvider):
        def __init__(self):
            super().__init__("solo")
            self.calls = 0

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            assert "write_artifact" not in {tool.name for tool in (tools or [])}
            assert any(
                "无人值守执行" in message.content
                for message in messages
                if message.role == "system"
            )
            if self.calls == 1:
                yield {
                    "delta": "",
                    "usage": None,
                    "tool_calls": [{
                        "id": "blocked-background-write",
                        "name": "write_artifact",
                        "arguments": {
                            "path": "must-not-exist.md",
                            "content": "forbidden",
                        },
                    }],
                }
                return
            observation = next(
                message for message in reversed(messages) if message.role == "tool"
            )
            assert observation.tool_error is True
            assert "后台权限快照不允许" in observation.content
            yield {"delta": "已完成只读分析，未修改文件。", "usage": None}

    orchestrator = SessionOrchestrator(session_id, mode="casual")
    orchestrator.providers = {"solo": BackgroundProvider()}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])
    tool_runs_before = len(tool_registry.list_runs(session_id=session_id, limit=None))
    async for event in orchestrator.stream_user_message(action):
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "completed_with_warnings"
    assert len(tool_registry.list_runs(session_id=session_id, limit=None)) == tool_runs_before
    calls = database.list_agent_tool_calls(run["id"])
    assert len(calls) == 1
    assert calls[0]["status"] == "denied"
    assert calls[0]["result"]["status"] == "denied"
