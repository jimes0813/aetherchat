import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.models.schemas import ProviderConfig  # noqa: E402
from app.services import orchestrator as orchestrator_module  # noqa: E402
from app.services.agent_run_service import AgentRunService  # noqa: E402
from app.services.approval_service import approval_service  # noqa: E402
from app.services.chat_database import (  # noqa: E402
    ChatDatabase,
)
from app.services.orchestrator import SessionOrchestrator  # noqa: E402
from app.services.plugin_registry import plugin_registry  # noqa: E402
from app.services.tool_registry import tool_registry  # noqa: E402

# Split from tests/test_basic_orchestrator.py for domain isolation.
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


async def _collect_events(orchestrator, targets):
    return [
        event
        async for event in orchestrator.stream_user_message({
            "content": "offline orchestration",
            "targets": targets,
            "mode": "casual",
        })
    ]


def _orchestrator_with(providers):
    orchestrator = SessionOrchestrator(str(uuid4()))
    orchestrator.providers = {provider.config.name: provider for provider in providers}
    orchestrator.refresh_providers = lambda: None
    return orchestrator

@pytest.mark.asyncio
async def test_orchestrator_injects_configured_agent_persona():
    provider = _FakeProvider(
        "architect",
        persona="架构评审；优先识别边界、风险和验收条件",
    )
    orchestrator = _orchestrator_with([provider])

    await _collect_events(orchestrator, ["architect"])

    system_text = "\n".join(
        message.content for message in provider.received_messages if message.role == "system"
    )
    assert "稳定标识是：architect" in system_text
    assert "架构评审；优先识别边界、风险和验收条件" in system_text


class _ToolLoopProvider(_FakeProvider):
    def __init__(self):
        super().__init__("tool-loop")
        self.calls = 0
        self.received_observation = False

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield {
                "delta": "让我先检查历史请求；这段工具轮草稿不应显示。",
                "usage": None,
                "tool_calls": [{
                    "id": "call-1",
                    "name": "plugin__desktop_control__capabilities",
                    "arguments": {},
                }],
            }
            yield {"delta": "", "usage": {"prompt_tokens": 10, "completion_tokens": 2}}
            return
        observation = next(message for message in reversed(messages) if message.role == "tool")
        assistant_call = next(
            message for message in reversed(messages)
            if message.role == "assistant" and message.tool_calls
        )
        assert [call.id for call in assistant_call.tool_calls] == ["call-1"]
        assert assistant_call.tool_calls[0].name == "plugin__desktop_control__capabilities"
        assert observation.tool_call_id == "call-1"
        assert observation.name == "plugin__desktop_control__capabilities"
        assert observation.tool_error is False
        self.received_observation = "desktop-control" in observation.content
        assert observation.images and observation.images[0].startswith("data:image/png;base64,")
        yield {"delta": "闭环完成", "usage": None}
        yield {"delta": "", "usage": {"prompt_tokens": 12, "completion_tokens": 3}}


