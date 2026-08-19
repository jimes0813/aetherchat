from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import monotonic

from ...models.schemas import ChatMessage
from ..context_builder import estimate_tokens
from ._constants import MAX_MODEL_TURNS, PSEUDO_TOOL_CALL_RE


class StreamTurnsMixin:
    def _provider_status_event(
        self,
        *,
        name: str,
        provider,
        phase: str,
        detail: str,
        local_context,
        estimated_context_tokens: int,
        request_tools,
        tool_schema_tokens: int,
        tool_loop_state,
        elapsed_ms: int = 0,
        first_token_ms: int | None = None,
    ) -> dict:
        event = {
            "type": "provider_status",
            "model": name,
            "provider_type": provider.config.type,
            "phase": phase,
            "detail": detail,
            "context_messages": len(local_context),
            "context_tokens": estimated_context_tokens,
            "tool_count": len(request_tools),
            "tool_schema_tokens": tool_schema_tokens,
            "round_index": tool_loop_state.tool_round,
            "elapsed_ms": elapsed_ms,
            "ts": datetime.now(UTC).isoformat(),
        }
        if first_token_ms is not None:
            event["first_token_ms"] = first_token_ms
        return event

    async def _run_provider_turns(
        self,
        *,
        name: str,
        provider,
        queue: asyncio.Queue,
        content: str,
        action: dict,
        agent_run_id: str,
        plan_step_id: str | None,
        workspace_id: str | None,
        available_tool_definitions,
        tool_definitions_by_name: dict,
        background_job_id: str,
        background_allowed_tools: set[str],
        provider_tools: list,
        tool_loop_state,
        local_context: list,
        full_parts: list[str],
        generated_files: list,
        required_comfyui_tools: list,
        allow_side_effects: bool,
        tool_schema_tokens: int,
        estimated_context_tokens: int,
        request_started_at: float,
        attachment_image_paths,
        record_reasoning,
        apply_queued_steers,
        tool_is_allowed,
    ) -> tuple[bool, int, int, float, int, float]:
        completed = False
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_cost = 0.0
        first_token_reported = False
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
                full_parts[:] = [
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

            def status(
                phase: str,
                detail: str,
                *,
                elapsed_ms: int = 0,
                first: bool = False,
                _tokens: int = estimated_context_tokens,
                _tools=request_tools,
            ) -> dict:
                return self._provider_status_event(
                    name=name,
                    provider=provider,
                    phase=phase,
                    detail=detail,
                    local_context=local_context,
                    estimated_context_tokens=_tokens,
                    request_tools=_tools,
                    tool_schema_tokens=tool_schema_tokens,
                    tool_loop_state=tool_loop_state,
                    elapsed_ms=elapsed_ms,
                    first_token_ms=elapsed_ms if first else None,
                )

            if _provider_turn > 0:
                request_started_at = monotonic()
                first_token_reported = False
                estimated_context_tokens = sum(
                    estimate_tokens(message.content) + 8
                    for message in local_context
                )
                await queue.put(status("requesting", "工具结果已加入上下文，正在等待模型继续"))
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
                    elapsed_ms = round((monotonic() - request_started_at) * 1000)
                    await queue.put(status(
                        "tool",
                        "模型已开始构造工具调用",
                        elapsed_ms=elapsed_ms,
                        first=True,
                    ))
                if delta:
                    if not first_token_reported:
                        first_token_reported = True
                        elapsed_ms = round((monotonic() - request_started_at) * 1000)
                        await queue.put(status(
                            "streaming",
                            "已收到首个 token，正在生成回复",
                            elapsed_ms=elapsed_ms,
                            first=True,
                        ))
                    round_text += delta
                round_tool_calls.extend(chunk.get("tool_calls") or [])
                if round_tool_calls and not first_token_reported:
                    first_token_reported = True
                    elapsed_ms = round((monotonic() - request_started_at) * 1000)
                    await queue.put(status(
                        "tool",
                        "模型已返回结构化工具调用",
                        elapsed_ms=elapsed_ms,
                        first=True,
                    ))
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
                if await self._recover_textual_tool_call(
                    round_text=round_text,
                    requested_name=pseudo_match.group(1),
                    provider_tools=provider_tools,
                    available_tool_definitions=available_tool_definitions,
                    workspace_id=workspace_id,
                    allow_side_effects=allow_side_effects,
                    queue=queue,
                    name=name,
                    tool_loop_state=tool_loop_state,
                    local_context=local_context,
                ):
                    break
                continue

            if not round_tool_calls:
                final_text = round_text.strip()
                if final_text:
                    # Only the terminal provider turn is user-facing. Text
                    # emitted alongside tool requests is execution scratch
                    # space (for example, repeated "让我检查…" requests) and
                    # must stay in the model context instead of becoming
                    # chat history or a live chat bubble.
                    full_parts[:] = [final_text]
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
                full_parts[:] = [
                    invalid_call_message
                    + " 本轮无效调用均未执行；请重试，系统将要求模型使用分块工件后再一次性导出。"
                ]
                completed = True
                break
            if required_comfyui_tools:
                if tool_loop_state.required_tools_complete:
                    full_parts[:] = [
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
                    full_parts[:] = [
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

        return (
            completed,
            total_prompt_tokens,
            total_completion_tokens,
            total_cost,
            estimated_context_tokens,
            request_started_at,
        )
