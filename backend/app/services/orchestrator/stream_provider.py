from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID, uuid4

from ...models.schemas import ChatMessage, Message
from ...providers.base import LLMProvider
from ..chat_database import chat_database
from ..context_builder import build_messages_for_provider, estimate_tokens
from ..git_workspace_service import (
    WORKSPACE_TOOL_NAMES,
)
from ..memory_service import memory_service


class StreamProviderMixin:
    async def _run_selected_provider(
        self,
        name: str,
        provider: LLMProvider,
        execution: dict | None = None,
        *,
        queue: asyncio.Queue[dict],
        content: str,
        action: dict,
        available_tool_definitions,
        tool_definitions_by_name: dict,
        background_job_id: str,
        background_allowed_tools: set[str],
        context_msgs: list,
        attachment_images,
        attachment_image_paths,
        agent_run_id: str,
    ) -> None:
        current_task = asyncio.current_task()
        if current_task is None:  # pragma: no cover - only possible outside a running loop
            raise RuntimeError("Provider task is not attached to an event loop")
        self._running_tasks[name] = current_task
        execution = execution or {}
        plan_step_id = str(execution.get("plan_step_id") or "") or None
        workspace_id = str(execution.get("workspace_id") or "") or None
        publish_message = bool(execution.get("publish_message", True))
        allow_side_effects = bool(execution.get("allow_side_effects", True))

        def tool_is_allowed(tool_name: str) -> bool:
            if not self.run_service.is_side_effecting_tool(tool_name):
                return True
            if not allow_side_effects:
                return False
            if background_job_id:
                return tool_name in background_allowed_tools
            return True

        routed_tools = self._route_tools(
            content,
            available_tool_definitions,
            {**action, **execution},
        )
        provider_tools = [
            tool for tool in routed_tools
            if (
                (
                    tool.name not in WORKSPACE_TOOL_NAMES
                    or workspace_id is not None
                )
                and (
                    tool_is_allowed(tool.name)
                )
            )
        ]
        required_comfyui_tools = [
            name
            for name in self._required_comfyui_tools(
                content,
                {**action, **execution},
            )
            if tool_is_allowed(name)
        ]
        full_parts: list[str] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_cost = 0.0
        completed = False
        message_emitted = False
        generated_files: list[dict] = []
        reasoning_summary: list[dict[str, str]] = []
        request_started_at = monotonic()

        async def record_reasoning(phase: str, detail: str) -> None:
            """Publish a safe, observable reasoning summary.

            This intentionally records orchestration facts only. Provider
            hidden reasoning and raw chain-of-thought are never persisted
            or forwarded to the renderer.
            """

            normalized = " ".join(str(detail or "").split())[:240]
            if not normalized or len(reasoning_summary) >= 32:
                return
            entry = {
                "phase": phase,
                "detail": normalized,
                "ts": datetime.now(UTC).isoformat(),
            }
            if reasoning_summary and (
                reasoning_summary[-1]["phase"] == phase
                and reasoning_summary[-1]["detail"] == normalized
            ):
                return
            reasoning_summary.append(entry)
            await queue.put({
                "type": "reasoning_step",
                "model": name,
                "step": entry,
                "reset": len(reasoning_summary) == 1,
                "plan_step_id": plan_step_id,
            })

        try:
            if self._is_interrupted(name):
                return
            checkpoint = execution.get("resume_checkpoint")
            if checkpoint is None:
                checkpoint = (action.get("_resume_checkpoints") or {}).get(name)
            provider_limit, output_reserve, tool_schema_tokens = (
                self._context_limits(provider, provider_tools)
            )
            restored_context, tool_round = self._restore_checkpoint(checkpoint)
            system_prompt = self._provider_system_prompt(name, provider)
            if restored_context:
                source_context = restored_context
                context_system_prompt = ""
            else:
                source_context = execution.get("context_messages") or context_msgs
                step_instruction = str(execution.get("instruction") or "").strip()
                if step_instruction:
                    system_prompt = (
                        f"{system_prompt}\n\n"
                        "【当前 Plan Step】\n"
                        f"{step_instruction}"
                    )
                context_system_prompt = system_prompt
            local_context = await build_messages_for_provider(
                source_context,
                provider_limit=provider_limit,
                output_reserve=output_reserve,
                system_prompt=context_system_prompt,
                max_conversation_messages=(
                    self._history_message_limit(provider)
                ),
            )
            tool_loop_state = self.tool_loop.start(
                initial_tool_round=tool_round,
                required_tool_names=required_comfyui_tools,
                context=local_context,
            )
            estimated_context_tokens = sum(
                estimate_tokens(message.content) + 8
                for message in local_context
            )
            await record_reasoning(
                "analysis",
                (
                    f"已整理 {len(local_context)} 条上下文，"
                    f"并筛选 {len(provider_tools)} 个与请求相关的可用工具。"
                ),
            )
            await queue.put({
                "type": "provider_status",
                "model": name,
                "provider_type": provider.config.type,
                "phase": "requesting",
                "detail": "上下文已整理，正在等待模型返回首个 token",
                "context_messages": len(local_context),
                "context_tokens": estimated_context_tokens,
                "tool_count": len(provider_tools),
                "tool_schema_tokens": tool_schema_tokens,
                "round_index": tool_loop_state.tool_round,
                "elapsed_ms": 0,
                "ts": datetime.now(UTC).isoformat(),
            })

            async def apply_queued_steers(kind: str = "steer") -> bool:
                steers = self._consume_steers(name)
                if not steers:
                    return False
                for steer in steers:
                    local_context.append(ChatMessage(
                        role="user",
                        content=(
                            "【运行中用户转向】\n"
                            f"{str(steer.get('content') or '').strip()}\n"
                            "以这条最新指令为准；重新检查尚未执行的计划和工具调用。"
                        ),
                    ))
                if agent_run_id:
                    self._save_provider_checkpoint(
                        agent_run_id,
                        name,
                        local_context,
                        next_tool_round=tool_loop_state.tool_round,
                        kind=kind,
                        plan_step_id=plan_step_id,
                    )
                for steer in steers:
                    steer_id = str(steer.get("id") or "")
                    if agent_run_id:
                        self.run_service.mark_steer_applied(agent_run_id, steer_id, name)
                    await queue.put({
                        "type": "steer_applied",
                        "model": name,
                        "steer_id": steer_id,
                    })
                return True

            (
                completed,
                total_prompt_tokens,
                total_completion_tokens,
                total_cost,
                estimated_context_tokens,
                request_started_at,
            ) = await self._run_provider_turns(
                name=name,
                provider=provider,
                queue=queue,
                content=content,
                action=action,
                agent_run_id=agent_run_id,
                plan_step_id=plan_step_id,
                workspace_id=workspace_id,
                available_tool_definitions=available_tool_definitions,
                tool_definitions_by_name=tool_definitions_by_name,
                background_job_id=background_job_id,
                background_allowed_tools=background_allowed_tools,
                provider_tools=provider_tools,
                tool_loop_state=tool_loop_state,
                local_context=local_context,
                full_parts=full_parts,
                generated_files=generated_files,
                required_comfyui_tools=required_comfyui_tools,
                allow_side_effects=allow_side_effects,
                tool_schema_tokens=tool_schema_tokens,
                estimated_context_tokens=estimated_context_tokens,
                request_started_at=request_started_at,
                attachment_image_paths=attachment_image_paths,
                record_reasoning=record_reasoning,
                apply_queued_steers=apply_queued_steers,
                tool_is_allowed=tool_is_allowed,
            )

            final_content = "\n\n".join(full_parts).strip()
            if not final_content and completed:
                final_content = "操作已完成。"
            if final_content and completed:
                await record_reasoning(
                    "final",
                    "已核对模型输出与工具结果，正在整理最终答复。",
                )
                await queue.put({
                    "type": "provider_status",
                    "model": name,
                    "provider_type": provider.config.type,
                    "phase": "finalizing",
                    "detail": "模型生成结束，正在持久化消息与文件关联",
                    "context_messages": len(local_context),
                    "context_tokens": estimated_context_tokens,
                    "tool_count": len(provider_tools),
                    "tool_schema_tokens": tool_schema_tokens,
                    "round_index": tool_loop_state.tool_round,
                    "elapsed_ms": round(
                        (monotonic() - request_started_at) * 1000
                    ),
                    "ts": datetime.now(UTC).isoformat(),
                })
                if agent_run_id:
                    self._save_provider_checkpoint(
                        agent_run_id,
                        name,
                        [
                            *local_context,
                            ChatMessage(role="assistant", content=final_content),
                        ],
                        next_tool_round=tool_loop_state.tool_round,
                        kind="completed",
                        plan_step_id=plan_step_id,
                    )
                if publish_message:
                    message = Message(
                        id=uuid4(),
                        session_id=UUID(self.session_id),
                        author_model=name,
                        role="assistant",
                        content=final_content,
                        tokens_in=total_prompt_tokens,
                        tokens_out=total_completion_tokens,
                        cost_usd=round(total_cost, 6),
                        generated_files=generated_files,
                        reasoning_summary=reasoning_summary,
                        created_at=datetime.now(UTC),
                    )
                    self.conversation_history.append(
                        ChatMessage(role="assistant", content=final_content)
                    )
                    await chat_database.run_in_thread(
                        chat_database.save_message, message, mode=self.mode
                    )
                    memory_service.refresh(self.session_id)
                    await queue.put({
                        "type": "message_complete",
                        "message": message.model_dump(mode="json"),
                    })
                else:
                    await queue.put({
                        "type": "plan_step_output",
                        "model": name,
                        "content": final_content,
                        "partial": False,
                    })
                message_emitted = True

        except asyncio.CancelledError:
            await queue.put({"type": "interrupted", "model": name})
        except Exception as exc:
            await queue.put({
                "type": "error",
                "code": "provider_error",
                "model": name,
                "message": f"{name} 调用失败：{exc}",
            })
        finally:
            final_content = "\n\n".join(full_parts).strip()
            if final_content and not message_emitted:
                await record_reasoning(
                    "final",
                    "本轮提前结束；正在保留已经生成的部分答复。",
                )
                if publish_message:
                    message = Message(
                        id=uuid4(),
                        session_id=UUID(self.session_id),
                        author_model=name,
                        role="assistant",
                        content=final_content,
                        tokens_in=total_prompt_tokens,
                        tokens_out=total_completion_tokens,
                        cost_usd=round(total_cost, 6),
                        generated_files=generated_files,
                        reasoning_summary=reasoning_summary,
                        created_at=datetime.now(UTC),
                    )
                    self.conversation_history.append(
                        ChatMessage(role="assistant", content=final_content)
                    )
                    await chat_database.run_in_thread(
                        chat_database.save_message, message, mode=self.mode
                    )
                    memory_service.refresh(self.session_id)
                    await queue.put({
                        "type": "message_complete",
                        "message": message.model_dump(mode="json"),
                        "partial": True,
                    })
                else:
                    await queue.put({
                        "type": "plan_step_output",
                        "model": name,
                        "content": final_content,
                        "partial": True,
                    })
            if name in self._running_tasks:
                del self._running_tasks[name]
            await queue.put({
                "type": "provider_done",
                "model": name,
                "plan_step_id": plan_step_id,
            })

