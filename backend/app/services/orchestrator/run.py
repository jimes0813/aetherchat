from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID, uuid4

from ...models.schemas import ChatMessage, Message, ToolCall
from ...providers.base import LLMProvider, get_provider
from ..approval_service import approval_policy
from ..chat_database import chat_database
from ..chat_references import resolve_chat_references
from ..context_builder import build_messages_for_provider, estimate_tokens
from ..git_workspace_service import (
    WORKSPACE_TOOL_NAMES,
    GitWorkspaceError,
    git_workspace_service,
)
from ..knowledge_service import KnowledgeError, knowledge_service
from ..mcp_registry import mcp_registry
from ..memory_service import memory_service
from ..model_registry import model_registry
from ..plugin_registry import plugin_registry
from ..skills_rules_service import skills_rules_service
from ..tool_loop import (
    COMFYUI_IMAGE_TOOL,
    COMFYUI_VIDEO_TOOL,
    FILE_WRITE_TOOLS,
)
from ..tool_registry import ToolInvoke, tool_registry
from ._constants import BROWSER_OPEN_TOOL, MAX_MODEL_TURNS, PSEUDO_TOOL_CALL_RE


class RunMixin:
    session_id: str
    project_id: str | None
    mode: str
    _active_run_id: str | None

    def begin_agent_run(self, run_id: str, steers: list[dict] | None = None) -> None:
        self._active_run_id = str(run_id)
        self._run_steers = [dict(item) for item in (steers or []) if isinstance(item, dict)]
        self._plan_cancelled = False

    def end_agent_run(self, run_id: str) -> None:
        if self._active_run_id == str(run_id):
            self._active_run_id = None
            self._run_steers = []
            self._plan_cancelled = False

    def queue_steer(self, steer: dict) -> None:
        if self._active_run_id != str(steer.get("run_id") or ""):
            raise ValueError("转向消息不属于当前运行")
        if any(item.get("id") == steer.get("id") for item in self._run_steers):
            return
        self._run_steers.append(dict(steer))

    def record_steer_message(self, content: str) -> tuple[Message, dict]:
        message = Message(
            id=uuid4(),
            session_id=UUID(self.session_id),
            author_model="user",
            role="user",
            content=str(content).strip(),
            created_at=datetime.now(UTC),
        )
        self.conversation_history.append(ChatMessage(role="user", content=message.content))
        return message, message.model_dump(mode="json")

    async def persist_steer_message(self, message: Message) -> None:
        await chat_database.run_in_thread(chat_database.save_message, message, mode=self.mode)
        memory_service.refresh(self.session_id, user_content=message.content)

    def _consume_steers(self, model: str) -> list[dict]:
        selected: list[dict] = []
        for steer in self._run_steers:
            targets = {str(value) for value in steer.get("targets") or []}
            applied = {str(value) for value in steer.get("applied_models") or []}
            if targets and "all" not in targets and model not in targets:
                continue
            if model in applied:
                continue
            applied.add(model)
            steer["applied_models"] = sorted(applied)
            selected.append(steer)
        return selected

    def _has_pending_steers(self, model: str) -> bool:
        for steer in self._run_steers:
            targets = {str(value) for value in steer.get("targets") or []}
            applied = {str(value) for value in steer.get("applied_models") or []}
            if targets and "all" not in targets and model not in targets:
                continue
            if model not in applied:
                return True
        return False

    async def stream_user_message(self, action: dict) -> AsyncIterator[dict]:
        self.refresh_providers()
        workspace_service = (
            self.run_service.workspace_service or git_workspace_service
        )
        agent_run_id = str(action.get("_agent_run_id") or self._active_run_id or "").strip()
        background_job_id = str(action.get("_background_job_id") or "").strip()
        background_allowed_tools = {
            str(value)
            for value in action.get("_background_allowed_tools") or []
            if str(value).strip()
        }
        pending_steers = action.get("_pending_steers") or []
        if agent_run_id and self._active_run_id is None:
            self.begin_agent_run(agent_run_id, pending_steers)
        elif agent_run_id and self._active_run_id == agent_run_id:
            for steer in pending_steers:
                if isinstance(steer, dict):
                    self.queue_steer(steer)
        requested_mode = action.get("mode")
        if isinstance(requested_mode, str):
            self.mode = requested_mode
            self.facilitator.mode = self.mode
        raw_attachment_ids = action.get("attachment_ids") or []
        if not isinstance(raw_attachment_ids, list) or len(raw_attachment_ids) > 10:
            yield {"type": "error", "code": "invalid_attachments", "message": "单条消息最多附带 10 个有效附件。"}
            return
        attachment_ids = [str(value) for value in raw_attachment_ids]
        referenced = resolve_chat_references(
            session_id=self.session_id,
            user_content=str(action.get("content") or "").strip(),
            references=action.get("references"),
            attachment_ids=attachment_ids,
        )
        attachment_ids = referenced["attachment_ids"]
        reference_context = str(referenced.get("context") or "")
        content = str(action.get("content") or "").strip()
        if not content and attachment_ids:
            content = "请阅读并分析我附带的资料。"
        if not content and reference_context:
            content = "请根据我引用的内容理解意图并继续。"
        if not content:
            yield {"type": "error", "code": "empty_message", "message": "消息内容不能为空。"}
            return
        try:
            attachment_refs = knowledge_service.attachment_refs(self.session_id, attachment_ids)
            attachment_context = knowledge_service.context_for_attachments(
                self.session_id, attachment_ids, content
            )
            project_knowledge_context = (
                ""
                if attachment_ids
                else knowledge_service.context_for_query(self.session_id, content)
            )
            attachment_images = knowledge_service.image_data_urls(self.session_id, attachment_ids)
            attachment_image_paths = knowledge_service.image_paths(
                self.session_id,
                attachment_ids,
            )
            for extra_path in referenced.get("image_paths") or []:
                if extra_path and extra_path not in attachment_image_paths:
                    attachment_image_paths.append(str(extra_path))
        except (KeyError, KnowledgeError, ValueError) as exc:
            message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
            yield {"type": "error", "code": "invalid_attachments", "message": str(message)}
            return
        if not self._history_loaded:
            self.conversation_history = [
                ChatMessage(
                    role=row["role"],
                    content=row["content"],
                    name=row["author_model"] if row["role"] == "assistant" else None,
                )
                for row in chat_database.chat_history(self.session_id)
                if row["role"] in {"system", "user", "assistant", "tool"}
            ]
            self._history_loaded = True
        raw_targets = action.get("targets") or list(self.providers.keys())
        explicit_targets = {target for target in raw_targets if target != "all"}
        targets = self._parse_targets(content, raw_targets)

        # Mode-aware filtering
        if self.mode == "roundtable" and targets:
            index = self._roundtable_cursor % len(targets)
            targets = [targets[index]]
            self._roundtable_cursor += 1

        if not action.get("_aether_retry"):
            user_message = Message(
                id=uuid4(),
                session_id=UUID(self.session_id),
                author_model="user",
                role="user",
                content=content,
                attachments=attachment_refs,
                created_at=datetime.now(UTC),
            )
            self.conversation_history.append(ChatMessage(role="user", content=content, images=attachment_images))
            await chat_database.run_in_thread(chat_database.save_message, user_message, mode=self.mode)
            memory_service.refresh(self.session_id, user_content=content)
            yield {"type": "message_complete", "message": user_message.model_dump(mode="json")}

        # Facilitator check
        if self.facilitator.check_loop(content):
            yield self.facilitator.inject(self.facilitator.get_summary())

        queue: asyncio.Queue[dict] = asyncio.Queue()
        selected = [name for name in targets if self._should_participate(name, content, targets, explicit_targets)]
        if selected:
            from ..platform_governance import platform_governance

            selected = platform_governance.filter_model_names(
                selected,
                project_id=self.project_id,
                session_id=self.session_id,
            )
        if not selected:
            yield {
                "type": "error",
                "message": "没有选中可用模型。请选择具体模型，或选择全部模型后重试。若已开启模型白名单，请在「治理」面板检查策略。",
            }
            return
        if self.budget_remaining <= 0:
            self._budget_exhausted = True
            yield {
                "type": "error",
                "code": "budget_exceeded",
                "message": "会话预算已用尽，请增加预算后再调用模型。",
            }
            return
        available_tool_definitions = self._available_tools()
        tool_definitions_by_name = {
            definition.name: definition
            for definition in available_tool_definitions
        }
        base_routed_tools = self._route_tools(
            content,
            available_tool_definitions,
            action,
        )
        base_tool_names = {tool.name for tool in base_routed_tools}
        planned_execution = bool(
            agent_run_id
            and self.mode == "project"
            and str(action.get("execution_strategy") or "direct") == "planned"
        )
        plan_bundle: dict | None = None
        if planned_execution:
            plan_bundle = self.run_service.ensure_plan(
                run_id=agent_run_id,
                session_id=self.session_id,
                action=action,
                models=selected,
                profiles={
                    name: str(self.providers[name].config.persona or "")
                    for name in selected
                },
            )
            yield {
                "type": "plan_created",
                "run_id": agent_run_id,
                "goal": plan_bundle["goal"],
                "plan_steps": plan_bundle["steps"],
            }
            current_plan_run = self.run_service.database.get_agent_run(
                agent_run_id,
                include_events=False,
            ) or {}
            if (
                current_plan_run.get("plan_status") == "draft"
                and not action.get("_plan_confirmed")
            ):
                waiting_run = self.run_service.database.update_agent_run(
                    agent_run_id,
                    "waiting_plan_confirmation",
                    error=None,
                )
                self.run_service.database.append_agent_event(
                    agent_run_id,
                    "plan_confirmation_required",
                    {
                        "revision": current_plan_run.get("plan_revision"),
                        "step_count": len(plan_bundle["steps"]),
                    },
                )
                yield {
                    "type": "plan_confirmation_required",
                    "run_id": agent_run_id,
                    "revision": current_plan_run.get("plan_revision"),
                    "agent_run": waiting_run,
                }
                return
            self.run_service.begin_plan_execution(agent_run_id)
        instruction_bundle = skills_rules_service.resolve_for_session(
            self.session_id,
            user_message=str(content or ""),
        )
        context_msgs = self.context_assembler.assemble(
            self.conversation_history,
            memory_context=memory_service.context(self.session_id),
            tool_names=base_tool_names,
            attachment_context=attachment_context,
            reference_context=reference_context,
            project_knowledge_context=project_knowledge_context,
            instruction_context=str(instruction_bundle.get("context") or ""),
            language=str(action.get("language") or "").strip(),
            background_job_id=background_job_id,
            background_allowed_tools=background_allowed_tools,
        )

        async def run_provider(
            name: str,
            provider: LLMProvider,
            execution: dict | None = None,
        ):
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

                    tool_results: list[tuple[ToolCall, dict, list[str]]] = []
                    tool_call_records: dict[str, dict] = {}

                    # ruff B023 误报：本闭包只在当前迭代内被 await 消费，
                    # tool_results/tool_call_records 每轮重建，ToolLoop 轮次在全部调用结束后才自增，
                    # 闭包从不在变量被改写后执行，无迟绑定问题。逐行 noqa 而非全局豁免，保留对其它闭包的检查。
                    async def remember_tool_result(
                        tool_call: ToolCall,
                        payload: dict,
                        images: list[str],
                        *,
                        tool_run_id: str | None = None,
                        replayed: bool = False,
                    ) -> None:
                        tool_results.append((tool_call, payload, images))  # noqa: B023
                        result_status = str(payload.get("status") or "unknown")
                        tool_loop_state.record_tool_result(
                            tool_call.name,
                            result_status,
                        )
                        if result_status == "success":
                            result_detail = "执行成功，正在依据真实结果继续处理。"
                            if tool_call.name == BROWSER_OPEN_TOOL:
                                receipt = self._browser_execution_receipt(payload)
                                if receipt is not None:
                                    browser = str(receipt.get("browser") or "default")
                                    url = str(receipt.get("url") or "")[:500]
                                    result_detail = (
                                        f"桌面启动器已确认由 {browser} 接受打开 {url}；"
                                        "该凭证不等同于网页内容已加载。"
                                    )
                        else:
                            result_detail = "执行未成功，正在依据错误信息调整后续答复。"
                        await record_reasoning(
                            "result",
                            f"工具 {tool_call.name} {result_detail}",
                        )
                        discovered = self.tool_loop.generated_files_from_result(
                            tool_call.name,
                            payload,
                        )
                        known = {
                            str(item.get("path") or item.get("url") or "")
                            for item in generated_files
                        }
                        additions = [
                            item for item in discovered
                            if str(item.get("path") or item.get("url") or "") not in known
                        ]
                        if additions:
                            generated_files.extend(additions)
                            await queue.put({
                                "type": "artifact_update",
                                "model": name,
                                "generated_files": additions,
                            })
                        record = tool_call_records.get(tool_call.id)  # noqa: B023
                        if record is not None and not replayed:
                            updated = self.run_service.finish_tool_call(
                                record["id"],
                                payload,
                                tool_run_id=tool_run_id,
                            )
                            if updated is not None:
                                tool_call_records[tool_call.id] = updated  # noqa: B023
                        await queue.put({
                            "type": "tool_result_replayed" if replayed else "tool_result",
                            "model": name,
                            "tool_call_id": tool_call.id,
                            "tool_name": tool_call.name,
                            "round_index": tool_loop_state.tool_round,  # noqa: B023
                            "status": payload.get("status"),
                            "tool_run_id": tool_run_id,
                            "idempotency_key": (
                                tool_call_records.get(tool_call.id, {}).get("idempotency_key")  # noqa: B023
                            ),
                        })

                    for call_index, tool_call in enumerate(canonical_tool_calls):
                        tool_name = tool_call.name
                        arguments = tool_call.arguments
                        if tool_name == COMFYUI_IMAGE_TOOL:
                            user_requested_model = self._requested_comfyui_image_model(
                                content,
                            )
                            if user_requested_model:
                                # Keep the checkpoint named by the user
                                # authoritative even when a local model omits or
                                # rewrites the optional `model` tool argument.
                                arguments = {
                                    **arguments,
                                    "_user_requested_model": user_requested_model,
                                }
                        if tool_name == COMFYUI_VIDEO_TOOL:
                            user_requested_model = self._requested_comfyui_video_model(
                                content,
                            )
                            arguments = {
                                **arguments,
                                **(
                                    {"_user_requested_model": user_requested_model}
                                    if user_requested_model
                                    else {}
                                ),
                                # A current-message attachment is a stronger,
                                # server-verified signal than any path guessed
                                # by the model.  Prefer it unconditionally.
                                **(
                                    {"input_image": attachment_image_paths[0]}
                                    if attachment_image_paths
                                    else {}
                                ),
                            }
                        if tool_name == BROWSER_OPEN_TOOL:
                            arguments = {
                                **arguments,
                                "browser": str(
                                    arguments.get("browser")
                                    or self._requested_browser(content)
                                ),
                            }
                        argument_error = self.tool_loop.argument_error(
                            tool_name,
                            arguments,
                            tool_definitions_by_name.get(tool_name),
                            validate_arguments=tool_registry.validate_arguments,
                        )
                        is_file_write = tool_name in FILE_WRITE_TOOLS
                        if agent_run_id:
                            call_record = self.run_service.begin_tool_call(
                                run_id=agent_run_id,
                                session_id=self.session_id,
                                model=name,
                                provider_type=provider.config.type,
                                provider_model=provider.config.model,
                                provider_call_id=tool_call.id,
                                tool_name=tool_name,
                                arguments=arguments,
                                round_index=tool_loop_state.tool_round,
                                call_index=call_index,
                                plan_step_id=plan_step_id,
                                dedupe_across_rounds=(
                                    argument_error is None
                                    if is_file_write
                                    else None
                                ),
                            )
                            tool_call_records[tool_call.id] = call_record
                            await queue.put({
                                "type": "tool_call",
                                "model": name,
                                "tool_call_id": tool_call.id,
                                "tool_name": tool_name,
                                "round_index": tool_loop_state.tool_round,
                                "call_index": call_index,
                                "idempotency_key": call_record["idempotency_key"],
                                "status": call_record["status"],
                            })
                            if call_record["disposition"] == "replay":
                                replay_payload = call_record.get("result") or {
                                    "tool": tool_name,
                                    "status": "error",
                                    "error": "已完成工具调用缺少可恢复结果。",
                                }
                                if (
                                    tool_name == BROWSER_OPEN_TOOL
                                    and str(replay_payload.get("status") or "") == "success"
                                    and self._browser_execution_receipt(replay_payload) is None
                                ):
                                    replay_payload = {
                                        "tool": tool_name,
                                        "status": "error",
                                        "error": (
                                            "历史外部导航记录缺少桌面启动凭证，"
                                            "不能重放为成功；请发起新的浏览器操作。"
                                        ),
                                    }
                                await remember_tool_result(
                                    tool_call,
                                    replay_payload,
                                    [],
                                    tool_run_id=call_record.get("tool_run_id"),
                                    replayed=True,
                                )
                                continue
                            if call_record["disposition"] == "uncertain":
                                await remember_tool_result(
                                    tool_call,
                                    {
                                        "tool": tool_name,
                                        "status": "error",
                                        "error": call_record.get("error"),
                                        "requires_human_review": True,
                                    },
                                    [],
                                    replayed=True,
                                )
                                await queue.put({
                                    "type": "error",
                                    "code": "tool_result_uncertain",
                                    "model": name,
                                    "message": call_record.get("error"),
                                })
                                continue
                        if argument_error is not None:
                            if is_file_write:
                                tool_loop_state.record_invalid_file_arguments(
                                    tool_name
                                )
                            await remember_tool_result(
                                tool_call,
                                {
                                    "tool": tool_name,
                                    "status": "error",
                                    "error": argument_error,
                                    "executed": False,
                                },
                                [],
                            )
                            continue
                        if not tool_is_allowed(tool_name):
                            await remember_tool_result(
                                tool_call,
                                {
                                    "tool": tool_name,
                                    "status": "denied",
                                    "error": (
                                        (
                                            "后台权限快照不允许该副作用工具；"
                                            "操作已在调用前拒绝。"
                                        )
                                        if background_job_id
                                        else (
                                            "当前 Plan Step 为只读 Worker/Verifier；"
                                            "副作用操作只能由 Synthesizer 在独立授权后执行。"
                                        )
                                    ),
                                },
                                [],
                            )
                            continue
                        if (
                            agent_run_id
                            and approval_policy(tool_name, arguments) is not None
                        ):
                            self.run_service.update_tool_call_status(
                                call_record["id"],
                                "waiting_approval",
                            )
                        background_preapproved = (
                            bool(background_job_id)
                            and tool_name in background_allowed_tools
                            and tool_is_allowed(tool_name)
                        )
                        approval_feedback = ""
                        if background_preapproved:
                            approved = True
                        else:
                            approved, approval_feedback = await self._authorize_model_tool(
                                queue,
                                name,
                                tool_name,
                                arguments,
                            )
                        if not approved:
                            started_at = datetime.now(UTC).isoformat()
                            error = "用户拒绝了本次工具操作。"
                            if approval_feedback:
                                error = f"{error} 用户要求：{approval_feedback}"
                            if tool_name == forced_tool_name:
                                tool_loop_state.mark_required_failed(tool_name)
                            run = tool_registry.record_run(
                                tool_id=None,
                                tool_name=tool_name or "unknown",
                                caller=f"model:{name}",
                                session_id=self.session_id,
                                arguments=self.tool_loop.sanitize_arguments(
                                    tool_name,
                                    arguments,
                                ),
                                status="error",
                                output=None,
                                error=error,
                                started_at=started_at,
                            )
                            await remember_tool_result(
                                tool_call,
                                {"tool": tool_name, "status": "denied", "error": error},
                                [],
                                tool_run_id=run.get("id"),
                            )
                            await queue.put({"type": "tool_run", "run": run})
                            continue
                        active_call_record = tool_call_records.get(tool_call.id)
                        if active_call_record is not None:
                            self.run_service.update_tool_call_status(active_call_record["id"], "running")
                        plugin_match = plugin_registry.resolve_tool_name(tool_name)
                        if plugin_match is not None:
                            started_at = datetime.now(UTC).isoformat()
                            try:
                                plugin_result = await self._invoke_plugin_tool(
                                    tool_name,
                                    f"model:{name}",
                                    arguments,
                                )
                                plugin_result = await self._complete_desktop_action(
                                    tool_name,
                                    plugin_result,
                                )
                                safe_result, images = self.tool_loop.sanitize_result(
                                    plugin_result
                                )
                                run = tool_registry.record_run(
                                    tool_id=None,
                                    tool_name=tool_name,
                                    caller=f"model:{name}",
                                    session_id=self.session_id,
                                    arguments=self.tool_loop.sanitize_arguments(
                                        tool_name,
                                        arguments,
                                    ),
                                    status="success",
                                    output=safe_result,
                                    error=None,
                                    started_at=started_at,
                                )
                                await remember_tool_result(
                                    tool_call,
                                    {"tool": tool_name, "status": "success", "result": safe_result},
                                    images,
                                    tool_run_id=run.get("id"),
                                )
                                if tool_name == forced_tool_name:
                                    tool_loop_state.mark_required_succeeded(
                                        tool_name
                                    )
                            except Exception as exc:
                                if tool_name == forced_tool_name:
                                    tool_loop_state.mark_required_failed(tool_name)
                                run = tool_registry.record_run(
                                    tool_id=None,
                                    tool_name=tool_name,
                                    caller=f"model:{name}",
                                    session_id=self.session_id,
                                    arguments=self.tool_loop.sanitize_arguments(
                                        tool_name,
                                        arguments,
                                    ),
                                    status="error",
                                    output=None,
                                    error=str(exc),
                                    started_at=started_at,
                                )
                                await remember_tool_result(
                                    tool_call,
                                    {"tool": tool_name, "status": "error", "error": str(exc)},
                                    [],
                                    tool_run_id=run.get("id"),
                                )
                            await queue.put({"type": "tool_run", "run": run})
                            continue

                        mcp_match = mcp_registry.resolve_tool_name(tool_name)
                        if mcp_match is not None:
                            started_at = datetime.now(UTC).isoformat()
                            try:
                                mcp_result = await mcp_registry.invoke_tool(
                                    tool_name,
                                    arguments,
                                    session_id=self.session_id,
                                    project_id=self.project_id,
                                )
                                safe_result, images = self.tool_loop.sanitize_result(
                                    mcp_result
                                )
                                is_error = bool(mcp_result.get("is_error"))
                                run = tool_registry.record_run(
                                    tool_id=None,
                                    tool_name=tool_name,
                                    caller=f"model:{name}",
                                    session_id=self.session_id,
                                    arguments=self.tool_loop.sanitize_arguments(
                                        tool_name,
                                        arguments,
                                    ),
                                    status="error" if is_error else "success",
                                    output=safe_result,
                                    error="MCP 工具返回错误" if is_error else None,
                                    started_at=started_at,
                                )
                                await remember_tool_result(
                                    tool_call,
                                    {
                                        "tool": tool_name,
                                        "status": "error" if is_error else "success",
                                        "result": safe_result,
                                    },
                                    images,
                                    tool_run_id=run.get("id"),
                                )
                            except Exception as exc:
                                run = tool_registry.record_run(
                                    tool_id=None,
                                    tool_name=tool_name,
                                    caller=f"model:{name}",
                                    session_id=self.session_id,
                                    arguments=self.tool_loop.sanitize_arguments(
                                        tool_name,
                                        arguments,
                                    ),
                                    status="error",
                                    output=None,
                                    error=str(exc),
                                    started_at=started_at,
                                )
                                await remember_tool_result(
                                    tool_call,
                                    {"tool": tool_name, "status": "error", "error": str(exc)},
                                    [],
                                    tool_run_id=run.get("id"),
                                )
                            await queue.put({"type": "tool_run", "run": run})
                            continue

                        tool = tool_registry.find_by_name(tool_name, self.session_id)
                        if tool is None:
                            error = f"模型请求了未注册工具：{tool_name}"
                            await queue.put({"type": "error", "code": "unknown_tool", "model": name, "message": error})
                            await remember_tool_result(
                                tool_call,
                                {"tool": tool_name, "status": "error", "error": error},
                                [],
                            )
                            continue
                        try:
                            run = await tool_registry.invoke_async(
                                tool["id"],
                                ToolInvoke(caller=f"model:{name}", session_id=self.session_id, arguments=arguments),
                                session_id=self.session_id,
                                execution_context={
                                    "run_id": agent_run_id or None,
                                    "plan_step_id": plan_step_id,
                                    "workspace_id": workspace_id,
                                    "project_id": self.project_id,
                                },
                            )
                        except PermissionError as exc:
                            started_at = datetime.now(UTC).isoformat()
                            run = tool_registry.record_run(
                                tool_id=tool.get("id"),
                                tool_name=tool_name,
                                caller=f"model:{name}",
                                session_id=self.session_id,
                                arguments=self.tool_loop.sanitize_arguments(
                                    tool_name,
                                    arguments,
                                ),
                                status="error",
                                output=None,
                                error=str(exc),
                                started_at=started_at,
                            )
                            await remember_tool_result(
                                tool_call,
                                {
                                    "tool": tool_name,
                                    "status": "denied",
                                    "error": str(exc),
                                },
                                [],
                                tool_run_id=run.get("id"),
                            )
                            await queue.put({"type": "tool_run", "run": run})
                            continue
                        structured_output = (
                            run.get("result")
                            if "result" in run
                            else run.get("output")
                        )
                        safe_output, images = self.tool_loop.sanitize_result(
                            structured_output
                        )
                        await remember_tool_result(
                            tool_call,
                            {
                                "tool": tool_name,
                                "status": run.get("status", "error"),
                                "result": safe_output,
                                "error": run.get("error"),
                            },
                            images,
                            tool_run_id=run.get("id"),
                        )
                        await queue.put({"type": "tool_run", "run": {**run, "output": safe_output}})

                    self.tool_loop.append_results_to_context(
                        tool_loop_state,
                        local_context,
                        tool_results,
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

        async def stream_batch(
            executions: list[dict],
            outcomes: dict[str, dict],
        ) -> AsyncIterator[dict]:
            step_by_model = {
                str(item["model"]): item
                for item in executions
            }
            tasks = [
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
            tasks = [
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

    async def broadcast_user_message(self, action: dict):
        async for _ in self.stream_user_message(action):
            pass

    def interrupt(self, scope: str = "all"):
        """PR6: Interrupt support"""
        if scope == "all":
            self._plan_cancelled = True
            self._interrupt_all = True
            for task in list(self._running_tasks.values()):
                task.cancel()
        else:
            self._interrupted_models.add(scope)
            if scope in self._running_tasks:
                self._running_tasks[scope].cancel()
        asyncio.create_task(self._reset_interrupt(scope))

    async def _reset_interrupt(self, scope: str):
        await asyncio.sleep(0.1)
        if scope == "all":
            self._interrupt_all = False
        else:
            self._interrupted_models.discard(scope)

    def _is_interrupted(self, name: str) -> bool:
        return self._interrupt_all or name in self._interrupted_models

    def _cancel_running_providers(self, exclude: str | None = None):
        for name, task in list(self._running_tasks.items()):
            if name != exclude and not task.done():
                task.cancel()

    def refresh_providers(self):
        self.providers = {
            config.name: get_provider(config)
            for config in model_registry.configs(available_only=True)
        }

    async def start_model_streams(self, prompt: str):
        # 并行启动多个模型
        tasks = []
        for prov in self.providers.values():
            tasks.append(self._run_one(prov, prompt))
        await asyncio.gather(*tasks)

    async def _run_one(self, provider, prompt):
        async for _ in provider.stream_chat([ChatMessage(role="user", content=prompt)]):
            pass