class _PseudoToolProvider(_FakeProvider):
    def __init__(self):
        super().__init__("pseudo-tool")
        self.calls = 0

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield {
                "delta": "让我先检查。\n\n请求执行工具：plugin__documents__inventory",
                "usage": None,
            }
        elif self.calls == 2:
            assert "立即发起结构化工具调用" in messages[-1].content
            yield {
                "delta": "",
                "usage": None,
                "tool_calls": [{
                    "id": "recovered-call",
                    "name": "plugin__documents__inventory",
                    "arguments": {},
                }],
            }
        else:
            assert any(message.role == "tool" for message in messages)
            yield {"delta": "已完成真实工具调用。", "usage": None}
        yield {"delta": "", "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


class _ApprovalToolProvider(_FakeProvider):
    def __init__(self):
        super().__init__("approval-model")
        self.calls = 0

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield {
                "delta": "",
                "usage": None,
                "tool_calls": [{
                    "id": "approval-call",
                    "name": "write_artifact",
                    "arguments": {"path": "approved.md", "content": "sensitive body"},
                }],
            }
        else:
            observation = next(message for message in reversed(messages) if message.role == "tool")
            assert '"status": "success"' in observation.content
            yield {"delta": "授权后已完成", "usage": None}
        yield {"delta": "", "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


class _RejectedApprovalToolProvider(_FakeProvider):
    def __init__(self):
        super().__init__("rejected-approval-model")
        self.calls = 0
        self.received_feedback = False

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield {
                "delta": "",
                "usage": None,
                "tool_calls": [{
                    "id": "rejected-approval-call",
                    "name": "write_artifact",
                    "arguments": {"path": "denied.md", "content": "must not be written"},
                }],
            }
        else:
            observation = next(
                message for message in reversed(messages)
                if message.role == "tool"
            )
            self.received_feedback = (
                '"status": "denied"' in observation.content
                and "不要写文件，改为只读总结" in observation.content
            )
            yield {"delta": "已按反馈改为只读总结", "usage": None}
        yield {"delta": "", "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


class _ParallelToolProvider(_FakeProvider):
    def __init__(self):
        super().__init__("parallel-tools")
        self.calls = 0
        self.received_native_results = False

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield {
                "delta": "",
                "usage": None,
                "tool_calls": [
                    {
                        "id": "call-documents",
                        "name": "plugin__documents__inventory",
                        "arguments": {},
                    },
                    {
                        "id": "call-desktop",
                        "name": "plugin__desktop_control__capabilities",
                        "arguments": {},
                    },
                ],
            }
        else:
            assistant_call = next(
                message for message in reversed(messages)
                if message.role == "assistant" and message.tool_calls
            )
            assert [call.id for call in assistant_call.tool_calls] == [
                "call-documents",
                "call-desktop",
            ]
            results = {
                message.tool_call_id: message
                for message in messages
                if message.role == "tool" and message.tool_call_id
            }
            assert results["call-documents"].name == "plugin__documents__inventory"
            assert '"plugin_id": "documents"' in results["call-documents"].content
            assert results["call-desktop"].name == "plugin__desktop_control__capabilities"
            assert '"plugin_id": "desktop-control"' in results["call-desktop"].content
            self.received_native_results = True
            yield {"delta": "并行结果已正确关联", "usage": None}
        yield {"delta": "", "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


@pytest.mark.asyncio
async def test_orchestrator_reenters_model_after_tool_and_attaches_observation_image(monkeypatch):
    provider = _ToolLoopProvider()
    orchestrator = _orchestrator_with([provider])

    async def fake_invoke(tool_name, session_id, caller, arguments, **_kwargs):
        return {
            "plugin_id": "desktop-control",
            "status": "success",
            "result": {
                "snapshot_id": "snapshot-1",
                "screenshot": {
                    "image_data_url": "data:image/png;base64,iVBORw0KGgo=",
                    "width": 1,
                    "height": 1,
                },
            },
        }

    monkeypatch.setattr(plugin_registry, "invoke_tool", fake_invoke)
    events = await _collect_events(orchestrator, ["tool-loop"])
    assert provider.calls == 2
    assert provider.received_observation is True
    assert any(event["type"] == "tool_run" for event in events)
    completed = next(
        event["message"]
        for event in events
        if event["type"] == "message_complete"
        and event["message"]["author_model"] == "tool-loop"
    )
    assert completed["content"] == "闭环完成"
    assert "让我先检查历史请求" not in completed["content"]
    assert not any(
        "让我先检查历史请求" in str(event.get("delta") or "")
        for event in events
    )
    assert any(
        event["type"] == "stream_reset"
        for event in events
    )
    assert any(
        event["type"] == "message_complete"
        and event["message"]["author_model"] == "tool-loop"
        and "闭环完成" in event["message"]["content"]
        for event in events
    )


@pytest.mark.asyncio
async def test_orchestrator_correlates_parallel_tool_results_by_call_id(monkeypatch):
    provider = _ParallelToolProvider()
    orchestrator = _orchestrator_with([provider])

    async def fake_invoke(tool_name, session_id, caller, arguments, **_kwargs):
        plugin_id = "documents" if "documents" in tool_name else "desktop-control"
        return {
            "plugin_id": plugin_id,
            "status": "success",
            "result": {"tool_name": tool_name},
        }

    monkeypatch.setattr(plugin_registry, "invoke_tool", fake_invoke)
    events = await _collect_events(orchestrator, ["parallel-tools"])

    assert provider.calls == 2
    assert provider.received_native_results is True
    assert len([event for event in events if event["type"] == "tool_run"]) == 2
    assert any(
        event["type"] == "message_complete"
        and event["message"]["content"] == "并行结果已正确关联"
        for event in events
    )


def test_orchestrator_repairs_missing_and_duplicate_tool_call_ids():
    calls = SessionOrchestrator._normalize_tool_calls([
        {"id": "stable-call", "name": "first", "arguments": {}},
        {"id": "stable-call", "name": "second", "arguments": {"value": 2}},
        {"id": "", "name": "third", "arguments": "raw"},
        {
            "id": "json-string",
            "name": "write_artifact",
            "arguments": '{"path":"parsed.md","content":"ok"}',
        },
    ])

    assert calls[0].id == "stable-call"
    assert len({call.id for call in calls}) == 4
    assert all(call.id for call in calls)
    assert calls[2].arguments == {"_raw": "raw"}
    assert calls[3].arguments == {"path": "parsed.md", "content": "ok"}


@pytest.mark.asyncio
async def test_orchestrator_stops_repeated_truncated_file_writes_before_execution():
    class TruncatedWriteProvider(_FakeProvider):
        def __init__(self):
            super().__init__("truncated-writer")
            self.calls = 0
            self.tool_names: list[set[str]] = []

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            self.tool_names.append({tool.name for tool in tools or []})
            yield {
                "delta": "",
                "usage": None,
                "tool_calls": [{
                    "id": f"truncated-{self.calls}",
                    "name": "plugin__desktop_control__write_document",
                    "arguments": {
                        "_raw": '{"path":"/tmp/long.md","data":{"text":"truncated'
                    },
                }],
            }
            yield {
                "delta": "",
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            }

    provider = TruncatedWriteProvider()
    orchestrator = _orchestrator_with([provider])

    async def unexpected_approval(*_args, **_kwargs):
        raise AssertionError("invalid arguments must be rejected before approval")

    orchestrator._authorize_model_tool = unexpected_approval
    events = [
        event
        async for event in orchestrator.stream_user_message({
            "content": "把这篇长文保存到桌面文件",
            "targets": [provider.config.name],
            "mode": "casual",
        })
    ]

    assert provider.calls == 3
    assert "plugin__desktop_control__write_document" in provider.tool_names[0]
    assert "plugin__desktop_control__write_document" not in provider.tool_names[1]
    assert sum(event.get("type") == "approval_required" for event in events) == 0
    assert any(
        event.get("code") == "invalid_file_tool_arguments"
        for event in events
    )
    assert any(
        event.get("type") == "message_complete"
        and "避免反复覆盖或产生空文件" in event["message"]["content"]
        for event in events
    )


@pytest.mark.asyncio
async def test_orchestrator_resumes_from_checkpoint_without_repeating_side_effect(tmp_path):
    database = ChatDatabase(tmp_path / "orchestrator-checkpoint.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    run = run_service.create(session_id, {
        "content": "只写入一次 checkpoint.md",
        "targets": ["checkpoint-model"],
        "mode": "project",
    })

    class FailingAfterToolProvider(_FakeProvider):
        def __init__(self):
            super().__init__("checkpoint-model")
            self.calls = 0

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield {
                    "delta": "",
                    "usage": None,
                    "tool_calls": [{
                        "id": "checkpoint-call",
                        "name": "write_artifact",
                        "arguments": {
                            "path": "checkpoint.md",
                            "content": "written once",
                        },
                    }],
                }
                yield {"delta": "", "usage": {"prompt_tokens": 4, "completion_tokens": 2}}
                return
            assert any(message.tool_call_id == "checkpoint-call" for message in messages)
            if False:
                yield {}
            raise RuntimeError("offline after completed tool round")

    first_provider = FailingAfterToolProvider()
    first = SessionOrchestrator(session_id)
    first.providers = {"checkpoint-model": first_provider}
    first.refresh_providers = lambda: None
    first.run_service = run_service
    first.begin_agent_run(run["id"])

    async def allow_tool(_queue, _model, _tool_name, _arguments):
        return True, ""

    first._authorize_model_tool = allow_tool
    tool_runs_before = len(tool_registry.list_runs(session_id=session_id, limit=None))
    first_events = []
    first_action = {
        "content": "只写入一次 checkpoint.md",
        "targets": ["checkpoint-model"],
        "mode": "project",
        "_agent_run_id": run["id"],
    }
    async for event in first.stream_user_message(first_action):
        first_events.append(event)
        run_service.observe(run["id"], event)
    failed = run_service.finish(run["id"])

    assert failed["status"] == "failed"
    assert any(event.get("code") == "provider_error" for event in first_events)
    assert len(tool_registry.list_runs(session_id=session_id, limit=None)) == tool_runs_before + 1
    assert database.latest_agent_checkpoint(run["id"], "checkpoint-model")["kind"] == "tool_round"

    class ResumeProvider(_FakeProvider):
        def __init__(self):
            super().__init__("checkpoint-model")
            self.calls = 0
            self.saw_checkpoint = False

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            self.saw_checkpoint = (
                any(message.tool_calls for message in messages)
                and any(message.tool_call_id == "checkpoint-call" for message in messages)
            )
            yield {"delta": "从检查点完成", "usage": None}
            yield {"delta": "", "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

    retry_action, _ = run_service.prepare_retry(run["id"], session_id)
    retry_action["_agent_run_id"] = run["id"]
    resumed_provider = ResumeProvider()
    resumed = SessionOrchestrator(session_id)
    resumed.providers = {"checkpoint-model": resumed_provider}
    resumed.refresh_providers = lambda: None
    resumed.run_service = run_service
    resumed.begin_agent_run(run["id"], retry_action.get("_pending_steers"))

    async for event in resumed.stream_user_message(retry_action):
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "uncertain"
    assert resumed_provider.calls == 1
    assert resumed_provider.saw_checkpoint is True
    assert len(tool_registry.list_runs(session_id=session_id, limit=None)) == tool_runs_before + 1


@pytest.mark.asyncio
async def test_orchestrator_applies_steer_before_stale_tool_side_effect(tmp_path):
    database = ChatDatabase(tmp_path / "orchestrator-steer.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    run = run_service.create(session_id, {
        "content": "先准备旧方案",
        "targets": ["steer-model"],
    })
    started = asyncio.Event()
    release = asyncio.Event()

    class SteerProvider(_FakeProvider):
        def __init__(self):
            super().__init__("steer-model")
            self.calls = 0
            self.saw_steer = False

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield {"delta": "旧方案", "usage": None}
                started.set()
                await release.wait()
                yield {
                    "delta": "",
                    "usage": None,
                    "tool_calls": [{
                        "id": "stale-write",
                        "name": "write_artifact",
                        "arguments": {
                            "path": "must-not-exist.md",
                            "content": "stale",
                        },
                    }],
                }
            else:
                self.saw_steer = any(
                    message.role == "user" and "改为只读分析" in message.content
                    for message in messages
                )
                yield {"delta": "已改为只读分析", "usage": None}
            yield {"delta": "", "usage": {"prompt_tokens": 2, "completion_tokens": 1}}

    provider = SteerProvider()
    orchestrator = SessionOrchestrator(session_id)
    orchestrator.providers = {"steer-model": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])
    action = {
        "content": "先准备旧方案",
        "targets": ["steer-model"],
        "_agent_run_id": run["id"],
    }

    async def collect():
        events = []
        async for event in orchestrator.stream_user_message(action):
            events.append(event)
            run_service.observe(run["id"], event)
        run_service.finish(run["id"])
        return events

    collecting = asyncio.create_task(collect())
    await asyncio.wait_for(started.wait(), timeout=1)
    steer = run_service.queue_steer(
        run_id=run["id"],
        session_id=session_id,
        content="改为只读分析，不要写文件",
        targets=["steer-model"],
    )
    orchestrator.queue_steer(steer)
    release.set()
    events = await asyncio.wait_for(collecting, timeout=2)

    assert provider.calls == 2
    assert provider.saw_steer is True
    assert any(event["type"] == "stream_reset" for event in events)
    assert any(event["type"] == "steer_applied" for event in events)
    assert not any(event["type"] == "tool_run" for event in events)
    assert database.list_agent_tool_calls(run["id"]) == []
    checkpoint = database.latest_agent_checkpoint(run["id"], "steer-model")
    assert checkpoint["kind"] == "completed"
    assert any(
        "改为只读分析" in message["content"]
        for message in checkpoint["state"]["messages"]
        if message["role"] == "user"
    )


@pytest.mark.asyncio
async def test_agent_run_drops_turn_limit_tool_draft_from_chat(tmp_path, monkeypatch):
    database = ChatDatabase(tmp_path / "orchestrator-turn-limit.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    run = run_service.create(session_id, {
        "content": "验证模型调用上限",
        "targets": ["turn-limit-model"],
    })

    class TurnLimitProvider(_FakeProvider):
        def __init__(self):
            super().__init__("turn-limit-model")

        async def stream_chat(self, messages, tools=None, **kwargs):
            yield {
                "delta": "尚未完成",
                "usage": None,
                "tool_calls": [{
                    "id": "turn-limit-call",
                    "name": "list_tasks",
                    "arguments": {},
                }],
            }
            yield {"delta": "", "usage": {"prompt_tokens": 2, "completion_tokens": 1}}

    monkeypatch.setattr(orchestrator_module, "MAX_MODEL_TURNS", 1)
    monkeypatch.setattr("app.services.orchestrator.stream_turns.MAX_MODEL_TURNS", 1)
    provider = TurnLimitProvider()
    orchestrator = SessionOrchestrator(session_id)
    orchestrator.providers = {"turn-limit-model": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])

    events = []
    async for event in orchestrator.stream_user_message({
        "content": "验证模型调用上限",
        "targets": ["turn-limit-model"],
        "_agent_run_id": run["id"],
    }):
        events.append(event)
        run_service.observe(run["id"], event)
    failed = run_service.finish(run["id"])

    assert failed["status"] == "failed"
    assert database.list_agent_steps(run["id"])[0]["status"] == "failed"
    assert any(event.get("code") == "model_turn_limit" for event in events)
    assert not any(
        event["type"] == "message_complete"
        and event["message"].get("role") == "assistant"
        for event in events
    )
    assert not any(event.get("delta") == "尚未完成" for event in events)


