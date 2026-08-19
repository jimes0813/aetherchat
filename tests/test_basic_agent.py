import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.services import git_workspace_service as git_workspace_module  # noqa: E402
from app.services.agent_run_service import AgentRunService  # noqa: E402
from app.services.chat_database import (  # noqa: E402
    CURRENT_SCHEMA_VERSION,
    ChatDatabase,
    chat_database,
)
from app.services.git_workspace_service import (  # noqa: E402
    GitWorkspaceError,
    GitWorkspaceService,
)
from app.services.mcp_registry import (  # noqa: E402
    MCPConnectionCreate,
    MCPConnectionPatch,
    MCPRegistry,
)


# Split from tests/test_basic.py for domain isolation.
# Shared env/client fixtures live in tests/conftest.py.

def test_agent_run_state_events_retry_and_restart_recovery(tmp_path):
    path = tmp_path / "agent-runs.sqlite3"
    database = ChatDatabase(path)
    service = AgentRunService(database)
    session_id = str(uuid4())

    run = service.create(session_id, {
        "content": "实现可恢复任务", "targets": ["architect", "coder"], "mode": "project",
    })
    assert run["effective_execution_strategy"] == "direct"
    assert run["execution_strategy_source"] == "compatibility_default"
    assert run["input"]["execution_strategy"] == "direct"
    resolved_event = next(
        event for event in database.get_agent_run(run["id"])["events"]
        if event["event_type"] == "execution_strategy_resolved"
    )
    assert resolved_event["payload"] == {
        "mode": "project",
        "requested": None,
        "effective": "direct",
        "source": "compatibility_default",
    }
    deprecated_event = next(
        event for event in database.get_agent_run(run["id"])["events"]
        if event["event_type"] == "legacy_contract_deprecated"
    )
    assert deprecated_event["payload"] == {
        "code": "execution_strategy_required_in_v1",
        "effective_execution_strategy": "direct",
    }
    service.observe(run["id"], {"type": "delta", "model": "architect", "delta": "分析"})
    service.observe(run["id"], {
        "type": "message_complete",
        "message": {
            "id": str(uuid4()),
            "author_model": "architect",
            "role": "assistant",
            "content": "架构结论与证据摘要",
            "tokens_in": 120,
            "tokens_out": 30,
            "cost_usd": 0.015,
        },
    })
    message_event = next(
        event for event in database.get_agent_run(run["id"])["events"]
        if event["event_type"] == "message_complete"
    )
    assert message_event["payload"]["content_preview"] == "架构结论与证据摘要"
    assert message_event["payload"]["tokens_in"] == 120
    assert message_event["payload"]["tokens_out"] == 30
    assert message_event["payload"]["cost_usd"] == 0.015
    service.observe(run["id"], {
        "type": "error", "code": "provider_error", "model": "coder", "message": "provider offline",
    })
    failed = service.finish(run["id"])
    assert failed["status"] == "failed"
    assert {step["model"]: step["status"] for step in failed["steps"]} == {
        "architect": "completed", "coder": "failed",
    }

    retry_action, retrying = service.prepare_retry(run["id"], session_id)
    assert retry_action["targets"] == ["coder"]
    assert retry_action["_aether_retry"] is True
    assert retry_action["effective_execution_strategy"] == "direct"
    assert retry_action["execution_strategy_source"] == "compatibility_default"
    assert retrying["retry_count"] == 1
    service.observe(run["id"], {"type": "delta", "model": "coder", "delta": "修复"})
    service.observe(run["id"], {
        "type": "message_complete",
        "message": {"id": str(uuid4()), "author_model": "coder", "role": "assistant"},
    })
    completed = service.finish(run["id"])
    assert completed["status"] == "completed_with_warnings"

    active = service.create(session_id, {"content": "等待重启", "targets": ["coder"]})
    service.observe(active["id"], {"type": "delta", "model": "coder", "delta": "工作中"})
    reopened = ChatDatabase(path)
    assert reopened.recover_incomplete_agent_runs() == 1
    recovered = reopened.get_agent_run(active["id"])
    assert recovered["status"] == "recoverable"
    assert recovered["steps"][0]["status"] == "interrupted"
    assert recovered["events"][-1]["event_type"] == "recovery_required"


def test_observe_writes_running_step_once_for_repeated_deltas(tmp_path, monkeypatch):
    path = tmp_path / "agent-delta.sqlite3"
    database = ChatDatabase(path)
    service = AgentRunService(database)
    session_id = str(uuid4())
    run = service.create(session_id, {"content": "流式", "targets": ["coder"]})
    calls = []
    original = database.update_agent_step

    def counted(run_id, model, status, **kwargs):
        calls.append((run_id, model, status))
        return original(run_id, model, status, **kwargs)

    monkeypatch.setattr(database, "update_agent_step", counted)
    service.observe(run["id"], {"type": "delta", "model": "coder", "delta": "一"})
    service.observe(run["id"], {"type": "delta", "model": "coder", "delta": "二"})
    assert calls == [(run["id"], "coder", "running")]


