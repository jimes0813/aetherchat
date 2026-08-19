from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from ..git_workspace_service import GitWorkspaceError


class StreamPlanMixin:
    async def _stream_selected_batches(
        self,
        *,
        selected: list[str],
        planned_execution: bool,
        plan_bundle: dict | None,
        agent_run_id: str,
        queue: asyncio.Queue[dict],
        run_provider: Callable[..., Coroutine[Any, Any, None]],
        action: dict,
        workspace_service: Any,
        context_msgs: list[Any],
    ) -> AsyncIterator[dict]:
        async def stream_batch(
            executions: list[dict],
            outcomes: dict[str, dict],
        ) -> AsyncIterator[dict]:
            step_by_model = {
                str(item["model"]): item
                for item in executions
            }
            tasks: list[asyncio.Task[None]] = [
                asyncio.create_task(
                    run_provider(
                        str(item["model"]),
                        self.providers[str(item["model"])],
                        item,
                    )
                )
                for item in executions
            ]
            for item in executions:
                key = str(item.get("plan_step_id") or item["model"])
                outcomes[key] = {
                    "status": "running",
                    "content": "",
                    "error": None,
                    "code": None,
                }
            fatal_codes = {
                "provider_error", "budget_exceeded", "tool_loop_limit",
                "model_turn_limit", "tool_result_uncertain",
                "unknown_pseudo_tool", "tool_call_format",
                "required_tool_not_called",
            }
            done = 0
            while done < len(tasks):
                event = await queue.get()
                event_model = str(
                    event.get("model")
                    or (event.get("approval") or {}).get("model")
                    or (event.get("message") or {}).get("author_model")
                    or ""
                )
                execution = step_by_model.get(event_model)
                plan_step = execution.get("step") if execution else None
                key = str(
                    event.get("plan_step_id")
                    or (execution or {}).get("plan_step_id")
                    or event_model
                )
                if plan_step is not None:
                    event = {
                        **event,
                        "run_id": agent_run_id,
                        "plan_step_id": plan_step["id"],
                        "plan_step_key": plan_step["step_key"],
                        "plan_step_kind": plan_step["kind"],
                    }
                if event.get("type") == "provider_done":
                    done += 1
                    if key in outcomes and outcomes[key]["status"] == "running":
                        outcomes[key] = {
                            "status": "failed",
                            "content": outcomes[key].get("content") or "",
                            "error": "模型未产生可确认的步骤输出",
                        }
                    continue
                if event.get("type") == "error":
                    code = str(event.get("code") or "")
                    affected = [key] if key in outcomes else list(outcomes)
                    if code in fatal_codes or not event_model:
                        for outcome_key in affected:
                            outcomes[outcome_key]["status"] = "failed"
                            outcomes[outcome_key]["code"] = code
                            outcomes[outcome_key]["error"] = str(
                                event.get("message") or "计划步骤执行失败"
                            )
                elif event.get("type") == "interrupted" and key in outcomes:
                    outcomes[key]["status"] = "interrupted"
                    outcomes[key]["error"] = "用户中断"
                elif event.get("type") == "plan_step_output" and key in outcomes:
                    outcomes[key]["content"] = str(event.get("content") or "")
                    if event.get("partial"):
                        if outcomes[key]["status"] == "running":
                            outcomes[key]["status"] = "failed"
                            outcomes[key]["error"] = "模型只产生了部分输出"
                    elif outcomes[key]["status"] == "running":
                        outcomes[key]["status"] = "completed"
                elif event.get("type") == "message_complete" and key in outcomes:
                    message = event.get("message") or {}
                    if (
                        message.get("role") == "assistant"
                        and message.get("author_model") == event_model
                    ):
                        outcomes[key]["content"] = str(message.get("content") or "")
                        if event.get("partial"):
                            if outcomes[key]["status"] == "running":
                                outcomes[key]["status"] = "failed"
                                outcomes[key]["error"] = "模型只产生了部分输出"
                        elif outcomes[key]["status"] == "running":
                            outcomes[key]["status"] = "completed"

                if (
                    execution is not None
                    and not execution.get("publish_message", True)
                    and event.get("type") in {"delta", "stream_reset"}
                ):
                    continue
                yield event
            await asyncio.gather(*tasks, return_exceptions=True)

        if not planned_execution or plan_bundle is None:
            tasks: list[asyncio.Task[None]] = [
                asyncio.create_task(run_provider(name, self.providers[name]))
                for name in selected
            ]
            done = 0
            while done < len(tasks):
                event = await queue.get()
                if event.get("type") == "provider_done":
                    done += 1
                    continue
                yield event
            await asyncio.gather(*tasks, return_exceptions=True)
            return

        plan_terminal_error_emitted = False
        while True:
            plan_steps = self.run_service.database.list_agent_plan_steps(agent_run_id)
            if self._plan_cancelled:
                for step in plan_steps:
                    if step["status"] in {"pending", "ready", "running"}:
                        interrupted_step = self.run_service.finish_plan_step(
                            agent_run_id,
                            step["id"],
                            status="interrupted",
                            error="用户中断整个计划",
                        )
                        yield {
                            "type": "plan_step_update",
                            "run_id": agent_run_id,
                            "plan_step": self._public_plan_step(interrupted_step),
                        }
                break
            by_key = {step["step_key"]: step for step in plan_steps}
            synthesizer = by_key.get("synthesizer")
            if synthesizer and synthesizer["status"] == "completed":
                break

            pending = [
                step for step in plan_steps
                if step["status"] in {"pending", "ready", "interrupted"}
            ]
            if not pending:
                break

            ready: list[dict] = []
            for step in pending:
                if (
                    step["kind"] in {"verifier", "synthesizer"}
                    and all(
                        candidate["status"] in {
                            "completed", "failed", "blocked", "interrupted", "skipped",
                        }
                        for candidate in plan_steps
                        if candidate["kind"] == "worker"
                    )
                    and not any(
                        candidate["kind"] == "worker"
                        and candidate["status"] == "completed"
                        for candidate in plan_steps
                    )
                ):
                    blocked = self.run_service.finish_plan_step(
                        agent_run_id,
                        step["id"],
                        status="blocked",
                        error="没有成功完成的 Worker 输出，无法核验或综合",
                    )
                    yield {
                        "type": "plan_step_update",
                        "run_id": agent_run_id,
                        "plan_step": self._public_plan_step(blocked),
                    }
                    continue
                dependency_states = [
                    by_key.get(key, {}).get("status")
                    for key in step.get("dependencies") or []
                ]
                if all(status == "completed" for status in dependency_states):
                    ready.append(step)
                    continue
                terminal_dependencies = all(
                    status in {
                        "completed", "failed", "blocked", "interrupted", "skipped",
                    }
                    for status in dependency_states
                )
                if step.get("allow_failed_dependencies") and terminal_dependencies:
                    ready.append(step)
                    continue
                if terminal_dependencies and any(
                    status != "completed" for status in dependency_states
                ):
                    blocked = self.run_service.finish_plan_step(
                        agent_run_id,
                        step["id"],
                        status="blocked",
                        error="依赖步骤失败，当前步骤未执行",
                    )
                    yield {
                        "type": "plan_step_update",
                        "run_id": agent_run_id,
                        "plan_step": self._public_plan_step(blocked),
                    }

            if not ready:
                refreshed = self.run_service.database.list_agent_plan_steps(agent_run_id)
                unresolved = [
                    step for step in refreshed
                    if step["status"] in {"pending", "ready", "interrupted"}
                ]
                if unresolved:
                    for step in unresolved:
                        blocked = self.run_service.finish_plan_step(
                            agent_run_id,
                            step["id"],
                            status="blocked",
                            error="计划依赖无法满足或检测到无进展状态",
                        )
                        yield {
                            "type": "plan_step_update",
                            "run_id": agent_run_id,
                            "plan_step": self._public_plan_step(blocked),
                        }
                    yield {
                        "type": "error",
                        "code": "plan_blocked",
                        "message": "Plan DAG 没有可执行步骤，已安全停止。",
                    }
                    plan_terminal_error_emitted = True
                break

            next_phase = min(int(step["phase"]) for step in ready)
            phase_steps = [
                step for step in ready if int(step["phase"]) == next_phase
            ]
            if self.budget_remaining <= 0:
                for step in phase_steps:
                    failed_step = self.run_service.finish_plan_step(
                        agent_run_id,
                        step["id"],
                        status="failed",
                        error="会话预算已用尽，计划步骤未执行",
                    )
                    yield {
                        "type": "plan_step_update",
                        "run_id": agent_run_id,
                        "plan_step": self._public_plan_step(failed_step),
                    }
                yield {
                    "type": "error",
                    "code": "budget_exceeded",
                    "message": "会话预算已用尽，计划执行已停止。",
                }
                plan_terminal_error_emitted = True
                break
            executions: list[dict] = []
            unavailable: list[tuple[dict, str]] = []
            for step in phase_steps:
                model = str(step.get("assigned_model") or "")
                if not model or model not in self.providers:
                    unavailable.append((step, f"计划模型不可用：{model or '未分配'}"))
                    continue
                running_step = self.run_service.start_plan_step(
                    agent_run_id, step["id"]
                )
                yield {
                    "type": "plan_step_update",
                    "run_id": agent_run_id,
                    "plan_step": self._public_plan_step(running_step),
                }
                current_steps = self.run_service.database.list_agent_plan_steps(
                    agent_run_id
                )
                current_by_key = {
                    item["step_key"]: item for item in current_steps
                }
                worker_workspace: dict | None = None
                if (
                    running_step["kind"] == "worker"
                    and action.get("repository_id")
                ):
                    try:
                        worker_workspace = await asyncio.to_thread(
                            workspace_service.prepare_worker_workspace,
                            run_id=agent_run_id,
                            plan_step_id=running_step["id"],
                            session_id=self.session_id,
                            repository_id=str(action["repository_id"]),
                            base_commit=str(
                                action.get("repository_base_commit") or ""
                            ),
                            attempt=int(running_step.get("attempt") or 1),
                        )
                    except (GitWorkspaceError, OSError) as exc:
                        unavailable.append((running_step, str(exc)))
                        continue
                workspace_instruction = ""
                if worker_workspace is not None:
                    workspace_instruction = (
                        "\n\n【独立 Git worktree】\n"
                        "你已获得仅属于当前 Worker 的隔离 worktree。"
                        "先调用 workspace_list_files / workspace_read_file "
                        "理解代码，再用 workspace_write_file、"
                        "workspace_delete_file 或 workspace_run_command 完成实际修改。"
                        "workspace_status 可检查候选变更。"
                        "不要调用 write_artifact 或普通 run_command 来代替代码修改。"
                        "你不能直接访问用户主仓库；结束后系统会独立生成 diff "
                        "并运行计划中已确认的核验命令。"
                    )
                executions.append({
                    "model": model,
                    "plan_step_id": running_step["id"],
                    "step": running_step,
                    "workspace_id": (
                        worker_workspace.get("id")
                        if worker_workspace is not None
                        else None
                    ),
                    "publish_message": running_step["kind"] == "synthesizer",
                    "allow_side_effects": running_step["kind"] == "synthesizer",
                    "instruction": (
                        self._plan_step_instruction(
                            plan_bundle["goal"],
                            running_step,
                        )
                        + workspace_instruction
                    ),
                    "context_messages": self._plan_step_context(
                        context_msgs,
                        plan_bundle["goal"],
                        running_step,
                        current_by_key,
                        self._run_steers,
                        self.run_service.database.list_agent_reviews(
                            agent_run_id
                        ),
                        workspace_service.deterministic_evidence(
                            agent_run_id
                        ),
                    ),
                    "resume_checkpoint": (
                        action.get("_resume_plan_checkpoints") or {}
                    ).get(running_step["id"]),
                })

            for step, error in unavailable:
                failed_step = self.run_service.finish_plan_step(
                    agent_run_id,
                    step["id"],
                    status="failed",
                    error=error,
                )
                yield {
                    "type": "plan_step_update",
                    "run_id": agent_run_id,
                    "plan_step": self._public_plan_step(failed_step),
                }

            if not executions:
                continue

            outcomes: dict[str, dict] = {}
            async for event in stream_batch(executions, outcomes):
                yield event

            review_for_replan: dict | None = None
            for execution in executions:
                step = execution["step"]
                outcome = outcomes.get(step["id"]) or {
                    "status": "failed",
                    "content": "",
                    "error": "计划步骤没有执行结果",
                }
                status = str(outcome.get("status") or "failed")
                if status not in {"completed", "failed", "interrupted"}:
                    status = "failed"
                finished_step = self.run_service.finish_plan_step(
                    agent_run_id,
                    step["id"],
                    status=status,
                    content=str(outcome.get("content") or ""),
                    error=str(outcome.get("error") or "") or None,
                )
                yield {
                    "type": "plan_step_update",
                    "run_id": agent_run_id,
                    "plan_step": self._public_plan_step(finished_step),
                }
                workspace_id = str(
                    execution.get("workspace_id") or ""
                )
                if step["kind"] == "worker" and workspace_id:
                    try:
                        workspace = await workspace_service.finalize_workspace(
                            workspace_id,
                            action.get("verification_commands") or [],
                        )
                        yield {
                            "type": "workspace_verified",
                            "run_id": agent_run_id,
                            "plan_step_id": step["id"],
                            "workspace": (
                                workspace_service.public_workspace(
                                    workspace
                                )
                            ),
                        }
                    except (GitWorkspaceError, OSError) as exc:
                        yield {
                            "type": "error",
                            "code": "workspace_verification_failed",
                            "model": execution["model"],
                            "message": str(exc),
                        }
                if step["kind"] == "verifier" and outcome.get("content"):
                    parsed_review = self._parse_plan_review(
                        str(outcome.get("content") or "")
                    )
                    parsed_review = workspace_service.enforce_review(
                        agent_run_id,
                        parsed_review,
                    )
                    review = self.run_service.record_review(
                        run_id=agent_run_id,
                        plan_step_id=step["id"],
                        session_id=self.session_id,
                        reviewer_model=str(step.get("assigned_model") or ""),
                        review=parsed_review,
                    )
                    yield {
                        "type": "review_recorded",
                        "run_id": agent_run_id,
                        "review": review,
                    }
                    review_for_replan = review
            if review_for_replan is not None:
                revised_run = self.run_service.maybe_revise_after_review(
                    run_id=agent_run_id,
                    session_id=self.session_id,
                    review=review_for_replan,
                )
                if revised_run is not None:
                    revised_steps = (
                        self.run_service.database.list_agent_plan_steps(
                            agent_run_id
                        )
                    )
                    yield {
                        "type": "plan_replanned",
                        "run_id": agent_run_id,
                        "revision": revised_run.get("plan_revision"),
                        "automatic_revision_count": revised_run.get(
                            "automatic_revision_count"
                        ),
                        "trigger_review": review_for_replan,
                        "plan_steps": [
                            self._public_plan_step(item)
                            for item in revised_steps
                        ],
                    }
                    continue
                if review_for_replan.get("verdict") == "fail":
                    current_steps = (
                        self.run_service.database.list_agent_plan_steps(
                            agent_run_id
                        )
                    )
                    synthesizer = next(
                        (
                            item for item in current_steps
                            if item["kind"] == "synthesizer"
                        ),
                        None,
                    )
                    if synthesizer and synthesizer["status"] in {
                        "pending", "ready", "interrupted",
                    }:
                        blocked = self.run_service.finish_plan_step(
                            agent_run_id,
                            synthesizer["id"],
                            status="blocked",
                            error=(
                                "Verifier 在允许的返工次数后仍判定失败；"
                                "已阻止生成未经核验的最终交付"
                            ),
                        )
                        yield {
                            "type": "plan_step_update",
                            "run_id": agent_run_id,
                            "plan_step": self._public_plan_step(blocked),
                        }
                    yield {
                        "type": "error",
                        "code": "plan_verification_failed",
                        "message": (
                            "核验在有限返工后仍失败，已安全停止最终综合。"
                        ),
                    }
                    plan_terminal_error_emitted = True
                    break
            if any(
                outcome.get("code") in {
                    "tool_result_uncertain", "budget_exceeded",
                }
                for outcome in outcomes.values()
            ):
                plan_terminal_error_emitted = True
                break

        final_steps = self.run_service.database.list_agent_plan_steps(agent_run_id)
        final_synthesizer = next(
            (
                step for step in final_steps
                if step["kind"] == "synthesizer"
            ),
            None,
        )
        if (
            not self._plan_cancelled
            and not plan_terminal_error_emitted
            and (
                not final_synthesizer
                or final_synthesizer["status"] != "completed"
            )
        ):
            yield {
                "type": "error",
                "code": "plan_incomplete",
                "message": "计划未生成可确认的最终交付，请在运行面板检查失败步骤。",
            }
