from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import redis.asyncio as redis

from ..sandbox.executor import execute_in_sandbox
from .artifact_service import ArtifactService, MergeProposalStore, _utc_now
from .redis_lock import with_lock

artifact_service = ArtifactService()
merge_store = MergeProposalStore()
redis_client: redis.Redis | None = None


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    caller: str,
    session_id: str,
    *,
    tool_id: str | None = None,
    registry: Any | None = None,
    execution_context: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Dispatch an internal tool and always return/persist one ToolRun shape."""

    started_at = _utc_now()
    arguments = dict(arguments or {})
    status = "success"
    output: Any = None
    error: str | None = None
    linked_task_id = str(arguments.get("task_id") or "").strip()
    task_started = False

    try:
        artifact_service.normalize_session_id(session_id)
        if linked_task_id and name in {
            "write_artifact",
            "run_command",
            "workspace_write_file",
            "workspace_delete_file",
            "workspace_run_command",
        }:
            from ..routers.tasks import begin_task_tool

            begin_task_tool(linked_task_id, session_id, caller)
            task_started = True

        if name.startswith("workspace_"):
            from .git_workspace_service import (
                WORKSPACE_TOOL_NAMES,
                git_workspace_service,
            )

            if name not in WORKSPACE_TOOL_NAMES:
                raise KeyError(f"Unknown Worker workspace tool: {name}")
            context = execution_context or {}
            workspace_id = str(context.get("workspace_id") or "")
            if not workspace_id:
                raise PermissionError(
                    "该工具只能在编排器分配的 Worker worktree 中调用"
                )
            if name == "workspace_run_command":
                output = await git_workspace_service.run_workspace_command(
                    arguments=arguments,
                    workspace_id=workspace_id,
                    session_id=session_id,
                    run_id=context.get("run_id"),
                    plan_step_id=context.get("plan_step_id"),
                )
                sandbox_result = output.get("sandbox") or {}
                if int(sandbox_result.get("exit_code", 1)) != 0:
                    status = "error"
                    error = str(
                        sandbox_result.get("error")
                        or (
                            "Worker sandbox command exited with code "
                            f"{sandbox_result.get('exit_code', 1)}"
                        )
                    )
            else:
                output = await asyncio.to_thread(
                    git_workspace_service.invoke_workspace_tool,
                    name=name,
                    arguments=arguments,
                    workspace_id=workspace_id,
                    session_id=session_id,
                    run_id=context.get("run_id"),
                    plan_step_id=context.get("plan_step_id"),
                )

        elif name == "write_artifact":
            path = arguments.get("path", "output.txt")
            content = arguments.get("content", "")
            message = arguments.get("commit_message", f"Auto by {caller}")
            async with with_lock(redis_client, f"artifact:{session_id}:{path}"):
                written = await artifact_service.write(
                    session_id,
                    path,
                    str(content),
                    str(message),
                    caller,
                )
            output = {"artifact": written}

        elif name == "read_artifact":
            path = arguments.get("path", "README.md")
            normalized = artifact_service.normalize_path(path)
            content = await artifact_service.read(session_id, normalized)
            output = {"path": normalized, "content": content}

        elif name == "run_command":
            command = arguments.get("cmd", "echo no-command")
            cwd = arguments.get("cwd", ".")
            timeout = arguments.get("timeout", 30)
            workspace_path = await artifact_service.prepare_checkout(session_id)
            sandbox_result = await execute_in_sandbox(
                command,
                cwd,
                timeout,
                session_id,
                "demo",
                workspace_path=workspace_path,
            )
            output = {"command": command, "sandbox": sandbox_result}
            if int(sandbox_result.get("exit_code", 1)) != 0:
                status = "error"
                error = str(
                    sandbox_result.get("error")
                    or f"Sandbox command exited with code {sandbox_result.get('exit_code', 1)}"
                )

        elif name == "upsert_memory_entity":
            from .memory_service import memory_service

            key = str(arguments.get("key") or "").strip()
            value = str(arguments.get("value") or "").strip()
            if not key or not value:
                raise ValueError("key and value are required")
            metadata = arguments.get("metadata")
            if metadata is not None and not isinstance(metadata, dict):
                raise ValueError("metadata must be an object")
            updated_memory = memory_service.upsert_entity(
                session_id,
                key=key,
                value=value,
                kind=str(arguments.get("kind") or "fact"),
                source=caller,
                metadata=metadata,
            )
            entity = next(
                item for item in updated_memory.get("entities") or []
                if item.get("key") == key
            )
            output = {"entity": entity}

        elif name == "list_tasks":
            from ..routers.tasks import _public_task, fake_tasks

            output = {
                "tasks": [
                    _public_task(task)
                    for task in fake_tasks
                    if str(task.session_id) == str(session_id)
                ]
            }

        elif name == "claim_task":
            from ..routers.tasks import _blocked_by, _public_task, _save_tasks, fake_tasks

            task_id = UUID(str(arguments.get("task_id") or ""))
            async with with_lock(redis_client, f"claim:{task_id}", timeout=10):
                matched = None
                for index, task in enumerate(fake_tasks):
                    if task.id == task_id and str(task.session_id) == str(session_id):
                        if task.status != "backlog":
                            raise ValueError("Task is not available for claiming")
                        blockers = _blocked_by(task)
                        if blockers:
                            raise ValueError(
                                "Task is blocked by: "
                                + ", ".join(str(blocker) for blocker in blockers)
                            )
                        matched = task.model_copy(update={"status": "claimed", "claimed_by": caller})
                        fake_tasks[index] = matched
                        _save_tasks()
                        break
            if matched is None:
                raise KeyError("Task not found")
            output = {"task": _public_task(matched)}

        elif name == "subdivide_task":
            from ..models.schemas import Task
            from ..routers.tasks import _save_tasks, fake_tasks

            task_id = UUID(str(arguments.get("task_id") or ""))
            parent = next(
                (task for task in fake_tasks if task.id == task_id and str(task.session_id) == str(session_id)),
                None,
            )
            if parent is None:
                raise KeyError("Task not found")
            titles = [str(title).strip() for title in arguments.get("subtasks") or [] if str(title).strip()]
            if not titles:
                raise ValueError("subtasks must contain at least one title")
            created = [
                Task(
                    id=uuid4(),
                    session_id=UUID(str(session_id)),
                    title=title,
                    description=f"拆分自：{parent.title}",
                    status="backlog",
                    priority=parent.priority,
                )
                for title in titles
            ]
            fake_tasks.extend(created)
            _save_tasks()
            output = {"parent_task_id": str(parent.id), "tasks": [task.model_dump(mode="json") for task in created]}

        elif name == "get_progress_summary":
            from ..routers.tasks import _blocked_by, fake_tasks

            session_tasks = [task for task in fake_tasks if str(task.session_id) == str(session_id)]
            counts = {
                status_name: sum(task.status == status_name for task in session_tasks)
                for status_name in ("backlog", "claimed", "in_progress", "review", "merged", "done")
            }
            output = {
                "total": len(session_tasks),
                "counts": counts,
                "blocked": sum(bool(_blocked_by(task)) for task in session_tasks),
                "ready": sum(not _blocked_by(task) for task in session_tasks),
            }

        elif name in {"create_task", "create"}:
            from ..models.schemas import Task
            from ..routers.tasks import (
                _public_task,
                _save_tasks,
                _validate_dependencies,
                fake_tasks,
            )

            title = str(arguments.get("title") or "").strip()
            if not title:
                raise ValueError("title is required")
            task_id = uuid4()
            parsed_session_id = UUID(str(session_id))
            raw_dependencies = arguments.get("depends_on") or []
            if not isinstance(raw_dependencies, list):
                raise ValueError("depends_on must be an array of task IDs")
            dependencies = _validate_dependencies(
                session_id=parsed_session_id,
                task_id=task_id,
                dependencies=[UUID(str(value)) for value in raw_dependencies],
            )
            task = Task(
                id=task_id,
                session_id=parsed_session_id,
                title=title,
                description=str(arguments.get("description") or ""),
                status="backlog",
                priority=int(arguments.get("priority", 0)),
                depends_on=dependencies,
            )
            fake_tasks.append(task)
            _save_tasks()
            output = {"task": _public_task(task)}

        elif name in {"create_merge_proposal", "propose_merge"}:
            path = artifact_service.normalize_path(arguments.get("artifact_path", "src/main.py"))
            rationale = str(arguments.get("rationale") or "Model proposed update")
            proposal = merge_store.create(
                session_id=session_id,
                artifact_path=path,
                proposer=str(arguments.get("proposer") or caller),
                rationale=rationale,
                from_hash=arguments.get("from_hash"),
                to_hash=arguments.get("to_hash"),
                merge_rule=str(arguments.get("merge_rule") or "two_votes"),
            )
            output = {"proposal": proposal}

        elif name == "vote_on_proposal":
            proposal_id = str(arguments.get("proposal_id") or "")
            current = merge_store.get(proposal_id)
            if current is None or str(current.get("session_id")) != str(session_id):
                raise KeyError("Merge proposal not found")
            updated_proposal, accepted = merge_store.vote(
                proposal_id,
                voter=str(arguments.get("voter") or caller),
                approve=bool(arguments.get("approve", True)),
                role=arguments.get("role"),
                manual_confirmation=bool(arguments.get("manual_confirmation", False)),
            )
            output = {"proposal": updated_proposal, "vote_accepted": accepted}

        else:
            raise KeyError(f"Unknown internal tool: {name}")

    except Exception as exc:
        status = "error"
        error = str(exc)
        output = None

    if task_started:
        from ..routers.tasks import finish_task_tool

        synchronized_task = finish_task_tool(
            linked_task_id,
            session_id,
            success=status == "success",
        )
        if isinstance(output, dict) and synchronized_task is not None:
            output["task_sync"] = synchronized_task

    if registry is None:
        from .tool_registry import tool_registry

        registry = tool_registry
    if tool_id is None:
        tool = registry.find_by_name(name, session_id)
        tool_id = tool.get("id") if tool else None

    return registry.record_run(
        tool_id=tool_id,
        tool_name=name,
        caller=caller,
        session_id=session_id,
        arguments=arguments,
        status=status,
        output=output,
        error=error,
        started_at=started_at,
    )