@pytest.mark.asyncio
async def test_orchestrator_recovers_textual_pseudo_tool_call():
    provider = _PseudoToolProvider()
    orchestrator = _orchestrator_with([provider])
    events = await _collect_events(orchestrator, ["pseudo-tool"])

    assert provider.calls == 3
    assert any(event["type"] == "stream_reset" for event in events)
    assert any(event["type"] == "tool_run" for event in events)
    completed = [
        event["message"]
        for event in events
        if event["type"] == "message_complete"
        and event["message"]["author_model"] == "pseudo-tool"
    ]
    assert completed[0]["content"] == "已完成真实工具调用。"
    assert "请求执行工具" not in completed[0]["content"]


@pytest.mark.asyncio
async def test_orchestrator_pauses_for_structured_chat_approval():
    provider = _ApprovalToolProvider()
    orchestrator = _orchestrator_with([provider])
    stream = orchestrator.stream_user_message({
        "content": "请更新项目说明",
        "targets": ["approval-model"],
        "mode": "project",
    })
    events = []
    async for event in stream:
        events.append(event)
        if event["type"] == "approval_required":
            approval = event["approval"]
            assert approval["tool_name"] == "write_artifact"
            assert approval["arguments"]["content"].startswith("[已隐藏")
            approval_service.decide(approval["id"], orchestrator.session_id, "allow_once")

    assert any(event["type"] == "approval_resolved" and event["decision"] == "allow_once" for event in events)
    assert any(event["type"] == "tool_run" and event["run"]["status"] == "success" for event in events)
    artifact_update = next(
        event for event in events
        if event["type"] == "artifact_update"
    )
    assert artifact_update["generated_files"][0]["path"] == "approved.md"
    assert any(
        event["type"] == "message_complete"
        and event["message"]["content"] == "授权后已完成"
        and event["message"]["generated_files"][0]["path"] == "approved.md"
        for event in events
    )