def test_agent_run_execution_strategy_contract_is_explicit_and_validated(tmp_path):
    database = ChatDatabase(tmp_path / "agent-strategy.sqlite3")
    service = AgentRunService(database)
    session_id = str(uuid4())

    action = {
        "content": "按计划执行", "targets": ["coder"],
        "mode": "project", "execution_strategy": "planned",
    }
    run = service.create(session_id, action)
    assert action["effective_execution_strategy"] == "planned"
    assert run["effective_execution_strategy"] == "planned"
    assert run["execution_strategy_source"] == "explicit"
    assert run["input"]["requested_execution_strategy"] == "planned"

    spoofed = service.create(session_id, {
        "content": "不能伪造实际策略", "targets": ["coder"],
        "mode": "project", "execution_strategy": "direct",
        "effective_execution_strategy": "planned",
        "execution_strategy_source": "compatibility_default",
    })
    assert spoofed["effective_execution_strategy"] == "direct"
    assert spoofed["execution_strategy_source"] == "explicit"

    with pytest.raises(ValueError, match="仅适用于 project"):
        service.create(session_id, {
            "content": "无效组合", "targets": ["coder"],
            "mode": "casual", "execution_strategy": "planned",
        })

    with pytest.raises(ValueError, match="不支持的执行策略"):
        service.create(session_id, {
            "content": "无效策略", "targets": ["coder"],
            "mode": "project", "execution_strategy": "automatic",
        })


