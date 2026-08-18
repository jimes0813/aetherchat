from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.services.agent_run_service import AgentRunService  # noqa: E402
from app.services.chat_database import ChatDatabase  # noqa: E402


def _run_with_completed_model(
    service: AgentRunService,
    session_id: str,
    *,
    content: str = "完成受控任务",
) -> dict:
    run = service.create(
        session_id,
        {"content": content, "targets": ["coder"], "mode": "project"},
    )
    service.observe(
        run["id"],
        {
            "type": "message_complete",
            "message": {
                "id": str(uuid4()),
                "author_model": "coder",
                "role": "assistant",
                "content": "结果已生成",
            },
        },
    )
    return run


def _complete_model(service: AgentRunService, run: dict) -> None:
    service.observe(
        run["id"],
        {
            "type": "message_complete",
            "message": {
                "id": str(uuid4()),
                "author_model": "coder",
                "role": "assistant",
                "content": "结果已生成",
            },
        },
    )


def _begin_effect(
    service: AgentRunService,
    run: dict,
    session_id: str,
    *,
    tool_name: str = "write_artifact",
) -> dict:
    return service.begin_tool_call(
        run_id=run["id"],
        session_id=session_id,
        model="coder",
        provider_type="openai_compat",
        provider_model="offline",
        provider_call_id=str(uuid4()),
        tool_name=tool_name,
        arguments={"path": "result.md", "content": "verified"},
        round_index=0,
        call_index=0,
    )


def test_model_only_run_is_not_reported_as_verified_completion(tmp_path):
    database = ChatDatabase(tmp_path / "model-only.sqlite3")
    service = AgentRunService(database)
    run = _run_with_completed_model(service, str(uuid4()))

    completed = service.finish(run["id"])

    assert completed["status"] == "completed_with_warnings"
    assert completed["outcome_status"] == "completed_with_warnings"
    assert "没有形成可独立核验" in completed["completion_summary"]
    event = database.list_agent_events(run["id"])[-1]
    assert event["event_type"] == "run_completed"
    assert event["payload"]["completion_status"] == "completed_with_warnings"


def test_verified_side_effect_produces_verified_completion(tmp_path):
    database = ChatDatabase(tmp_path / "verified.sqlite3")
    service = AgentRunService(database)
    session_id = str(uuid4())
    run = service.create(
        session_id,
        {"content": "写入工件", "targets": ["coder"], "mode": "project"},
    )
    call = _begin_effect(service, run, session_id)
    service.finish_tool_call(
        call["id"],
        {
            "tool": "write_artifact",
            "status": "success",
            "result": {"path": "result.md", "version": 1},
        },
        tool_run_id="tool-run-verified",
    )
    _complete_model(service, run)

    completed = service.finish(run["id"])

    assert completed["status"] == "completed_verified"
    assert completed["outcome_status"] == "completed_verified"
    assert completed["verified_effect_count"] == 1
    assert completed["error"] is None


def test_unverified_side_effect_produces_uncertain_completion(tmp_path):
    database = ChatDatabase(tmp_path / "unverified.sqlite3")
    service = AgentRunService(database)
    session_id = str(uuid4())
    run = service.create(
        session_id,
        {"content": "发送消息", "targets": ["coder"], "mode": "project"},
    )
    call = _begin_effect(
        service,
        run,
        session_id,
        tool_name="remote__send_message",
    )
    service.finish_tool_call(
        call["id"],
        {
            "tool": "remote__send_message",
            "status": "success",
            "result": {"ok": True},
        },
    )
    _complete_model(service, run)

    completed = service.finish(run["id"])

    assert completed["status"] == "uncertain"
    assert completed["outcome_status"] == "uncertain"
    assert completed["unverified_effect_count"] == 1
    assert "未核验" in completed["completion_summary"]


def test_failed_tool_result_cannot_produce_green_completion(tmp_path):
    database = ChatDatabase(tmp_path / "failed-tool.sqlite3")
    service = AgentRunService(database)
    session_id = str(uuid4())
    run = service.create(
        session_id,
        {"content": "写入工件", "targets": ["coder"], "mode": "project"},
    )
    call = _begin_effect(service, run, session_id)
    service.finish_tool_call(
        call["id"],
        {
            "tool": "write_artifact",
            "status": "error",
            "error": "write failed",
        },
    )
    _complete_model(service, run)

    completed = service.finish(run["id"])

    assert completed["status"] == "completed_with_warnings"
    assert completed["problem_tool_count"] == 1
    assert "失败" in completed["completion_summary"]


def test_legacy_completed_run_gets_conservative_outcome(tmp_path):
    database = ChatDatabase(tmp_path / "legacy.sqlite3")
    service = AgentRunService(database)
    run = _run_with_completed_model(service, str(uuid4()))
    database.update_agent_run(run["id"], "completed")

    legacy = database.get_agent_run(run["id"], include_events=False)

    assert legacy["status"] == "completed"
    assert legacy["outcome_status"] == "completed_with_warnings"
    assert "没有形成可独立核验" in legacy["completion_summary"]


def test_uncertain_tool_error_remains_uncertain_instead_of_failed(tmp_path):
    database = ChatDatabase(tmp_path / "uncertain-error.sqlite3")
    service = AgentRunService(database)
    session_id = str(uuid4())
    run = service.create(
        session_id,
        {"content": "执行远端副作用", "targets": ["coder"], "mode": "project"},
    )

    service.observe(
        run["id"],
        {
            "type": "error",
            "code": "tool_result_uncertain",
            "model": "coder",
            "message": "远端返回中断，副作用结果未知",
        },
    )
    finished = service.finish(run["id"])

    assert finished["status"] == "uncertain"
    assert finished["outcome_status"] == "uncertain"
    assert finished["steps"][0]["status"] == "interrupted"