@pytest.mark.asyncio
async def test_orchestrator_returns_rejection_feedback_to_the_model():
    provider = _RejectedApprovalToolProvider()
    orchestrator = _orchestrator_with([provider])
    stream = orchestrator.stream_user_message({
        "content": "请更新项目说明",
        "targets": ["rejected-approval-model"],
        "mode": "project",
    })
    events = []
    async for event in stream:
        events.append(event)
        if event["type"] == "approval_required":
            approval_service.decide(
                event["approval"]["id"],
                orchestrator.session_id,
                "deny",
                "不要写文件，改为只读总结",
            )

    resolved = next(event for event in events if event["type"] == "approval_resolved")
    assert resolved["decision"] == "deny"
    assert resolved["feedback"] == "不要写文件，改为只读总结"
    assert provider.received_feedback is True
    assert any(
        event["type"] == "tool_run"
        and event["run"]["status"] == "error"
        and "不要写文件，改为只读总结" in event["run"]["error"]
        for event in events
    )
    assert any(
        event["type"] == "message_complete"
        and event["message"]["content"] == "已按反馈改为只读总结"
        for event in events
    )


@pytest.mark.asyncio
async def test_browser_navigation_requires_a_matching_desktop_receipt():
    requested_actions = []

    async def execute_desktop_action(action):
        requested_actions.append(action)
        return {
            "success": True,
            "kind": "external_navigation",
            "url": action["url"],
            "browser": action["browser"],
            "launcher": "firefox",
            "verification": "launcher_accepted",
            "accepted_at": "2026-08-01T10:05:52Z",
        }

    plugin_result = {
        "plugin_id": "browser-links",
        "plugin_name": "浏览器链接",
        "action": "open_url",
        "status": "success",
        "result": {
            "kind": "open_external",
            "url": "https://www.youtube.com",
            "browser": "firefox",
        },
    }
    orchestrator = SessionOrchestrator(
        str(uuid4()),
        desktop_action_handler=execute_desktop_action,
    )
    completed = await orchestrator._complete_desktop_action(
        "plugin__browser_links__open_url",
        plugin_result,
    )

    assert requested_actions == [{
        "kind": "open_external",
        "url": "https://www.youtube.com",
        "browser": "firefox",
    }]
    assert completed["result"]["execution_receipt"]["launcher"] == "firefox"
    assert SessionOrchestrator._requested_browser("请用 fierfox 打开") == "firefox"
    assert SessionOrchestrator._browser_execution_receipt({
        "status": "success",
        "result": plugin_result,
    }) is None

    without_desktop = SessionOrchestrator(str(uuid4()))
    with pytest.raises(RuntimeError, match="没有活动的 AetherChat 桌面客户端"):
        await without_desktop._complete_desktop_action(
            "plugin__browser_links__open_url",
            plugin_result,
        )


