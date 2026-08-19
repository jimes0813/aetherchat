import asyncio
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.models.schemas import ProviderConfig  # noqa: E402
from app.services import git_workspace_service as git_workspace_module  # noqa: E402
from app.services.agent_run_service import AgentRunService  # noqa: E402
from app.services.chat_database import (  # noqa: E402
    ChatDatabase,
)
from app.services.git_workspace_service import (  # noqa: E402
    GitWorkspaceService,
)
from app.services.orchestrator import SessionOrchestrator  # noqa: E402
from app.services.tool_registry import tool_registry  # noqa: E402

# Split from tests/test_basic_orchestrator.py for domain isolation.
# Shared env/client fixtures live in tests/conftest.py.

def _initialize_test_git_repository(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(root), "init", "-q"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "tests@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "AetherChat Tests"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "core.autocrlf", "false"],
        check=True,
    )
    (root / "app.py").write_bytes(b"VALUE = 1\n")
    subprocess.run(["git", "-C", str(root), "add", "app.py"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "initial"],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()


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
async def test_project_plan_waits_for_confirmation_before_model_calls(tmp_path):
    database = ChatDatabase(tmp_path / "planned-confirmation.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "先展示计划，确认后再执行",
        "targets": ["planner-model"],
        "mode": "project",
        "execution_strategy": "planned",
        "plan_confirmation_required": True,
        "max_revision_cycles": 1,
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class ConfirmationProvider(_FakeProvider):
        def __init__(self):
            super().__init__("planner-model", persona="Architect/Reviewer")
            self.calls = []

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            if "类型：verifier" in system_text:
                self.calls.append("verifier")
                content = json.dumps({
                    "verdict": "pass",
                    "summary": "候选满足目标",
                    "evidence": ["候选"],
                    "issues": [],
                }, ensure_ascii=False)
            elif "类型：synthesizer" in system_text:
                self.calls.append("synthesizer")
                content = "确认后生成的最终交付"
            else:
                self.calls.append("worker")
                content = "确认后执行的候选"
            yield {"delta": content, "usage": None}

    provider = ConfirmationProvider()
    draft_orchestrator = SessionOrchestrator(session_id, mode="project")
    draft_orchestrator.providers = {"planner-model": provider}
    draft_orchestrator.refresh_providers = lambda: None
    draft_orchestrator.run_service = run_service
    draft_orchestrator.begin_agent_run(run["id"])

    draft_events = []
    async for event in draft_orchestrator.stream_user_message(action):
        draft_events.append(event)
        run_service.observe(run["id"], event)
    waiting = run_service.finish(run["id"])

    assert waiting["status"] == "waiting_plan_confirmation"
    assert waiting["plan_status"] == "draft"
    assert provider.calls == []
    assert any(
        event["type"] == "plan_confirmation_required"
        for event in draft_events
    )
    assert not any(
        event["type"] == "plan_step_update"
        for event in draft_events
    )

    confirmed_action, _ = run_service.prepare_confirmed_plan(
        run_id=run["id"],
        session_id=session_id,
        expected_revision=1,
    )
    confirmed_action["_agent_run_id"] = run["id"]
    executing = SessionOrchestrator(session_id, mode="project")
    executing.providers = {"planner-model": provider}
    executing.refresh_providers = lambda: None
    executing.run_service = run_service
    executing.begin_agent_run(run["id"])

    execution_events = []
    async for event in executing.stream_user_message(confirmed_action):
        execution_events.append(event)
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "completed_with_warnings"
    assert completed["plan_status"] == "completed"
    assert provider.calls == ["worker", "verifier", "synthesizer"]
    assert len([
        event for event in execution_events
        if event["type"] == "message_complete"
        and event["message"]["role"] == "assistant"
    ]) == 1


@pytest.mark.asyncio
async def test_project_mode_executes_worker_verifier_synthesizer_dag(tmp_path):
    database = ChatDatabase(tmp_path / "planned-orchestrator.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "形成一份经过双模型核验的迁移方案",
        "targets": ["researcher", "reviewer"],
        "mode": "project",
        "execution_strategy": "planned",
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class PlannedProvider(_FakeProvider):
        def __init__(self, name, persona):
            super().__init__(name, persona=persona)
            self.calls = []
            self.saw_dependency_bundle = False
            self.tool_policies = {}

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            if "类型：verifier" in system_text:
                phase = "verifier"
                self.saw_dependency_bundle = all(
                    value in "\n".join(message.content for message in messages)
                    for value in ("候选-researcher", "候选-reviewer")
                )
                content = json.dumps({
                    "verdict": "pass",
                    "summary": "两份候选互相补充且未发现冲突",
                    "evidence": ["候选-researcher", "候选-reviewer"],
                    "issues": [],
                }, ensure_ascii=False)
            elif "类型：synthesizer" in system_text:
                phase = "synthesizer"
                combined = "\n".join(message.content for message in messages)
                self.saw_dependency_bundle = (
                    "候选-researcher" in combined
                    and "候选-reviewer" in combined
                    and '"verdict": "pass"' in combined
                )
                content = "最终迁移方案：先验证兼容性，再分阶段迁移，并保留回滚证据。"
            else:
                phase = "worker"
                content = f"候选-{self.config.name}"
            self.calls.append(phase)
            tool_names = {tool.name for tool in (tools or [])}
            self.tool_policies[phase] = {
                "has_list_tasks": "list_tasks" in tool_names,
                "has_write_artifact": "write_artifact" in tool_names,
            }
            yield {"delta": content, "usage": None}
            yield {
                "delta": "",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }

    researcher = PlannedProvider("researcher", "Researcher")
    reviewer = PlannedProvider("reviewer", "Architect/Reviewer")
    orchestrator = SessionOrchestrator(session_id, mode="project")
    orchestrator.providers = {
        "researcher": researcher,
        "reviewer": reviewer,
    }
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])

    events = []
    async for event in orchestrator.stream_user_message(action):
        events.append(event)
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "completed_with_warnings"
    assert researcher.calls == ["worker"]
    assert reviewer.calls == ["worker", "verifier", "synthesizer"]
    assert reviewer.saw_dependency_bundle is True
    assert researcher.tool_policies["worker"] == {
        "has_list_tasks": True,
        "has_write_artifact": False,
    }
    assert reviewer.tool_policies["verifier"]["has_write_artifact"] is False
    assert reviewer.tool_policies["synthesizer"]["has_write_artifact"] is True
    assert len([
        event for event in events
        if event["type"] == "message_complete"
        and event["message"]["role"] == "assistant"
    ]) == 1
    assert len([
        event for event in events if event["type"] == "plan_step_output"
    ]) == 3
    final_message = next(
        event["message"]
        for event in events
        if event["type"] == "message_complete"
        and event["message"]["role"] == "assistant"
    )
    assert final_message["content"].startswith("最终迁移方案")

    detail = database.get_agent_run(run["id"])
    assert {step["status"] for step in detail["plan_steps"]} == {"completed"}
    assert detail["review_count"] == 1
    assert detail["reviews"][0]["verdict"] == "pass"
    assert detail["reviews"][0]["evidence"] == [
        "候选-researcher", "候选-reviewer",
    ]
    assert any(event["type"] == "plan_created" for event in events)
    assert any(event["type"] == "review_recorded" for event in events)


@pytest.mark.asyncio
async def test_planned_code_run_uses_scoped_worktree_tools_and_real_evidence(
    tmp_path,
    monkeypatch,
):
    repository_root = tmp_path / "orchestrated-repository"
    _initialize_test_git_repository(repository_root)
    database = ChatDatabase(tmp_path / "planned-code.sqlite3")
    workspace_service = GitWorkspaceService(
        database,
        worktree_root=tmp_path / "managed-worktrees",
    )
    run_service = AgentRunService(database, workspace_service)
    monkeypatch.setattr(
        git_workspace_module,
        "git_workspace_service",
        workspace_service,
    )
    session_id = str(uuid4())
    registered = workspace_service.register_repository(
        session_id=session_id,
        selected_path=str(repository_root),
    )
    action = {
        "content": "在隔离 worktree 中把 VALUE 改为 3",
        "targets": ["code-agent"],
        "mode": "project",
        "execution_strategy": "planned",
        "repository_id": registered["id"],
        "verification_commands": ["python -m pytest -q"],
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    async def passing_sandbox(*args, **kwargs):
        return {
            "stdout": "1 passed\n",
            "exit_code": 0,
            "timed_out": False,
            "network": "none",
        }

    monkeypatch.setattr(
        git_workspace_module,
        "execute_in_sandbox",
        passing_sandbox,
    )

    class CodeProvider(_FakeProvider):
        def __init__(self):
            super().__init__("code-agent", persona="Implementer/Reviewer")
            self.worker_turns = 0
            self.saw_deterministic_evidence = False

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            combined = "\n".join(message.content for message in messages)
            tool_names = {tool.name for tool in (tools or [])}
            if "类型：worker" in system_text:
                self.worker_turns += 1
                assert {
                    "workspace_list_files",
                    "workspace_read_file",
                    "workspace_write_file",
                    "workspace_run_command",
                    "workspace_status",
                } <= tool_names
                assert "write_artifact" not in tool_names
                if self.worker_turns == 1:
                    yield {
                        "delta": "",
                        "usage": None,
                        "tool_calls": [{
                            "id": "write-isolated-worktree",
                            "name": "workspace_write_file",
                            "arguments": {
                                "path": "app.py",
                                "content": "VALUE = 3\n",
                            },
                        }],
                    }
                else:
                    assert any(
                        message.role == "tool"
                        and message.name == "workspace_write_file"
                        and not message.tool_error
                        for message in messages
                    )
                    yield {"delta": "已在隔离 worktree 修改 app.py", "usage": None}
                return
            if "类型：verifier" in system_text:
                self.saw_deterministic_evidence = (
                    "deterministic_workspace_verification" not in combined
                    and '"verification_status": "passed"' in combined
                    and '"exit_code": 0' in combined
                )
                yield {
                    "delta": json.dumps({
                        "verdict": "pass",
                        "summary": "diff 与测试证据通过",
                        "evidence": ["AetherChat deterministic verifier"],
                        "issues": [],
                    }, ensure_ascii=False),
                    "usage": None,
                }
                return
            assert "类型：synthesizer" in system_text
            yield {"delta": "候选已核验，等待用户审核并应用 diff。", "usage": None}

    provider = CodeProvider()
    orchestrator = SessionOrchestrator(session_id, mode="project")
    orchestrator.providers = {"code-agent": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])

    events = []
    async for event in orchestrator.stream_user_message(action):
        events.append(event)
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "completed_verified"
    assert provider.worker_turns == 2
    assert provider.saw_deterministic_evidence is True
    assert any(event["type"] == "workspace_verified" for event in events)
    candidates = workspace_service.list_run_workspaces(
        run_id=run["id"],
        session_id=session_id,
    )
    assert len(candidates) == 1
    assert candidates[0]["verification_status"] == "passed"
    assert candidates[0]["changed_files"] == [{
        "status": " M",
        "path": "app.py",
    }]
    assert (repository_root / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


@pytest.mark.asyncio
async def test_verifier_needs_revision_triggers_one_bounded_rework_cycle(tmp_path):
    database = ChatDatabase(tmp_path / "planned-review-revision.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "形成经过返工核验的发布方案",
        "targets": ["lead"],
        "mode": "project",
        "execution_strategy": "planned",
        "max_revision_cycles": 1,
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class RevisionProvider(_FakeProvider):
        def __init__(self):
            super().__init__("lead", persona="Architect/Reviewer")
            self.calls = []
            self.worker_count = 0
            self.verifier_count = 0
            self.saw_revision_context = False

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            combined = "\n".join(message.content for message in messages)
            if "类型：verifier" in system_text:
                self.calls.append("verifier")
                self.verifier_count += 1
                if self.verifier_count == 1:
                    content = json.dumps({
                        "verdict": "needs_revision",
                        "summary": "缺少可验证的回滚条件",
                        "evidence": ["候选-v1"],
                        "issues": ["补充回滚触发条件"],
                    }, ensure_ascii=False)
                else:
                    assert "候选-v2" in combined
                    content = json.dumps({
                        "verdict": "pass",
                        "summary": "返工版本补齐回滚条件",
                        "evidence": ["候选-v2"],
                        "issues": [],
                    }, ensure_ascii=False)
            elif "类型：synthesizer" in system_text:
                self.calls.append("synthesizer")
                assert '"verdict": "pass"' in combined
                content = "最终发布方案：包含回滚触发条件。"
            else:
                self.calls.append("worker")
                self.worker_count += 1
                if self.worker_count == 2:
                    self.saw_revision_context = (
                        "候选-v1" in combined
                        and "补充回滚触发条件" in combined
                        and "受控返工" in combined
                    )
                content = f"候选-v{self.worker_count}"
            yield {"delta": content, "usage": None}

    provider = RevisionProvider()
    orchestrator = SessionOrchestrator(session_id, mode="project")
    orchestrator.providers = {"lead": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])

    events = []
    async for event in orchestrator.stream_user_message(action):
        events.append(event)
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "completed_with_warnings"
    assert completed["plan_revision"] == 2
    assert completed["automatic_revision_count"] == 1
    assert provider.calls == [
        "worker", "verifier", "worker", "verifier", "synthesizer",
    ]
    assert provider.saw_revision_context is True
    detail = database.get_agent_run(run["id"])
    assert [review["verdict"] for review in detail["reviews"]] == [
        "needs_revision", "pass",
    ]
    assert [
        revision["source"] for revision in detail["plan_revisions"]
    ] == ["verifier_replan", "planner"]
    assert any(event["type"] == "plan_replanned" for event in events)
    assert len([
        event for event in events
        if event["type"] == "message_complete"
        and event["message"]["role"] == "assistant"
    ]) == 1


@pytest.mark.asyncio
async def test_repeated_verifier_failure_blocks_unverified_synthesis(tmp_path):
    database = ChatDatabase(tmp_path / "planned-review-fail.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "核验失败时不得伪造最终交付",
        "targets": ["lead"],
        "mode": "project",
        "execution_strategy": "planned",
        "max_revision_cycles": 1,
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class FailingVerifierProvider(_FakeProvider):
        def __init__(self):
            super().__init__("lead", persona="Architect/Reviewer")
            self.calls = []

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            if "类型：verifier" in system_text:
                self.calls.append("verifier")
                content = json.dumps({
                    "verdict": "fail",
                    "summary": "关键证据仍不可验证",
                    "evidence": [],
                    "issues": ["缺少真实测试证据"],
                }, ensure_ascii=False)
            elif "类型：synthesizer" in system_text:
                self.calls.append("synthesizer")
                content = "此内容不应生成"
            else:
                self.calls.append("worker")
                content = "缺少证据的候选"
            yield {"delta": content, "usage": None}

    provider = FailingVerifierProvider()
    orchestrator = SessionOrchestrator(session_id, mode="project")
    orchestrator.providers = {"lead": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])

    events = []
    async for event in orchestrator.stream_user_message(action):
        events.append(event)
        run_service.observe(run["id"], event)
    failed = run_service.finish(run["id"])

    assert failed["status"] == "failed"
    assert failed["automatic_revision_count"] == 1
    assert provider.calls == ["worker", "verifier", "worker", "verifier"]
    synthesizer = next(
        step for step in failed["plan_steps"]
        if step["kind"] == "synthesizer"
    )
    assert synthesizer["status"] == "blocked"
    assert any(
        event.get("code") == "plan_verification_failed"
        for event in events
    )
    assert not any(
        event["type"] == "message_complete"
        and event["message"]["role"] == "assistant"
        for event in events
    )


@pytest.mark.asyncio
async def test_project_plan_retry_reuses_completed_worker_outputs(tmp_path):
    database = ChatDatabase(tmp_path / "planned-retry.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "先独立分析，再核验并综合",
        "targets": ["worker-a", "reviewer"],
        "mode": "project",
        "execution_strategy": "planned",
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class FirstAttemptProvider(_FakeProvider):
        def __init__(self, name, persona):
            super().__init__(name, persona=persona)
            self.calls = []

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            if "类型：verifier" in system_text:
                self.calls.append("verifier")
                if False:
                    yield {}
                raise RuntimeError("verifier offline")
            if "类型：synthesizer" in system_text:
                self.calls.append("synthesizer")
                if False:
                    yield {}
                raise RuntimeError("synthesizer offline")
            self.calls.append("worker")
            yield {"delta": f"已完成-{self.config.name}", "usage": None}

    worker = FirstAttemptProvider("worker-a", "Researcher")
    reviewer = FirstAttemptProvider("reviewer", "Architect/Reviewer")
    first = SessionOrchestrator(session_id, mode="project")
    first.providers = {"worker-a": worker, "reviewer": reviewer}
    first.refresh_providers = lambda: None
    first.run_service = run_service
    first.begin_agent_run(run["id"])

    first_events = []
    async for event in first.stream_user_message(action):
        first_events.append(event)
        run_service.observe(run["id"], event)
    failed = run_service.finish(run["id"])

    assert failed["status"] == "failed"
    assert worker.calls == ["worker"]
    assert reviewer.calls == ["worker", "verifier", "synthesizer"]
    before_retry = database.list_agent_plan_steps(run["id"])
    assert [
        step["status"] for step in before_retry if step["kind"] == "worker"
    ] == ["completed", "completed"]

    retry_action, _ = run_service.prepare_retry(run["id"], session_id)
    retry_action["_agent_run_id"] = run["id"]

    class RetryReviewer(_FakeProvider):
        def __init__(self):
            super().__init__("reviewer", persona="Architect/Reviewer")
            self.calls = []
            self.saw_reused_workers = False

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            combined = "\n".join(message.content for message in messages)
            self.saw_reused_workers = (
                "已完成-worker-a" in combined
                and "已完成-reviewer" in combined
            )
            if "类型：verifier" in system_text:
                self.calls.append("verifier")
                content = json.dumps({
                    "verdict": "pass",
                    "summary": "复用的两份 Worker 输出可用于综合",
                    "evidence": ["worker-a", "reviewer"],
                    "issues": [],
                }, ensure_ascii=False)
            else:
                assert "类型：synthesizer" in system_text
                self.calls.append("synthesizer")
                content = "重试后最终交付"
            yield {"delta": content, "usage": None}

    retry_reviewer = RetryReviewer()
    resumed = SessionOrchestrator(session_id, mode="project")
    resumed.providers = {"reviewer": retry_reviewer}
    resumed.refresh_providers = lambda: None
    resumed.run_service = run_service
    resumed.begin_agent_run(run["id"], retry_action.get("_pending_steers"))

    retry_events = []
    async for event in resumed.stream_user_message(retry_action):
        retry_events.append(event)
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "completed_with_warnings"
    assert retry_reviewer.calls == ["verifier", "synthesizer"]
    assert retry_reviewer.saw_reused_workers is True
    assert all(
        step["status"] == "completed"
        for step in database.list_agent_plan_steps(run["id"])
    )
    assert any(
        event["type"] == "message_complete"
        and event["message"]["content"] == "重试后最终交付"
        for event in retry_events
    )


@pytest.mark.asyncio
async def test_project_worker_cannot_execute_side_effect_tool(tmp_path):
    database = ChatDatabase(tmp_path / "planned-read-only-worker.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "先分析，最终阶段再决定是否写入",
        "targets": ["solo"],
        "mode": "project",
        "execution_strategy": "planned",
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class ReadOnlyWorkerProvider(_FakeProvider):
        def __init__(self):
            super().__init__("solo", persona="Architect/Reviewer")
            self.worker_calls = 0

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            if "类型：worker" in system_text:
                self.worker_calls += 1
                assert "write_artifact" not in {tool.name for tool in (tools or [])}
                if self.worker_calls == 1:
                    yield {
                        "delta": "",
                        "usage": None,
                        "tool_calls": [{
                            "id": "forbidden-worker-write",
                            "name": "write_artifact",
                            "arguments": {
                                "path": "must-not-write.md",
                                "content": "forbidden",
                            },
                        }],
                    }
                    return
                observation = next(
                    message for message in reversed(messages)
                    if message.role == "tool"
                )
                assert observation.tool_error is True
                assert "只读 Worker/Verifier" in observation.content
                yield {"delta": "只读分析已完成", "usage": None}
                return
            if "类型：verifier" in system_text:
                yield {
                    "delta": json.dumps({
                        "verdict": "pass",
                        "summary": "Worker 未执行副作用",
                        "evidence": [],
                        "issues": [],
                    }, ensure_ascii=False),
                    "usage": None,
                }
                return
            assert "类型：synthesizer" in system_text
            yield {"delta": "最终未写入任何工件。", "usage": None}

    provider = ReadOnlyWorkerProvider()
    orchestrator = SessionOrchestrator(session_id, mode="project")
    orchestrator.providers = {"solo": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])
    tool_runs_before = len(tool_registry.list_runs(session_id=session_id, limit=None))

    async for event in orchestrator.stream_user_message(action):
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "completed_with_warnings"
    assert provider.worker_calls == 2
    assert len(tool_registry.list_runs(session_id=session_id, limit=None)) == tool_runs_before
    calls = database.list_agent_tool_calls(run["id"])
    assert len(calls) == 1
    assert calls[0]["status"] == "denied"
    assert calls[0]["plan_step_id"]


@pytest.mark.asyncio
async def test_interrupt_all_stops_remaining_project_plan_phases(tmp_path):
    database = ChatDatabase(tmp_path / "planned-interrupt.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "执行一个可中断的项目计划",
        "targets": ["slow-plan"],
        "mode": "project",
        "execution_strategy": "planned",
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]
    started = asyncio.Event()

    class SlowPlanProvider(_FakeProvider):
        def __init__(self):
            super().__init__("slow-plan", persona="Architect/Reviewer")
            self.phases = []

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            phase = (
                "worker" if "类型：worker" in system_text
                else "verifier" if "类型：verifier" in system_text
                else "synthesizer"
            )
            self.phases.append(phase)
            started.set()
            await asyncio.sleep(10)
            yield {"delta": "不应完成", "usage": None}

    provider = SlowPlanProvider()
    orchestrator = SessionOrchestrator(session_id, mode="project")
    orchestrator.providers = {"slow-plan": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])

    async def collect():
        events = []
        async for event in orchestrator.stream_user_message(action):
            events.append(event)
            run_service.observe(run["id"], event)
        return events

    collecting = asyncio.create_task(collect())
    await asyncio.wait_for(started.wait(), timeout=1)
    orchestrator.interrupt("all")
    events = await asyncio.wait_for(collecting, timeout=2)
    interrupted = run_service.finish(run["id"])

    assert interrupted["status"] == "interrupted"
    assert provider.phases == ["worker"]
    assert not any(event.get("code") == "plan_incomplete" for event in events)
    plan_steps = database.list_agent_plan_steps(run["id"])
    assert next(
        step for step in plan_steps if step["kind"] == "synthesizer"
    )["status"] == "interrupted"


@pytest.mark.asyncio
async def test_project_plan_does_not_synthesize_when_all_workers_fail(tmp_path):
    database = ChatDatabase(tmp_path / "planned-all-workers-fail.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "不能在没有候选结果时伪造最终交付",
        "targets": ["offline-worker"],
        "mode": "project",
        "execution_strategy": "planned",
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class OfflineWorker(_FakeProvider):
        def __init__(self):
            super().__init__("offline-worker", persona="Architect/Reviewer")
            self.calls = 0

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            if False:
                yield {}
            raise RuntimeError("offline")

    provider = OfflineWorker()
    orchestrator = SessionOrchestrator(session_id, mode="project")
    orchestrator.providers = {"offline-worker": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])

    events = []
    async for event in orchestrator.stream_user_message(action):
        events.append(event)
        run_service.observe(run["id"], event)
    failed = run_service.finish(run["id"])

    assert failed["status"] == "failed"
    assert provider.calls == 1
    plan_steps = database.list_agent_plan_steps(run["id"])
    assert next(
        step for step in plan_steps if step["kind"] == "verifier"
    )["status"] == "blocked"
    assert next(
        step for step in plan_steps if step["kind"] == "synthesizer"
    )["status"] == "blocked"
    assert not any(
        event["type"] == "message_complete"
        and event["message"]["role"] == "assistant"
        for event in events
    )