def _create_legacy_chat_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA user_version = 0;
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL DEFAULT 'project',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_message_at TEXT
            );
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                author_model TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tokens_in INTEGER NOT NULL DEFAULT 0,
                tokens_out INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            INSERT INTO sessions
                (id, title, mode, created_at, updated_at)
                VALUES ('legacy-session', '旧数据', 'project', '2026-08-01', '2026-08-01');
            INSERT INTO messages
                (id, session_id, author_model, role, content, created_at)
                VALUES ('legacy-message', 'legacy-session', 'user', 'user', '必须保留', '2026-08-01');
            """
        )


def test_chat_database_migrates_in_order_with_pre_upgrade_backup(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    _create_legacy_chat_database(path)

    database = ChatDatabase(path)

    assert database.last_migration_backup is not None
    assert database.last_migration_backup.exists()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert connection.execute(
            "SELECT content FROM messages WHERE id = 'legacy-message'"
        ).fetchone()[0] == "必须保留"
        tool_columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(agent_tool_calls)"
            ).fetchall()
        }
        assert {"verification_status", "evidence_json", "verified_at"} <= tool_columns
        session_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
        }
        assert "project_id" in session_columns
        event_tables = {
            row[0] for row in connection.execute(
                """SELECT name FROM sqlite_schema
                   WHERE type = 'table' AND name IN ('event_streams', 'event_journal')"""
            ).fetchall()
        }
        assert event_tables == {"event_streams", "event_journal"}
    with sqlite3.connect(database.last_migration_backup) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 0
        message_columns = {
            row[1] for row in backup.execute("PRAGMA table_info(messages)").fetchall()
        }
        assert "attachments_json" not in message_columns
        assert backup.execute(
            "SELECT content FROM messages WHERE id = 'legacy-message'"
        ).fetchone()[0] == "必须保留"

    reopened = ChatDatabase(path)
    assert reopened.last_migration_backup is None


def test_chat_database_restores_backup_when_migration_fails(tmp_path, monkeypatch):
    path = tmp_path / "broken-migration.sqlite3"
    _create_legacy_chat_database(path)

    def fail_migration(_self, _connection):
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(ChatDatabase, "_migration_2", fail_migration)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        ChatDatabase(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute(
            "SELECT content FROM messages WHERE id = 'legacy-message'"
        ).fetchone()[0] == "必须保留"
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'agent_tool_calls'"
        ).fetchone()[0] == 0
    assert len(list(
        tmp_path.glob(f"broken-migration.sqlite3.pre-v0-to-v{CURRENT_SCHEMA_VERSION}-*.bak")
    )) == 1


def test_chat_database_restores_schema_2_backup_when_event_migration_fails(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "broken-event-migration.sqlite3"
    ChatDatabase(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE event_journal")
        connection.execute("DROP TABLE event_streams")
        connection.execute("PRAGMA user_version = 2")
        connection.execute(
            """INSERT INTO sessions
               (id, title, mode, created_at, updated_at)
               VALUES ('schema-2-session', '保留数据', 'project', '2026-08-11', '2026-08-11')"""
        )

    def fail_event_migration(_self, connection):
        connection.execute("CREATE TABLE migration_3_partial (id TEXT PRIMARY KEY)")
        raise RuntimeError("injected event migration failure")

    monkeypatch.setattr(ChatDatabase, "_migration_3", fail_event_migration)
    with pytest.raises(RuntimeError, match="injected event migration failure"):
        ChatDatabase(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT title FROM sessions WHERE id = 'schema-2-session'"
        ).fetchone()[0] == "保留数据"
        assert connection.execute(
            """SELECT COUNT(*) FROM sqlite_schema
               WHERE type = 'table' AND name = 'migration_3_partial'"""
        ).fetchone()[0] == 0
    assert len(list(
        tmp_path.glob(
            f"broken-event-migration.sqlite3.pre-v2-to-v{CURRENT_SCHEMA_VERSION}-*.bak"
        )
    )) == 1


def test_agent_plan_persists_goal_dag_review_and_retry_state(tmp_path):
    path = tmp_path / "agent-plan.sqlite3"
    database = ChatDatabase(path)
    service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "比较两个实现并交付一份经过核验的方案",
        "targets": ["researcher", "reviewer"],
        "mode": "project",
        "execution_strategy": "planned",
        "goal_spec": {
            "success_criteria": ["比较结论有证据", "明确未验证项"],
            "constraints": ["不得虚构测试结果"],
            "deliverable": "一份最终实施建议",
        },
    }
    run = service.create(session_id, action)
    plan = service.ensure_plan(
        run_id=run["id"],
        session_id=session_id,
        action=action,
        models=["researcher", "reviewer"],
        profiles={
            "researcher": "Researcher",
            "reviewer": "Architect/Reviewer",
        },
    )

    assert plan["goal"]["objective"] == action["content"]
    assert plan["goal"]["deliverable"] == "一份最终实施建议"
    assert [step["kind"] for step in plan["steps"]] == [
        "planner", "worker", "worker", "verifier", "synthesizer",
    ]
    assert [step["phase"] for step in plan["steps"]] == [0, 1, 1, 2, 3]
    verifier = next(step for step in plan["steps"] if step["kind"] == "verifier")
    synthesizer = next(step for step in plan["steps"] if step["kind"] == "synthesizer")
    assert verifier["assigned_model"] == "reviewer"
    assert verifier["allow_failed_dependencies"] is True
    assert synthesizer["dependencies"][-1] == "verifier"

    first_worker = next(
        step for step in plan["steps"]
        if step["step_key"].startswith("worker:1:")
    )
    second_worker = next(
        step for step in plan["steps"]
        if step["step_key"].startswith("worker:2:")
    )
    worker_call = service.begin_tool_call(
        run_id=run["id"],
        session_id=session_id,
        model="reviewer",
        provider_type="openai_compat",
        provider_model="reviewer-model",
        provider_call_id="provider-worker-call",
        tool_name="list_tasks",
        arguments={},
        round_index=0,
        call_index=0,
        plan_step_id=second_worker["id"],
    )
    service.finish_tool_call(
        worker_call["id"],
        {"tool": "list_tasks", "status": "success", "result": []},
    )
    verifier_call = service.begin_tool_call(
        run_id=run["id"],
        session_id=session_id,
        model="reviewer",
        provider_type="openai_compat",
        provider_model="reviewer-model",
        provider_call_id="provider-verifier-call",
        tool_name="list_tasks",
        arguments={},
        round_index=0,
        call_index=0,
        plan_step_id=verifier["id"],
    )
    assert verifier_call["disposition"] == "execute"
    assert verifier_call["id"] != worker_call["id"]
    assert verifier_call["idempotency_key"] != worker_call["idempotency_key"]
    worker_checkpoint = service.save_checkpoint(
        run_id=run["id"],
        session_id=session_id,
        model="reviewer",
        kind="tool_round",
        state={"version": 1, "messages": [], "next_tool_round": 1},
        plan_step_id=second_worker["id"],
    )
    verifier_checkpoint = service.save_checkpoint(
        run_id=run["id"],
        session_id=session_id,
        model="reviewer",
        kind="tool_round",
        state={"version": 1, "messages": [], "next_tool_round": 1},
        plan_step_id=verifier["id"],
    )
    assert service.latest_checkpoint(
        run["id"], "reviewer", second_worker["id"]
    )["id"] == worker_checkpoint["id"]
    assert service.latest_checkpoint(
        run["id"], "reviewer", verifier["id"]
    )["id"] == verifier_checkpoint["id"]
    service.start_plan_step(run["id"], first_worker["id"])
    service.finish_plan_step(
        run["id"], first_worker["id"], status="completed", content="研究结果"
    )
    service.start_plan_step(run["id"], second_worker["id"])
    service.finish_plan_step(
        run["id"], second_worker["id"], status="failed", error="离线模型失败"
    )
    review = service.record_review(
        run_id=run["id"],
        plan_step_id=verifier["id"],
        session_id=session_id,
        reviewer_model="reviewer",
        review={
            "verdict": "needs_revision",
            "summary": "只有一份候选结果",
            "evidence": ["研究结果"],
            "issues": ["第二个 Worker 失败"],
        },
    )
    assert review["verdict"] == "needs_revision"

    reopened = ChatDatabase(path)
    detail = reopened.get_agent_run(run["id"])
    assert detail["plan_step_count"] == 5
    assert detail["completed_plan_step_count"] == 2
    assert detail["review_count"] == 1
    assert detail["plan_steps"][1]["output"]["content"] == "研究结果"
    failed = service.finish(run["id"])
    assert failed["status"] == "failed"

    retry_action, retrying = service.prepare_retry(run["id"], session_id)
    assert retrying["retry_count"] == 1
    assert retry_action["execution_strategy"] == "planned"
    retried_steps = database.list_agent_plan_steps(run["id"])
    assert next(
        step for step in retried_steps if step["id"] == first_worker["id"]
    )["status"] == "completed"
    assert next(
        step for step in retried_steps if step["id"] == second_worker["id"]
    )["status"] == "pending"


def test_agent_plan_draft_edit_confirm_versioning_and_reject(tmp_path):
    database = ChatDatabase(tmp_path / "agent-plan-approval.sqlite3")
    service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "先审核计划，再执行迁移",
        "targets": ["builder", "reviewer"],
        "mode": "project",
        "execution_strategy": "planned",
        "plan_confirmation_required": True,
        "max_revision_cycles": 1,
    }
    run = service.create(session_id, action)
    plan = service.ensure_plan(
        run_id=run["id"],
        session_id=session_id,
        action=action,
        models=["builder", "reviewer"],
        profiles={"builder": "Implementer", "reviewer": "Reviewer"},
    )
    waiting = database.update_agent_run(
        run["id"], "waiting_plan_confirmation", error=None
    )

    assert waiting["plan_status"] == "draft"
    assert waiting["plan_revision"] == 1
    assert waiting["plan_approval_required"] is True
    assert waiting["plan_approved_at"] is None
    assert database.list_agent_plan_revisions(run["id"])[0]["source"] == "planner"
    reopened = ChatDatabase(tmp_path / "agent-plan-approval.sqlite3")
    assert reopened.recover_incomplete_agent_runs() == 0
    assert reopened.get_agent_run(run["id"])["status"] == "waiting_plan_confirmation"

    worker = next(step for step in plan["steps"] if step["kind"] == "worker")
    with pytest.raises(ValueError, match="更早阶段"):
        service.edit_plan(
            run_id=run["id"],
            session_id=session_id,
            expected_revision=1,
            goal_spec=None,
            plan_steps=[{
                "id": worker["id"],
                "dependencies": ["verifier"],
            }],
        )

    edited = service.edit_plan(
        run_id=run["id"],
        session_id=session_id,
        expected_revision=1,
        goal_spec={
            "objective": "迁移前先给出回滚与验证计划",
            "success_criteria": ["包含回滚步骤", "包含可执行验证"],
            "constraints": ["未经确认不得执行副作用"],
            "deliverable": "一份可审核的迁移方案",
        },
        plan_steps=[{
            "id": worker["id"],
            "title": "先验证迁移路径",
            "description": "只读检查兼容性、风险与回滚条件。",
            "assigned_model": "reviewer",
            "role": "Migration Analyst",
        }],
        change_summary="收紧目标并调整首个 Worker",
    )

    assert edited["status"] == "waiting_plan_confirmation"
    assert edited["plan_status"] == "draft"
    assert edited["plan_revision"] == 2
    assert edited["goal"]["objective"] == "迁移前先给出回滚与验证计划"
    edited_worker = next(
        step for step in edited["plan_steps"] if step["id"] == worker["id"]
    )
    assert edited_worker["title"] == "先验证迁移路径"
    assert edited_worker["assigned_model"] == "reviewer"
    revisions = database.list_agent_plan_revisions(run["id"])
    assert [item["revision"] for item in revisions] == [2, 1]
    assert revisions[0]["source"] == "user_edit"

    with pytest.raises(RuntimeError, match="版本已变化"):
        service.edit_plan(
            run_id=run["id"],
            session_id=session_id,
            expected_revision=1,
            goal_spec=None,
            plan_steps=[],
        )

    confirmed_action, confirmed = service.prepare_confirmed_plan(
        run_id=run["id"],
        session_id=session_id,
        expected_revision=2,
    )
    assert confirmed["status"] == "running"
    assert confirmed["plan_status"] == "approved"
    assert confirmed["plan_approved_at"]
    assert confirmed_action["_plan_confirmed"] is True
    assert confirmed_action["_aether_retry"] is True
    assert set(confirmed_action["targets"]) == {"builder", "reviewer"}

    rejected_run = service.create(session_id, {
        **action,
        "content": "这个计划应被拒绝",
    })
    service.ensure_plan(
        run_id=rejected_run["id"],
        session_id=session_id,
        action=action,
        models=["builder"],
        profiles={"builder": "Implementer"},
    )
    database.update_agent_run(
        rejected_run["id"], "waiting_plan_confirmation", error=None
    )
    rejected = service.reject_plan(
        run_id=rejected_run["id"],
        session_id=session_id,
        expected_revision=1,
    )
    assert rejected["status"] == "interrupted"
    assert rejected["plan_status"] == "cancelled"
    assert all(
        step["status"] in {"completed", "skipped"}
        for step in rejected["plan_steps"]
    )
    assert service.finish(rejected_run["id"])["status"] == "interrupted"


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
    # The workspace service deliberately ignores global Git configuration.
    # Keep this fixture independent from Git for Windows' global autocrlf
    # default so the committed bytes and working tree remain identical.
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


@pytest.mark.asyncio
async def test_git_worker_worktree_verifies_diff_and_requires_safe_apply(
    tmp_path,
    monkeypatch,
):
    repository_root = tmp_path / "repository"
    initial_head = _initialize_test_git_repository(repository_root)
    database = ChatDatabase(tmp_path / "git-agent.sqlite3")
    workspace_service = GitWorkspaceService(
        database,
        worktree_root=tmp_path / "managed-worktrees",
    )
    run_service = AgentRunService(database, workspace_service)
    session_id = str(uuid4())
    registered = workspace_service.register_repository(
        session_id=session_id,
        selected_path=str(repository_root),
    )
    action = {
        "content": "把 VALUE 改为 2，并添加说明",
        "targets": ["coder"],
        "mode": "project",
        "execution_strategy": "planned",
        "repository_id": registered["id"],
        "verification_commands": ["python -m pytest -q"],
    }
    run = run_service.create(session_id, action)
    plan = run_service.ensure_plan(
        run_id=run["id"],
        session_id=session_id,
        action=action,
        models=["coder"],
        profiles={"coder": "Implementer"},
    )
    worker = next(step for step in plan["steps"] if step["kind"] == "worker")
    running_worker = run_service.start_plan_step(run["id"], worker["id"])
    workspace = workspace_service.prepare_worker_workspace(
        run_id=run["id"],
        plan_step_id=worker["id"],
        session_id=session_id,
        repository_id=registered["id"],
        base_commit=action["repository_base_commit"],
        attempt=running_worker["attempt"],
    )

    workspace_service.invoke_workspace_tool(
        name="workspace_write_file",
        arguments={"path": "app.py", "content": "VALUE = 2\n"},
        workspace_id=workspace["id"],
        session_id=session_id,
        run_id=run["id"],
        plan_step_id=worker["id"],
    )
    workspace_service.invoke_workspace_tool(
        name="workspace_write_file",
        arguments={"path": "NOTES.md", "content": "# Verified candidate\n"},
        workspace_id=workspace["id"],
        session_id=session_id,
        run_id=run["id"],
        plan_step_id=worker["id"],
    )
    assert (repository_root / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (repository_root / "NOTES.md").exists()

    async def fake_sandbox(command, *args, **kwargs):
        assert command == "python -m pytest -q"
        assert kwargs["allow_net"] is False
        assert Path(kwargs["workspace_path"]).is_relative_to(
            tmp_path / "managed-worktrees"
        )
        return {
            "stdout": "1 passed\n",
            "exit_code": 0,
            "timed_out": False,
            "network": "none",
        }

    monkeypatch.setattr(
        git_workspace_module,
        "execute_in_sandbox",
        fake_sandbox,
    )
    verified = await workspace_service.finalize_workspace(
        workspace["id"],
        action["verification_commands"],
    )
    assert verified["verification_status"] == "passed"
    assert verified["diff_sha256"]
    assert {item["path"] for item in verified["changed_files"]} == {
        "NOTES.md",
        "app.py",
    }
    assert verified["verification"][0]["exit_code"] == 0
    assert run_service.is_side_effecting_tool("workspace_write_file") is False

    database.update_agent_run(run["id"], "completed")
    applied = workspace_service.apply_workspace(
        workspace_id=workspace["id"],
        session_id=session_id,
    )
    assert applied["apply_status"] == "applied"
    assert (repository_root / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert (repository_root / "NOTES.md").read_text(
        encoding="utf-8"
    ) == "# Verified candidate\n"
    assert subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip() == initial_head
    assert subprocess.run(
        ["git", "-C", str(repository_root), "status", "--porcelain"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    with pytest.raises(GitWorkspaceError, match="已经处理"):
        workspace_service.apply_workspace(
            workspace_id=workspace["id"],
            session_id=session_id,
        )


@pytest.mark.asyncio
async def test_deterministic_git_verifier_overrides_model_pass_and_blocks_races(
    tmp_path,
    monkeypatch,
):
    repository_root = tmp_path / "repository"
    _initialize_test_git_repository(repository_root)
    database = ChatDatabase(tmp_path / "git-verifier.sqlite3")
    workspace_service = GitWorkspaceService(
        database,
        worktree_root=tmp_path / "managed-worktrees",
    )
    run_service = AgentRunService(database, workspace_service)
    session_id = str(uuid4())
    registered = workspace_service.register_repository(
        session_id=session_id,
        selected_path=str(repository_root),
    )
    action = {
        "content": "修改代码",
        "targets": ["coder"],
        "mode": "project",
        "execution_strategy": "planned",
        "repository_id": registered["id"],
        "verification_commands": ["python -m pytest -q"],
    }
    run = run_service.create(session_id, action)
    plan = run_service.ensure_plan(
        run_id=run["id"],
        session_id=session_id,
        action=action,
        models=["coder"],
        profiles={"coder": "Implementer"},
    )
    worker = next(step for step in plan["steps"] if step["kind"] == "worker")
    running_worker = run_service.start_plan_step(run["id"], worker["id"])
    workspace = workspace_service.prepare_worker_workspace(
        run_id=run["id"],
        plan_step_id=worker["id"],
        session_id=session_id,
        repository_id=registered["id"],
        base_commit=action["repository_base_commit"],
        attempt=running_worker["attempt"],
    )
    workspace_service.invoke_workspace_tool(
        name="workspace_write_file",
        arguments={"path": "app.py", "content": "VALUE = 'broken'\n"},
        workspace_id=workspace["id"],
        session_id=session_id,
        run_id=run["id"],
        plan_step_id=worker["id"],
    )

    async def failing_sandbox(*args, **kwargs):
        return {
            "stdout": "FAILED tests/test_app.py\n",
            "exit_code": 1,
            "timed_out": False,
            "network": "none",
        }

    monkeypatch.setattr(
        git_workspace_module,
        "execute_in_sandbox",
        failing_sandbox,
    )
    verified = await workspace_service.finalize_workspace(
        workspace["id"],
        action["verification_commands"],
    )
    assert verified["verification_status"] == "failed"
    enforced = workspace_service.enforce_review(
        run["id"],
        {
            "verdict": "pass",
            "summary": "模型声称测试通过",
            "evidence": ["模型自述"],
            "issues": [],
        },
    )
    assert enforced["verdict"] == "fail"
    assert "确定性" in enforced["summary"]

    database.update_agent_run(run["id"], "completed")
    (repository_root / "user-change.txt").write_text(
        "do not overwrite\n",
        encoding="utf-8",
    )
    with pytest.raises(GitWorkspaceError, match="主仓库存在新改动"):
        workspace_service.apply_workspace(
            workspace_id=workspace["id"],
            session_id=session_id,
        )
    assert (repository_root / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_git_repository_registration_requires_desktop_token_and_clean_tree(
    client,
    tmp_path,
    monkeypatch,
):
    repository_root = tmp_path / "desktop-repository"
    _initialize_test_git_repository(repository_root)
    session_id = str(uuid4())
    monkeypatch.setenv("AETHERCHAT_DESKTOP_TOKEN", "desktop-git-test-token")
    payload = {"session_id": session_id, "path": str(repository_root)}

    assert client.post(
        "/workspaces/repositories/register",
        json=payload,
    ).status_code == 403
    registered = client.post(
        "/workspaces/repositories/register",
        json=payload,
        headers={"X-Aether-Desktop-Token": "desktop-git-test-token"},
    )
    assert registered.status_code == 201, registered.text
    assert registered.json()["display_name"] == "desktop-repository"
    assert "root_path" not in registered.json()
    assert client.get(
        "/workspaces/repositories",
        params={"session_id": session_id},
    ).json()[0]["id"] == registered.json()["id"]
    assert client.get(
        "/workspaces/repositories",
        params={"session_id": str(uuid4())},
    ).json() == []

    (repository_root / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    dirty = client.post(
        "/workspaces/repositories/register",
        json=payload,
        headers={"X-Aether-Desktop-Token": "desktop-git-test-token"},
    )
    assert dirty.status_code == 400
    assert "未提交或未跟踪改动" in dirty.json()["detail"]


def test_agent_run_persists_tool_chain_checkpoint_and_idempotent_replay(tmp_path):
    path = tmp_path / "agent-run-v2.sqlite3"
    database = ChatDatabase(path)
    service = AgentRunService(database)
    session_id = str(uuid4())
    run = service.create(session_id, {
        "content": "写入一次工件",
        "targets": ["coder"],
        "mode": "project",
    })

    first = service.begin_tool_call(
        run_id=run["id"],
        session_id=session_id,
        model="coder",
        provider_type="openai_compat",
        provider_model="offline",
        provider_call_id="provider-call-1",
        tool_name="write_artifact",
        arguments={
            "path": "once.md",
            "content": "hello",
            "api_key": "must-not-be-persisted",
        },
        round_index=0,
        call_index=0,
    )
    assert first["disposition"] == "execute"
    assert first["side_effect"] is True
    assert first["arguments"]["api_key"] == "[redacted]"
    assert len(first["arguments_sha256"]) == 64

    completed = service.finish_tool_call(
        first["id"],
        {
            "tool": "write_artifact",
            "status": "success",
            "result": {"path": "once.md", "version": 1},
        },
        tool_run_id="tool-run-1",
    )
    assert completed["status"] == "completed"
    assert completed["verification_status"] == "verified"
    assert [item["kind"] for item in completed["evidence"]] == [
        "tool_result_digest", "artifact_version",
    ]
    assert completed["verified_at"] is not None

    replay = service.begin_tool_call(
        run_id=run["id"],
        session_id=session_id,
        model="coder",
        provider_type="openai_compat",
        provider_model="offline",
        provider_call_id="provider-call-retry",
        tool_name="write_artifact",
        arguments={
            "path": "once.md",
            "content": "hello",
            "api_key": "must-not-be-persisted",
        },
        round_index=4,
        call_index=2,
    )
    assert replay["disposition"] == "replay"
    assert replay["result"]["result"]["version"] == 1
    assert len(database.list_agent_tool_calls(run["id"])) == 1

    failed_call = service.begin_tool_call(
        run_id=run["id"],
        session_id=session_id,
        model="coder",
        provider_type="openai_compat",
        provider_model="offline",
        provider_call_id="provider-call-failed",
        tool_name="write_artifact",
        arguments={"path": "maybe-written.md", "content": "unknown outcome"},
        round_index=1,
        call_index=0,
    )
    service.finish_tool_call(
        failed_call["id"],
        {
            "tool": "write_artifact",
            "status": "error",
            "error": "connection closed after dispatch",
        },
    )
    failed_record = database.get_agent_tool_call(failed_call["id"])
    assert failed_record["verification_status"] == "failed"
    uncertain_retry = service.begin_tool_call(
        run_id=run["id"],
        session_id=session_id,
        model="coder",
        provider_type="openai_compat",
        provider_model="offline",
        provider_call_id="provider-call-failed-retry",
        tool_name="write_artifact",
        arguments={"path": "maybe-written.md", "content": "unknown outcome"},
        round_index=1,
        call_index=0,
    )
    assert uncertain_retry["disposition"] == "uncertain"
    assert uncertain_retry["verification_status"] == "uncertain"

    unverified_call = service.begin_tool_call(
        run_id=run["id"],
        session_id=session_id,
        model="coder",
        provider_type="mcp",
        provider_model="remote",
        provider_call_id="provider-call-unverified",
        tool_name="remote__send_message",
        arguments={"message": "hello"},
        round_index=2,
        call_index=0,
    )
    unverified = service.finish_tool_call(
        unverified_call["id"],
        {"tool": "remote__send_message", "status": "success", "result": {"ok": True}},
    )
    assert unverified["status"] == "completed"
    assert unverified["verification_status"] == "unverified"
    assert unverified["verified_at"] is None
    aggregate = database.get_agent_run(run["id"], include_events=False)
    assert aggregate["effect_count"] == 3
    assert aggregate["verified_effect_count"] == 1
    assert aggregate["failed_effect_count"] == 0
    assert aggregate["uncertain_effect_count"] == 1
    assert aggregate["effect_verification_status"] == "uncertain"

    checkpoint = service.save_checkpoint(
        run_id=run["id"],
        session_id=session_id,
        model="coder",
        kind="tool_round",
        state={
            "version": 1,
            "next_tool_round": 1,
            "messages": [{"role": "user", "content": "resume here"}],
        },
    )
    reopened = ChatDatabase(path)
    latest = reopened.latest_agent_checkpoint(run["id"], "coder")
    assert latest["id"] == checkpoint["id"]
    assert latest["state"]["next_tool_round"] == 1
    detail = reopened.get_agent_run(run["id"])
    assert len(detail["tool_calls"]) == 3
    assert len(detail["checkpoints"]) == 1


def test_agent_run_blocks_uncertain_side_effect_after_restart(tmp_path):
    path = tmp_path / "agent-run-uncertain.sqlite3"
    database = ChatDatabase(path)
    service = AgentRunService(database)
    session_id = str(uuid4())
    run = service.create(session_id, {
        "content": "执行一次副作用",
        "targets": ["coder"],
    })
    service.begin_tool_call(
        run_id=run["id"],
        session_id=session_id,
        model="coder",
        provider_type="anthropic",
        provider_model="offline",
        provider_call_id="toolu-pending",
        tool_name="write_artifact",
        arguments={"path": "uncertain.md", "content": "once"},
        round_index=0,
        call_index=0,
    )

    reopened = ChatDatabase(path)
    assert reopened.recover_incomplete_agent_runs() == 1
    uncertain = reopened.list_agent_tool_calls(run["id"])[0]
    assert uncertain["status"] == "uncertain"
    assert "阻止自动重放" in uncertain["error"]

    resumed = AgentRunService(reopened)
    resumed.prepare_retry(run["id"], session_id)
    blocked = resumed.begin_tool_call(
        run_id=run["id"],
        session_id=session_id,
        model="coder",
        provider_type="anthropic",
        provider_model="offline",
        provider_call_id="toolu-retry",
        tool_name="write_artifact",
        arguments={"path": "uncertain.md", "content": "once"},
        round_index=0,
        call_index=0,
    )
    assert blocked["disposition"] == "uncertain"
    assert "人工检查" in blocked["error"]


def test_effect_verification_trusts_only_known_nested_desktop_receipts(tmp_path):
    database = ChatDatabase(tmp_path / "effect-receipts.sqlite3")
    service = AgentRunService(database)
    session_id = str(uuid4())
    run = service.create(session_id, {
        "content": "核验桌面凭证",
        "targets": ["coder"],
        "mode": "project",
        "execution_strategy": "direct",
    })
    receipt = {
        "success": True,
        "verification": "launcher_accepted",
        "launcher": "firefox",
        "browser": "firefox",
        "accepted_at": "2026-08-11T00:00:00Z",
    }
    nested_result = {
        "plugin_id": "browser-links",
        "status": "success",
        "result": {
            "kind": "open_external",
            "url": "https://example.test",
            "execution_receipt": receipt,
        },
    }

    trusted_call = service.begin_tool_call(
        run_id=run["id"], session_id=session_id, model="coder",
        provider_type="openai_compat", provider_model="offline",
        provider_call_id="trusted-browser", tool_name="plugin__browser_links__open_url",
        arguments={"url": "https://example.test", "browser": "firefox"},
        round_index=0, call_index=0,
    )
    trusted = service.finish_tool_call(trusted_call["id"], {
        "tool": "plugin__browser_links__open_url",
        "status": "success",
        "result": nested_result,
    })
    assert trusted["verification_status"] == "verified"
    assert [item["kind"] for item in trusted["evidence"]] == [
        "tool_result_digest", "execution_receipt",
    ]

    spoofed_call = service.begin_tool_call(
        run_id=run["id"], session_id=session_id, model="coder",
        provider_type="mcp", provider_model="remote",
        provider_call_id="spoofed-browser", tool_name="remote__open_url",
        arguments={"url": "https://example.test"},
        round_index=1, call_index=0,
    )
    spoofed = service.finish_tool_call(spoofed_call["id"], {
        "tool": "remote__open_url",
        "status": "success",
        "result": nested_result,
    })
    assert spoofed["verification_status"] == "unverified"


def test_agent_run_api_lists_session_scoped_records(client):
    session_id = str(uuid4())
    other_session_id = str(uuid4())
    service = AgentRunService(chat_database)
    run = service.create(session_id, {"content": "记录运行", "targets": ["local"]})
    service.observe(run["id"], {
        "type": "error", "model": "local", "code": "provider_error", "message": "offline",
    })
    service.finish(run["id"])

    listed = client.get("/agent-runs", params={"session_id": session_id})
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == run["id"]
    detail = client.get(f"/agent-runs/{run['id']}", params={"session_id": session_id})
    assert detail.status_code == 200
    assert any(event["event_type"] == "run_failed" for event in detail.json()["events"])
    assert client.get(
        f"/agent-runs/{run['id']}", params={"session_id": other_session_id}
    ).status_code == 404


class _MemoryVault:
    def __init__(self):
        self.values = {}

    def get_key(self, ref):
        return self.values.get(ref)

    def set_key(self, ref, value):
        self.values[ref] = value

    def delete_key(self, ref):
        self.values.pop(ref, None)


@pytest.mark.asyncio
async def test_mcp_stdio_discovers_filters_invokes_and_persists_without_secrets(tmp_path):
    credential_vault = _MemoryVault()
    registry = MCPRegistry(tmp_path, credential_vault=credential_vault)
    server_script = PROJECT_ROOT / "tests" / "fixtures" / "mcp_echo_server.py"
    created = await registry.create(MCPConnectionCreate(
        id="local-echo",
        name="本地 Echo",
        transport="stdio",
        command=sys.executable,
        args=[str(server_script)],
        auth_type="none",
        token="must-not-be-persisted",
    ))
    assert created["status"] == "ready"
    assert created["has_token"] is True
    assert created["tools"][0]["name"] == "echo"
    definitions = registry.tool_definitions()
    assert definitions[0].name == "mcp__local_echo__echo"
    invoked = await registry.invoke_tool(definitions[0].name, {"message": "hello"})
    assert invoked["status"] == "success"
    assert invoked["content"][0]["text"] == "echo:hello"
    assert "must-not-be-persisted" not in registry.path.read_text(encoding="utf-8")

    hidden = registry.update("local-echo", MCPConnectionPatch(allowed_tools=[]))
    assert hidden["allowed_tools"] == []
    assert registry.tool_definitions() == []
    registry.delete("local-echo")
    assert credential_vault.values == {}