@pytest.mark.asyncio
async def test_orchestrator_uses_configured_cost_and_hard_stops_empty_budget():
    priced = _FakeProvider("priced", cost_in=1.0, cost_out=2.0)
    orchestrator = _orchestrator_with([priced])
    events = await _collect_events(orchestrator, ["priced"])

    complete = next(
        event["message"]
        for event in events
        if event["type"] == "message_complete"
        and event["message"]["author_model"] == "priced"
    )
    assert complete["cost_usd"] == 2.0
    assert orchestrator.budget_remaining == 8.0

    blocked = _FakeProvider("blocked", cost_in=1.0, cost_out=1.0)
    exhausted = _orchestrator_with([blocked])
    exhausted.budget_remaining = 0
    blocked_events = await _collect_events(exhausted, ["blocked"])
    assert blocked.called is False
    assert any(event.get("code") == "budget_exceeded" for event in blocked_events)


@pytest.mark.asyncio
async def test_provider_exception_finishes_and_single_interrupt_keeps_other_model_running():
    broken = _FakeProvider("broken", behavior="error")
    broken_orchestrator = _orchestrator_with([broken])
    broken_events = await asyncio.wait_for(
        _collect_events(broken_orchestrator, ["broken"]),
        timeout=1,
    )
    assert any(event.get("code") == "provider_error" for event in broken_events)

    slow_started = asyncio.Event()
    slow = _FakeProvider("slow", behavior="slow", started=slow_started)
    survivor = _FakeProvider("survivor", behavior="slow")
    orchestrator = _orchestrator_with([slow, survivor])
    collecting = asyncio.create_task(_collect_events(orchestrator, ["slow", "survivor"]))
    await asyncio.wait_for(slow_started.wait(), timeout=1)
    await asyncio.sleep(0.04)
    orchestrator.interrupt("slow")
    events = await asyncio.wait_for(collecting, timeout=2)

    assert any(event["type"] == "interrupted" and event["model"] == "slow" for event in events)
    assert not any(event["type"] == "interrupted" and event["model"] == "survivor" for event in events)
    assert any(
        event["type"] == "message_complete"
        and event["message"]["author_model"] == "survivor"
        for event in events
    )
