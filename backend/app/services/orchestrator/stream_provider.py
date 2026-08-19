from __future__ import annotations

import asyncio
import json
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
from ._constants import MAX_MODEL_TURNS, PSEUDO_TOOL_CALL_RE


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
        first_token_reported = False

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

            await apply_queued_steers()
            for _provider_turn in range(MAX_MODEL_TURNS):
                if self._is_interrupted(name):
                    break
                forced_tool_name = tool_loop_state.current_required_tool
                forced_tool = next(
                    (
                        tool for tool in provider_tools
                        if tool.name == forced_tool_name
                    ),
                    None,
                )
                if forced_tool_name and forced_tool is None:
                    tool_loop_state.mark_required_failed(forced_tool_name)
                    full_parts = [
                        (
                            f"无法执行本次生成：所需工具 `{forced_tool_name}` 当前未启用。"
                            "请启用 ComfyUI 插件后重试。"
                        )
                    ]
                    completed = True
                    break
                request_tools = (
                    [forced_tool]
                    if forced_tool is not None
                    else tool_loop_state.enabled_tools(provider_tools)
                )
                force_tool_call = forced_tool is not None
                if _provider_turn > 0:
                    request_started_at = monotonic()
                    first_token_reported = False
                    estimated_context_tokens = sum(
                        estimate_tokens(message.content) + 8
                        for message in local_context
                    )
                    await queue.put({
                        "type": "provider_status",
                        "model": name,
                        "provider_type": provider.config.type,
                        "phase": "requesting",
                        "detail": "工具结果已加入上下文，正在等待模型继续",
                        "context_messages": len(local_context),
                        "context_tokens": estimated_context_tokens,
                        "tool_count": len(request_tools),
                        "tool_schema_tokens": tool_schema_tokens,
                        "round_index": tool_loop_state.tool_round,
                        "elapsed_ms": 0,
                        "ts": datetime.now(UTC).isoformat(),
                    })
                round_text = ""
                round_tool_calls: list[dict] = []
                round_usage: dict | None = None
                async for chunk in provider.stream_chat(
                    messages=local_context,
                    tools=request_tools,
                    session_id=self.session_id,
                    tool_choice="required" if force_tool_call else None,
                ):
                    if self._is_interrupted(name):
                        break
                    delta = chunk.get("delta") or ""
                    if chunk.get("tool_activity") and not first_token_reported:
                        first_token_reported = True
                        elapsed_ms = round(
                            (monotonic() - request_started_at) * 1000
                        )
                        await queue.put({
                            "type": "provider_status",
                            "model": name,
                            "provider_type": provider.config.type,
                            "phase": "tool",
                            "detail": "模型已开始构造工具调用",
                            "context_messages": len(local_context),
                            "context_tokens": estimated_context_tokens,
                            "tool_count": len(request_tools),
                            "tool_schema_tokens": tool_schema_tokens,
                            "round_index": tool_loop_state.tool_round,
                            "first_token_ms": elapsed_ms,
                            "elapsed_ms": elapsed_ms,
                            "ts": datetime.now(UTC).isoformat(),
                        })
                    if delta:
                        if not first_token_reported:
                            first_token_reported = True
                            await queue.put({
                                "type": "provider_status",
                                "model": name,
                                "provider_type": provider.config.type,
                                "phase": "streaming",
                                "detail": "已收到首个 token，正在生成回复",
                                "context_messages": len(local_context),
                                "context_tokens": estimated_context_tokens,
                                "tool_count": len(request_tools),
                                "tool_schema_tokens": tool_schema_tokens,
                                "round_index": tool_loop_state.tool_round,
                                "first_token_ms": round(
                                    (monotonic() - request_started_at) * 1000
                                ),
                                "elapsed_ms": round(
                                    (monotonic() - request_started_at) * 1000
                                ),
                                "ts": datetime.now(UTC).isoformat(),
                            })
                        round_text += delta
                    round_tool_calls.extend(chunk.get("tool_calls") or [])
                    if round_tool_calls and not first_token_reported:
                        first_token_reported = True
                        await queue.put({
                            "type": "provider_status",
                            "model": name,
                            "provider_type": provider.config.type,
                            "phase": "tool",
                            "detail": "模型已返回结构化工具调用",
                            "context_messages": len(local_context),
                            "context_tokens": estimated_context_tokens,
                            "tool_count": len(request_tools),
                            "tool_schema_tokens": tool_schema_tokens,
                            "round_index": tool_loop_state.tool_round,
                            "first_token_ms": round(
                                (monotonic() - request_started_at) * 1000
                            ),
                            "elapsed_ms": round(
                                (monotonic() - request_started_at) * 1000
                            ),
                            "ts": datetime.now(UTC).isoformat(),
                        })
                    if chunk.get("usage"):
                        round_usage = chunk["usage"]

                became_exhausted = False
                if round_usage:
                    prompt_tokens = int(round_usage.get("prompt_tokens", 0) or 0)
                    completion_tokens = int(round_usage.get("completion_tokens", 0) or 0)
                    total_prompt_tokens += prompt_tokens
                    total_completion_tokens += completion_tokens
                    cost = self._calculate_cost(provider, round_usage)
                    total_cost += cost
                    async with self._budget_lock:
                        self.budget_remaining = max(0.0, round(self.budget_remaining - cost, 6))
                        became_exhausted = self.budget_remaining <= 0 and not self._budget_exhausted
                        if became_exhausted:
                            self._budget_exhausted = True
                    await queue.put({"type": "cost_update", "budget_remaining": self.budget_remaining})

                if self._has_pending_steers(name):
                    if round_text.strip():
                        local_context.append(ChatMessage(
                            role="assistant",
                            content=round_text.strip(),
                        ))
                    await queue.put({"type": "stream_reset", "model": name})
                    await apply_queued_steers()
                    continue

                if force_tool_call and not round_tool_calls:
                    await queue.put({"type": "stream_reset", "model": name})
                    if tool_loop_state.record_pseudo_tool_retry():
                        await queue.put({
                            "type": "error",
                            "code": "required_tool_not_called",
                            "model": name,
                            "message": (
                                f"{name} 未按要求调用 {forced_tool_name}；"
                                "已停止，避免把未执行的图片生成表述为成功。"
                            ),
                        })
                        break
                    if round_text.strip():
                        local_context.append(ChatMessage(
                            role="assistant",
                            content=round_text.strip(),
                        ))
                    local_context.append(ChatMessage(
                        role="user",
                        content=(
                            f"必须立即调用工具 {forced_tool_name}。"
                            "不要输出计划、解释或未来时描述；本轮只能返回结构化工具调用。"
                        ),
                    ))
                    continue

                pseudo_match = PSEUDO_TOOL_CALL_RE.search(round_text)
                if not round_tool_calls and pseudo_match:
                    requested_name = pseudo_match.group(1)
                    definition = next(
                        (tool for tool in provider_tools if tool.name == requested_name),
                        None,
                    )
                    if definition is None:
                        candidate = next(
                            (
                                tool
                                for tool in available_tool_definitions
                                if tool.name == requested_name
                            ),
                            None,
                        )
                        if (
                            candidate is not None
                            and (
                                candidate.name not in WORKSPACE_TOOL_NAMES
                                or workspace_id is not None
                            )
                            and (
                                allow_side_effects
                                or not self.run_service.is_side_effecting_tool(
                                    candidate.name
                                )
                            )
                        ):
                            # Recover the one registered tool named by the
                            # model without putting every schema back into
                            # ordinary prompts.
                            provider_tools.append(candidate)
                            definition = candidate
                    await queue.put({"type": "stream_reset", "model": name})
                    if definition is None:
                        await queue.put({
                            "type": "error",
                            "code": "unknown_pseudo_tool",
                            "model": name,
                            "message": f"{name} 请求了不存在的工具：{requested_name}",
                        })
                        break
                    if tool_loop_state.record_pseudo_tool_retry():
                        await queue.put({
                            "type": "error",
                            "code": "tool_call_format",
                            "model": name,
                            "message": f"{name} 连续以文本描述工具调用，已停止；请重试或检查本地模型工具调用模板。",
                        })
                        break
                    local_context.append(ChatMessage(role="assistant", content=round_text.strip()))
                    local_context.append(ChatMessage(
                        role="user",
                        content=(
                            f"你刚才没有真正调用工具。立即发起结构化工具调用 {requested_name}，"
                            "不要输出解释或‘请求执行工具’文本。"
                            f"参数 JSON Schema：{json.dumps(definition.parameters, ensure_ascii=False)}。"
                            "请根据对话与已有工具结果填写所有必需参数。"
                        ),
                    ))
                    continue

                if not round_tool_calls:
                    final_text = round_text.strip()
                    if final_text:
                        # Only the terminal provider turn is user-facing. Text
                        # emitted alongside tool requests is execution scratch
                        # space (for example, repeated "让我检查…" requests) and
                        # must stay in the model context instead of becoming
                        # chat history or a live chat bubble.
                        full_parts = [final_text]
                        await queue.put({
                            "type": "delta",
                            "model": name,
                            "delta": final_text,
                            "ts": datetime.now(UTC).isoformat(),
                        })
                    completed = True
                    break
                if became_exhausted:
                    await queue.put({
                        "type": "error",
                        "code": "budget_exceeded",
                        "message": "会话预算已用尽，电脑控制循环已停止。",
                    })
                    self._cancel_running_providers(exclude=name)
                    break
                if tool_loop_state.tool_round_limit_reached:
                    await queue.put({
                        "type": "error",
                        "code": "tool_loop_limit",
                        "model": name,
                        "message": (
                            f"{name} 达到 {tool_loop_state.max_tool_rounds} "
                            "轮工具调用上限，已安全停止。"
                        ),
                    })
                    break

                # Older renderers may still have shown provider deltas before
                # learning that this turn contains a tool call. Clear that
                # draft defensively; current renderers receive no such deltas.
                await queue.put({"type": "stream_reset", "model": name})
                canonical_tool_calls = self.tool_loop.normalize_tool_calls(
                    round_tool_calls
                )
                for tool_call in canonical_tool_calls:
                    await record_reasoning(
                        "tool",
                        f"已选择调用 {tool_call.name}，等待授权或本机执行结果。",
                    )
                local_context.append(ChatMessage(
                    role="assistant",
                    content=round_text.strip(),
                    tool_calls=canonical_tool_calls,
                ))

                tool_results = await self._execute_round_tool_calls(
                    canonical_tool_calls=canonical_tool_calls,
                    name=name,
                    provider=provider,
                    queue=queue,
                    content=content,
                    attachment_image_paths=attachment_image_paths,
                    tool_loop_state=tool_loop_state,
                    generated_files=generated_files,
                    tool_definitions_by_name=tool_definitions_by_name,
                    agent_run_id=agent_run_id,
                    plan_step_id=plan_step_id,
                    workspace_id=workspace_id,
                    background_job_id=background_job_id,
                    background_allowed_tools=background_allowed_tools,
                    forced_tool_name=forced_tool_name,
                    local_context=local_context,
                    record_reasoning=record_reasoning,
                    tool_is_allowed=tool_is_allowed,
                )

                if agent_run_id:
                    self._save_provider_checkpoint(
                        agent_run_id,
                        name,
                        local_context,
                        next_tool_round=tool_loop_state.tool_round,
                        kind="tool_round",
                        plan_step_id=plan_step_id,
                    )
                if tool_loop_state.invalid_file_limit_reached:
                    invalid_call_message = (
                        f"{name} 连续 {tool_loop_state.invalid_file_tool_calls} "
                        "次返回不完整或无效的"
                        "文件写入参数，已停止继续尝试，避免反复覆盖或产生空文件。"
                    )
                    await queue.put({
                        "type": "error",
                        "code": "invalid_file_tool_arguments",
                        "model": name,
                        "message": invalid_call_message,
                    })
                    full_parts = [
                        invalid_call_message
                        + " 本轮无效调用均未执行；请重试，系统将要求模型使用分块工件后再一次性导出。"
                    ]
                    completed = True
                    break
                if required_comfyui_tools:
                    if tool_loop_state.required_tools_complete:
                        full_parts = [
                            self._comfyui_completion_content(generated_files)
                        ]
                        completed = True
                        break
                    if tool_loop_state.required_tool_failed:
                        failure_detail = ""
                        for _tool_call, payload, _images in reversed(tool_results):
                            failure_detail = str(payload.get("error") or "").strip()
                            if failure_detail:
                                break
                        full_parts = [
                            "ComfyUI 生成未完成"
                            + (f"：{failure_detail}" if failure_detail else "，请检查工具日志后重试。")
                        ]
                        completed = True
                        break

            else:
                await queue.put({
                    "type": "error",
                    "code": "model_turn_limit",
                    "model": name,
                    "message": f"{name} 达到 {MAX_MODEL_TURNS} 次模型调用上限，已安全停止。",
                })

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

