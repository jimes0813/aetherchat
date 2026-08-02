import asyncio
import json
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

from ..models.schemas import ChatMessage, Message, ProviderConfig, ToolCall, ToolDefinition
from ..providers.base import LLMProvider, get_provider
from .agent_run_service import agent_run_service
from .approval_service import approval_policy, approval_service
from .chat_database import chat_database
from .computer_use_consent import conversation_computer_use_approval
from .context_builder import build_messages_for_provider, estimate_tokens
from .git_workspace_service import (
    WORKSPACE_TOOL_NAMES,
    GitWorkspaceError,
    git_workspace_service,
)
from .knowledge_service import KnowledgeError, knowledge_service
from .mcp_registry import mcp_registry
from .memory_service import memory_service
from .model_registry import model_registry
from .plugin_registry import plugin_registry
from .tool_registry import ToolInvoke, tool_registry

MAX_MODEL_TOOL_ROUNDS = 12
MAX_MODEL_TURNS = 32
MAX_PSEUDO_TOOL_RETRIES = 2
MAX_INVALID_FILE_TOOL_CALLS = 3
PSEUDO_TOOL_CALL_RE = re.compile(r"请求执行工具\s*[:：]\s*([A-Za-z0-9_]+)")
COMFYUI_STATUS_TOOL = "plugin__comfyui__status"
COMFYUI_IMAGE_TOOL = "plugin__comfyui__generate_image"
COMFYUI_VIDEO_TOOL = "plugin__comfyui__generate_video"
COMFYUI_WORKFLOW_TOOL = "plugin__comfyui__run_workflow"
BROWSER_OPEN_TOOL = "plugin__browser_links__open_url"
DESKTOP_WRITE_DOCUMENT_TOOL = "plugin__desktop_control__write_document"
FILE_WRITE_TOOLS = {
    "write_artifact",
    "plugin__documents__export_to_local",
    DESKTOP_WRITE_DOCUMENT_TOOL,
}
_COMPUTER_USE_LOCK = asyncio.Lock()

# Simple Facilitator for loop detection and summaries (PR10 starter)
class Facilitator:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.mode = "project"
        self.history: list[str] = []
        self.loop_count = 0

    def check_loop(self, content: str) -> bool:
        self.history.append(content[:100])
        if len(self.history) > 5:
            self.history.pop(0)
        # Simple repetition detection
        if len(set(self.history[-3:])) == 1:
            self.loop_count += 1
            return self.loop_count >= 2
        return False

    def get_summary(self) -> str:
        mode_note = f"当前模式: {self.mode if hasattr(self, 'mode') else 'project'}。"
        return f"{mode_note} 当前进度摘要（Facilitator）: 已处理 {len(self.history)} 条消息。建议检查任务状态并推进合并提案（需2票或人类确认）。"

    def inject(self, content: str) -> dict:
        return {
            "type": "facilitator_msg",
            "content": content,
            "ts": datetime.now(UTC).isoformat()
        }

DEFAULT_PROVIDERS = [
    ProviderConfig(name="claude", type="anthropic", model="claude-3-5-sonnet", persona="Architect/Reviewer", participation_policy="always", api_key_ref="ANTHROPIC_API_KEY"),
    ProviderConfig(name="deepseek", type="openai_compat", model="deepseek-chat", persona="Coder/Reasoner", participation_policy="always", api_key_ref="DEEPSEEK_API_KEY"),
    ProviderConfig(name="grok", type="openai_compat", model="grok-2", persona="Creative", participation_policy="always", api_key_ref="XAI_API_KEY"),
    ProviderConfig(name="qwen", type="openai_compat", model="qwen-plus", persona="Chinese/local + Critic", participation_policy="always", api_key_ref="QWEN_API_KEY"),
    ProviderConfig(name="kimi", type="openai_compat", model="kimi-k2", persona="Researcher", participation_policy="always", api_key_ref="KIMI_API_KEY"),
    ProviderConfig(name="gpt", type="openai_compat", model="gpt-4.1", persona="Implementer", participation_policy="always", api_key_ref="OPENAI_API_KEY"),
]

class SessionOrchestrator:
    def __init__(
        self,
        session_id: str,
        tenant_id: str = "demo",
        mode: str = "casual",
        desktop_action_handler: (
            Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None,
    ):
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.mode: str = mode  # casual | roundtable | project
        self.providers: dict[str, LLMProvider] = {}
        self.refresh_providers()
        self.budget_remaining = 10.0
        self.facilitator = Facilitator(session_id)
        self.conversation_history: list[ChatMessage] = []
        self._history_loaded = False
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._interrupt_all = False
        self._interrupted_models: set[str] = set()
        self._budget_lock = asyncio.Lock()
        self._budget_exhausted = False
        self._roundtable_cursor = 0
        self.run_service = agent_run_service
        self._active_run_id: str | None = None
        self._run_steers: list[dict] = []
        self._plan_cancelled = False
        self.desktop_action_handler = desktop_action_handler

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

    def record_steer_message(self, content: str) -> dict:
        message = Message(
            id=uuid4(),
            session_id=UUID(self.session_id),
            author_model="user",
            role="user",
            content=str(content).strip(),
            created_at=datetime.now(UTC),
        )
        self.conversation_history.append(ChatMessage(role="user", content=message.content))
        chat_database.save_message(message, mode=self.mode)
        memory_service.refresh(self.session_id, user_content=message.content)
        return message.model_dump(mode="json")

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

    @staticmethod
    def _requested_browser(content: str) -> str:
        if re.search(r"(?:\bfirefox\b|\bfierfox\b|火狐)", content, re.IGNORECASE):
            return "firefox"
        return "default"

    @staticmethod
    def _browser_execution_receipt(payload: dict[str, Any]) -> dict[str, Any] | None:
        plugin_envelope = payload.get("result")
        navigation = (
            plugin_envelope.get("result")
            if isinstance(plugin_envelope, dict)
            else None
        )
        receipt = (
            navigation.get("execution_receipt")
            if isinstance(navigation, dict)
            else None
        )
        if (
            isinstance(receipt, dict)
            and receipt.get("success") is True
            and receipt.get("verification") == "launcher_accepted"
        ):
            return receipt
        return None

    async def _complete_desktop_action(
        self,
        tool_name: str,
        plugin_result: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name != BROWSER_OPEN_TOOL:
            return plugin_result
        result = plugin_result.get("result")
        if not isinstance(result, dict) or result.get("kind") != "open_external":
            raise RuntimeError("外部导航插件没有返回可执行的桌面动作")
        if self.desktop_action_handler is None:
            raise RuntimeError(
                "外部导航未执行：当前没有活动的 AetherChat 桌面客户端可确认浏览器启动"
            )
        receipt = await self.desktop_action_handler({
            "kind": "open_external",
            "url": str(result.get("url") or ""),
            "browser": str(result.get("browser") or "default"),
        })
        if receipt.get("success") is not True:
            raise RuntimeError(
                str(receipt.get("error") or "桌面客户端未确认外部导航")
            )
        return {
            **plugin_result,
            "result": {
                **result,
                "execution_receipt": receipt,
            },
        }

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
        content = str(action.get("content") or "").strip()
        if not content and attachment_ids:
            content = "请阅读并分析我附带的资料。"
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
            chat_database.save_message(user_message, mode=self.mode)
            memory_service.refresh(self.session_id, user_content=content)
            yield {"type": "message_complete", "message": user_message.model_dump(mode="json")}

        # Facilitator check
        if self.facilitator.check_loop(content):
            yield self.facilitator.inject(self.facilitator.get_summary())

        queue: asyncio.Queue[dict] = asyncio.Queue()
        selected = [name for name in targets if self._should_participate(name, content, targets, explicit_targets)]
        if not selected:
            yield {
                "type": "error",
                "message": "没有选中可用模型。请选择具体模型，或选择全部模型后重试。",
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
        context_msgs = [message.model_copy(deep=True) for message in self.conversation_history]
        memory_context = memory_service.context(self.session_id)
        if memory_context:
            context_msgs.insert(0, ChatMessage(role="system", content=memory_context))
        if "upsert_memory_entity" in base_tool_names:
            context_msgs.insert(0, ChatMessage(
                role="system",
                content=(
                    "当用户确认了后续 Agent 会复用的稳定实体（角色、项目 URL、资源标识或关键事实）时，"
                    "使用 upsert_memory_entity 写入会话共享记忆，避免重复确认；不要把密码、令牌或其他秘密写入共享记忆。"
                ),
            ))
        if BROWSER_OPEN_TOOL in base_tool_names:
            context_msgs.insert(0, ChatMessage(
                role="system",
                content=(
                    "浏览器导航要求：只为用户明确要求的目标调用一次 open_url，不要先打开搜索页或无关站点。"
                    "用户指定 Firefox/火狐时把 browser 设为 firefox。工具只有在 execution_receipt.success=true "
                    "时才表示桌面启动器已接受打开请求；这不证明网页内容、登录状态或加载结果，未经电脑观察工具"
                    "核验时不得声称已看见页面内容。"
                ),
            ))
        if attachment_context:
            context_msgs.insert(0, ChatMessage(role="system", content=attachment_context))
        if project_knowledge_context:
            context_msgs.insert(0, ChatMessage(role="system", content=project_knowledge_context))
        language = str(action.get("language") or "").strip()
        if language == "中文优先":
            context_msgs.insert(0, ChatMessage(role="system", content="请优先使用清晰、准确的中文回复。"))
        elif language == "中英双语":
            context_msgs.insert(0, ChatMessage(role="system", content="请使用中英双语回复，先中文后英文。"))
        if "write_artifact" in base_tool_names:
            context_msgs.insert(0, ChatMessage(
                role="system",
                content=(
                    "文件写入要求：不得把长文全文塞进一次工具参数。超过约 1200 个汉字时，"
                    "使用 write_artifact 分块写入：首块 mode=overwrite、sequence=0，"
                    "后续每块 mode=append、sequence 依次递增，且每块不超过 1200 个汉字。"
                    "需要保存到本机时，分块完成后只调用一次 documents.export_to_local，"
                    "传工作区相对 path 与本机绝对 destination_path。"
                    "不要用 desktop_control.write_document 写工作区相对路径，也不要在失败后"
                    "原样重发同一长参数。"
                ),
            ))
        if any(name.startswith("plugin__desktop_control__") for name in base_tool_names):
            context_msgs.insert(0, ChatMessage(
                role="system",
                content=(
                    "你可以使用 AetherChat Computer Use 工具观察并操作本机。"
                    "先 list_windows/list_apps，再 get_state；优先引用 snapshot_id 与稳定 element_id，"
                    "也可用 element_text+control_type 模糊定位；临时 element_index 只作为兼容回退。"
                    "每次点击、输入、滚动或拖拽后必须重新 get_state 验证结果。"
                    "write_artifact 只写入当前会话工件库；run_command 在隔离的 /workspace 中执行，"
                    "不能访问或写入本机桌面。要在本机新建 TXT/MD/JSON/CSV/TSV 文件，"
                    "优先调用 documents 的 export_to_local，传入工作区相对 path 与本机绝对 destination_path；"
                    "只有短内容才可直接调用 desktop_control 的 write_document，且必须传绝对 path 和 data.text。"
                    "不要向用户输出‘请求执行工具’来代替调用；必须发送结构化工具调用。"
                    "用户在普通聊天文本中输入‘允许’不等于授权；真实授权必须由对话中的独立授权卡片完成。"
                    "不得猜测敏感数据，不得绕过本机授权对话框。"
                ),
            ))
        if any(name.startswith("plugin__comfyui__") for name in base_tool_names):
            context_msgs.insert(0, ChatMessage(
                role="system",
                content=(
                    "你可以与本机 ComfyUI 图像/视频模型协作。用户要求生成视觉内容时，"
                    "直接选择 generate_image、generate_video 或 run_workflow；generate_image 的 model "
                    "可省略以自动选择默认模型。只有用户询问环境状态或确实需要盘点资源时才调用 "
                    "comfyui.status；同一次请求中 status 成功后不得重复调用。"
                    "复杂工作流可先调用 workflow_schema 获取节点映射、"
                    "依赖与可覆盖输入，不要猜节点 ID。不要声称已经生成而不调用工具。"
                    "如果用户明确要求不要生成，只能调用 status，绝不能调用 generate_image、"
                    "generate_video 或 run_workflow。LTX 是否可运行必须严格读取 status 返回的 "
                    "generation.video_pipelines[].available 与 warning，不得根据 GPU/显存自行推断。"
                    "你负责把用户意图整理为包含主体、环境、构图、光线、风格及动作的完整提示词，"
                    "ComfyUI 负责生成。工具返回文件后必须明确报告实际 filename 或 URL，"
                    "并引用 metadata_artifact；该 JSON 已保存 prompt、模型、seed、尺寸和工作流来源。"
                ),
            ))
        if background_job_id:
            allowed_description = (
                "、".join(sorted(background_allowed_tools))
                if background_allowed_tools else "无"
            )
            context_msgs.insert(0, ChatMessage(
                role="system",
                content=(
                    "本次请求由 AetherChat 本机后台调度器触发。"
                    "无人值守执行必须遵守创建任务时固化的权限快照；"
                    f"允许的副作用工具白名单：{allowed_description}。"
                    "未列入白名单的副作用不得请求授权、不得尝试绕过，"
                    "只能输出分析结果并明确需要人工处理的后续动作。"
                ),
            ))

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
            pseudo_tool_retries = 0
            invalid_file_tool_calls = 0
            disabled_tool_names: set[str] = set()
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
                required_comfyui_index = 0
                required_comfyui_failed = False
                for required_name in required_comfyui_tools:
                    matching_results = [
                        message
                        for message in local_context
                        if message.role == "tool" and message.name == required_name
                    ]
                    if any(not message.tool_error for message in matching_results):
                        required_comfyui_index += 1
                        continue
                    if matching_results:
                        required_comfyui_failed = True
                    break
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
                    "phase": "requesting",
                    "detail": "上下文已整理，正在等待模型返回首个 token",
                    "context_messages": len(local_context),
                    "context_tokens": estimated_context_tokens,
                    "tool_count": len(provider_tools),
                    "tool_schema_tokens": tool_schema_tokens,
                    "round_index": tool_round,
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
                            next_tool_round=tool_round,
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
                    forced_tool_name = (
                        required_comfyui_tools[required_comfyui_index]
                        if (
                            not required_comfyui_failed
                            and required_comfyui_index < len(required_comfyui_tools)
                        )
                        else None
                    )
                    forced_tool = next(
                        (
                            tool for tool in provider_tools
                            if tool.name == forced_tool_name
                        ),
                        None,
                    )
                    if forced_tool_name and forced_tool is None:
                        required_comfyui_failed = True
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
                        else [
                            tool for tool in provider_tools
                            if tool.name not in disabled_tool_names
                        ]
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
                            "phase": "requesting",
                            "detail": "工具结果已加入上下文，正在等待模型继续",
                            "context_messages": len(local_context),
                            "context_tokens": estimated_context_tokens,
                            "tool_count": len(request_tools),
                            "tool_schema_tokens": tool_schema_tokens,
                            "round_index": tool_round,
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
                                "phase": "tool",
                                "detail": "模型已开始构造工具调用",
                                "context_messages": len(local_context),
                                "context_tokens": estimated_context_tokens,
                                "tool_count": len(request_tools),
                                "tool_schema_tokens": tool_schema_tokens,
                                "round_index": tool_round,
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
                                    "phase": "streaming",
                                    "detail": "已收到首个 token，正在生成回复",
                                    "context_messages": len(local_context),
                                    "context_tokens": estimated_context_tokens,
                                    "tool_count": len(request_tools),
                                    "tool_schema_tokens": tool_schema_tokens,
                                    "round_index": tool_round,
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
                                "phase": "tool",
                                "detail": "模型已返回结构化工具调用",
                                "context_messages": len(local_context),
                                "context_tokens": estimated_context_tokens,
                                "tool_count": len(request_tools),
                                "tool_schema_tokens": tool_schema_tokens,
                                "round_index": tool_round,
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
                        pseudo_tool_retries += 1
                        if pseudo_tool_retries > MAX_PSEUDO_TOOL_RETRIES:
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
                        pseudo_tool_retries += 1
                        if pseudo_tool_retries > MAX_PSEUDO_TOOL_RETRIES:
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
                    if tool_round >= MAX_MODEL_TOOL_ROUNDS:
                        await queue.put({
                            "type": "error",
                            "code": "tool_loop_limit",
                            "model": name,
                            "message": f"{name} 达到 {MAX_MODEL_TOOL_ROUNDS} 轮工具调用上限，已安全停止。",
                        })
                        break

                    # Older renderers may still have shown provider deltas before
                    # learning that this turn contains a tool call. Clear that
                    # draft defensively; current renderers receive no such deltas.
                    await queue.put({"type": "stream_reset", "model": name})
                    canonical_tool_calls = self._normalize_tool_calls(round_tool_calls)
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

                    # ruff B023 误报：本闭包只在当前迭代内被 await 消费（下方 617-814），
                    # tool_results/tool_call_records 每轮 549-550 重建、tool_round 自增在 856（晚于全部调用），
                    # 闭包从不在变量被改写后执行，无迟绑定问题。逐行 noqa 而非全局豁免，保留对其它闭包的检查。
                    async def remember_tool_result(
                        tool_call: ToolCall,
                        payload: dict,
                        images: list[str],
                        *,
                        tool_run_id: str | None = None,
                        replayed: bool = False,
                    ) -> None:
                        nonlocal invalid_file_tool_calls
                        tool_results.append((tool_call, payload, images))  # noqa: B023
                        result_status = str(payload.get("status") or "unknown")
                        if result_status == "success":
                            if tool_call.name in FILE_WRITE_TOOLS:
                                invalid_file_tool_calls = 0
                            if tool_call.name == COMFYUI_STATUS_TOOL:
                                # One successful inventory snapshot is enough
                                # for this turn. Removing the schema prevents
                                # local models from repeating the same large
                                # result until their context overflows.
                                disabled_tool_names.add(tool_call.name)
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
                        discovered = self._generated_files_from_tool_result(
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
                            "round_index": tool_round,  # noqa: B023
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
                        if tool_name == BROWSER_OPEN_TOOL:
                            arguments = {
                                **arguments,
                                "browser": str(
                                    arguments.get("browser")
                                    or self._requested_browser(content)
                                ),
                            }
                        argument_error = self._tool_argument_error(
                            tool_name,
                            arguments,
                            tool_definitions_by_name.get(tool_name),
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
                                round_index=tool_round,
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
                                "round_index": tool_round,
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
                                invalid_file_tool_calls += 1
                                if tool_name == DESKTOP_WRITE_DOCUMENT_TOOL:
                                    disabled_tool_names.add(tool_name)
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
                                required_comfyui_failed = True
                            run = tool_registry.record_run(
                                tool_id=None,
                                tool_name=tool_name or "unknown",
                                caller=f"model:{name}",
                                session_id=self.session_id,
                                arguments=self._sanitize_tool_arguments(tool_name, arguments),
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
                                if tool_name.startswith("plugin__desktop_control__"):
                                    async with _COMPUTER_USE_LOCK:
                                        with conversation_computer_use_approval():
                                            plugin_result = await plugin_registry.invoke_tool(
                                                tool_name, self.session_id, f"model:{name}", arguments
                                            )
                                else:
                                    plugin_result = await plugin_registry.invoke_tool(
                                        tool_name, self.session_id, f"model:{name}", arguments
                                    )
                                plugin_result = await self._complete_desktop_action(
                                    tool_name,
                                    plugin_result,
                                )
                                safe_result, images = self._sanitize_tool_result(plugin_result)
                                run = tool_registry.record_run(
                                    tool_id=None,
                                    tool_name=tool_name,
                                    caller=f"model:{name}",
                                    session_id=self.session_id,
                                    arguments=self._sanitize_tool_arguments(tool_name, arguments),
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
                                    required_comfyui_index += 1
                            except Exception as exc:
                                if tool_name == forced_tool_name:
                                    required_comfyui_failed = True
                                run = tool_registry.record_run(
                                    tool_id=None,
                                    tool_name=tool_name,
                                    caller=f"model:{name}",
                                    session_id=self.session_id,
                                    arguments=self._sanitize_tool_arguments(tool_name, arguments),
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
                                mcp_result = await mcp_registry.invoke_tool(tool_name, arguments)
                                safe_result, images = self._sanitize_tool_result(mcp_result)
                                is_error = bool(mcp_result.get("is_error"))
                                run = tool_registry.record_run(
                                    tool_id=None,
                                    tool_name=tool_name,
                                    caller=f"model:{name}",
                                    session_id=self.session_id,
                                    arguments=self._sanitize_tool_arguments(tool_name, arguments),
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
                                    arguments=self._sanitize_tool_arguments(tool_name, arguments),
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
                        run = await tool_registry.invoke_async(
                            tool["id"],
                            ToolInvoke(caller=f"model:{name}", session_id=self.session_id, arguments=arguments),
                            session_id=self.session_id,
                            execution_context={
                                "run_id": agent_run_id or None,
                                "plan_step_id": plan_step_id,
                                "workspace_id": workspace_id,
                            },
                        )
                        structured_output = (
                            run.get("result")
                            if "result" in run
                            else run.get("output")
                        )
                        safe_output, images = self._sanitize_tool_result(
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

                    for previous_message in local_context:
                        previous_message.images = []
                    remaining_result_chars = 180_000
                    remaining_image_slots = 4
                    for tool_call, tool_result, result_images in tool_results:
                        serialized = json.dumps(tool_result, ensure_ascii=False, default=str, indent=2)
                        if len(serialized) > remaining_result_chars:
                            if remaining_result_chars > 0:
                                serialized = (
                                    serialized[:remaining_result_chars]
                                    + "\n...[工具结果已截断]"
                                )
                            else:
                                serialized = "[工具结果因本轮总长度上限已省略]"
                        remaining_result_chars = max(0, remaining_result_chars - len(serialized))
                        attached_images = result_images[:remaining_image_slots]
                        remaining_image_slots -= len(attached_images)
                        local_context.append(ChatMessage(
                            role="tool",
                            name=tool_call.name,
                            tool_call_id=tool_call.id,
                            tool_error=tool_result.get("status") != "success",
                            content=(
                                f"第 {tool_round + 1} 轮工具 {tool_call.name} 的结果。"
                                "如执行了界面动作，请重新调用 get_state 验证，不要凭旧截图继续。\n"
                                f"{serialized}"
                            ),
                            images=attached_images,
                        ))
                    tool_round += 1
                    if agent_run_id:
                        self._save_provider_checkpoint(
                            agent_run_id,
                            name,
                            local_context,
                            next_tool_round=tool_round,
                            kind="tool_round",
                            plan_step_id=plan_step_id,
                        )
                    if invalid_file_tool_calls >= MAX_INVALID_FILE_TOOL_CALLS:
                        invalid_call_message = (
                            f"{name} 连续 {invalid_file_tool_calls} 次返回不完整或无效的"
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
                        if (
                            not required_comfyui_failed
                            and required_comfyui_index >= len(required_comfyui_tools)
                        ):
                            full_parts = [
                                self._comfyui_completion_content(generated_files)
                            ]
                            completed = True
                            break
                        if required_comfyui_failed:
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
                        "phase": "finalizing",
                        "detail": "模型生成结束，正在持久化消息与文件关联",
                        "context_messages": len(local_context),
                        "context_tokens": estimated_context_tokens,
                        "tool_count": len(provider_tools),
                        "tool_schema_tokens": tool_schema_tokens,
                        "round_index": tool_round,
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
                            next_tool_round=tool_round,
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
                        chat_database.save_message(message, mode=self.mode)
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
                        chat_database.save_message(message, mode=self.mode)
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

    @staticmethod
    def _plan_step_instruction(goal: dict, step: dict) -> str:
        goal_text = json.dumps({
            "objective": goal.get("objective"),
            "success_criteria": goal.get("success_criteria") or [],
            "constraints": goal.get("constraints") or [],
            "deliverable": goal.get("deliverable"),
        }, ensure_ascii=False, indent=2)
        base = (
            f"步骤：{step.get('title')}\n"
            f"类型：{step.get('kind')}\n"
            f"职责：{step.get('role')}\n"
            f"说明：{step.get('description')}\n"
            f"Goal Spec：\n{goal_text}\n"
        )
        if step.get("kind") == "worker":
            return (
                f"{base}\n"
                "独立完成你的候选结果，不假设其他 Worker 会弥补遗漏。"
                "只报告实际执行或验证过的事实；明确列出证据、风险与未验证项。"
            )
        if step.get("kind") == "verifier":
            return (
                f"{base}\n"
                "核验后只输出一个 JSON 对象，不要使用 Markdown 代码围栏。格式："
                '{"verdict":"pass|needs_revision|fail",'
                '"summary":"核验摘要","evidence":["可核对证据"],'
                '"issues":["矛盾、缺口或未验证项"]}。'
                "若没有足够证据，不能返回 pass。"
            )
        if step.get("kind") == "synthesizer":
            return (
                f"{base}\n"
                "生成唯一、可直接交付给用户的最终答案。不要展示内部 JSON、Plan Step "
                "编号或模型讨论过程；吸收 Verifier 指出的问题，并明确仍无法验证的部分。"
            )
        return base

    @staticmethod
    def _public_plan_step(step: dict) -> dict:
        return {**step, "output": None}

    @staticmethod
    def _plan_step_context(
        base_context: list[ChatMessage],
        goal: dict,
        step: dict,
        steps_by_key: dict[str, dict],
        steers: list[dict],
        reviews: list[dict],
        workspace_evidence: list[dict] | None = None,
    ) -> list[ChatMessage]:
        messages = [message.model_copy(deep=True) for message in base_context]
        if step.get("kind") == "worker" and int(step.get("attempt") or 0) > 1:
            previous_output = step.get("output")
            previous_content = (
                str(previous_output.get("content") or "")
                if isinstance(previous_output, dict)
                else ""
            )
            latest_review = reviews[-1] if reviews else {}
            issues = latest_review.get("issues")
            issue_lines = [
                str(issue)[:1000]
                for issue in issues[:20]
            ] if isinstance(issues, list) else []
            messages.append(ChatMessage(
                role="user",
                content=(
                    "【Verifier 触发的受控返工】\n"
                    f"上一版候选：\n{previous_content[:40_000] or '[无可复用候选]'}\n\n"
                    f"核验摘要：{str(latest_review.get('summary') or '需要修订')[:10_000]}\n"
                    + (
                        "待修问题：\n"
                        + "\n".join(f"- {issue}" for issue in issue_lines)
                        if issue_lines else "待修问题：核验未提供结构化问题，请重新检查成功条件。"
                    )
                    + "\n请针对问题生成新的完整候选，不要只描述修改过程。"
                ),
            ))
        dependencies = [
            steps_by_key[key]
            for key in step.get("dependencies") or []
            if key in steps_by_key
        ]
        if dependencies:
            blocks: list[str] = []
            remaining = 120_000
            for dependency in dependencies:
                output = dependency.get("output")
                content = (
                    str(output.get("content") or "")
                    if isinstance(output, dict)
                    else ""
                )
                content = content[:40_000]
                block = (
                    f"### {dependency.get('title')} "
                    f"[{dependency.get('kind')} / {dependency.get('status')}]\n"
                    f"模型：{dependency.get('assigned_model') or 'system'}\n"
                    f"{content or '[无可用输出]'}"
                )
                if dependency.get("error"):
                    block += f"\n错误：{dependency['error']}"
                if len(block) > remaining:
                    block = block[:remaining] + "\n...[依赖输出已截断]"
                remaining = max(0, remaining - len(block))
                blocks.append(block)
                if remaining == 0:
                    break
            messages.append(ChatMessage(
                role="user",
                content=(
                    "以下是已经持久化的上游步骤结果。只把其中可核对内容视为证据，"
                    "失败或空输出不得推断为成功：\n\n"
                    + "\n\n".join(blocks)
                ),
            ))
        if (
            workspace_evidence
            and step.get("kind") in {"verifier", "synthesizer"}
        ):
            messages.append(ChatMessage(
                role="user",
                content=(
                    "【不可由模型声明替代的 Git/测试证据】\n"
                    "以下记录由 AetherChat 在独立 worktree 上直接采集。"
                    "exit_code 非 0 不能视为通过；unverified 表示只有 diff、"
                    "没有执行确定性核验命令：\n"
                    + json.dumps(
                        workspace_evidence,
                        ensure_ascii=False,
                        indent=2,
                    )[:80_000]
                ),
            ))
        steer_texts = [
            str(item.get("content") or "").strip()
            for item in steers
            if str(item.get("content") or "").strip()
        ]
        if steer_texts:
            messages.append(ChatMessage(
                role="user",
                content=(
                    "【本 Run 最新转向记录】\n"
                    + "\n".join(f"- {text}" for text in steer_texts[-10:])
                    + "\n最终输出必须服从这些较新的指令。"
                ),
            ))
        return messages

    @staticmethod
    def _parse_plan_review(content: str) -> dict:
        normalized = str(content or "").strip()
        candidate = normalized
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", normalized, re.S)
        if fenced:
            candidate = fenced.group(1)
        else:
            start = normalized.find("{")
            end = normalized.rfind("}")
            if start >= 0 and end > start:
                candidate = normalized[start:end + 1]
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        verdict = str(parsed.get("verdict") or "unknown").lower()
        if verdict not in {"pass", "needs_revision", "fail"}:
            verdict = "unknown"
        evidence = parsed.get("evidence")
        issues = parsed.get("issues")
        return {
            "verdict": verdict,
            "summary": str(parsed.get("summary") or normalized or "无核验摘要")[:20_000],
            "evidence": evidence[:50] if isinstance(evidence, list) else [],
            "issues": issues[:50] if isinstance(issues, list) else [],
        }

    async def _authorize_model_tool(
        self,
        queue: asyncio.Queue[dict],
        model: str,
        tool_name: str,
        arguments: dict,
    ) -> tuple[bool, str]:
        policy = approval_policy(tool_name, arguments)
        if policy is None:
            return True, ""
        safe_arguments = dict(self._sanitize_tool_arguments(tool_name, arguments))
        for key in ("content", "api_key", "token", "password", "secret"):
            if key in safe_arguments:
                safe_arguments[key] = f"[已隐藏 {len(str(safe_arguments[key]))} 个字符]"
        pending = approval_service.create(
            session_id=self.session_id,
            model=model,
            tool_name=tool_name,
            arguments=safe_arguments,
            **policy,
        )
        if pending is None:
            return True, ""
        await queue.put({"type": "approval_required", "approval": pending.public()})
        decision = await approval_service.wait(pending.id)
        await queue.put({
            "type": "approval_resolved",
            "approval_id": pending.id,
            "decision": decision,
            "feedback": pending.feedback,
        })
        return decision in {"allow_once", "allow_session"}, pending.feedback

    @staticmethod
    def _comfyui_completion_content(generated_files: list[dict]) -> str:
        if not generated_files:
            return (
                "ComfyUI 已完成生成，但没有返回可关联的输出文件。"
                "请在协作日志中查看本次工具结果。"
            )
        lines = []
        for item in generated_files[:8]:
            name = str(item.get("name") or "generated-file").strip()
            location = str(item.get("path") or item.get("url") or "").strip()
            if location:
                lines.append(f"- {name}：`{location}`")
            else:
                lines.append(f"- {name}")
        return (
            "ComfyUI 已完成生成。文件已附在本条消息中，可直接预览或打开。\n\n"
            "文件位置：\n"
            + "\n".join(lines)
        )

    @staticmethod
    def _comfyui_intent_text(
        content: str,
        execution: dict | None = None,
    ) -> str:
        """Normalize ComfyUI intent while removing explicitly negated actions."""
        execution = execution or {}
        text = " ".join((
            str(content or ""),
            str(execution.get("instruction") or ""),
        )).casefold()
        text = re.sub(
            r"(?:不要|请勿|无需|不需要|禁止|别)\s*(?:再\s*)?"
            r"(?:重新生成|生成|绘制|制作|创作|渲染|画|运行|执行)"
            r"[^,，。！？；;\n]*",
            " ",
            text,
        )
        text = re.sub(
            r"(?:do\s+not|don't|dont|no\s+need\s+to|without)\s+"
            r"(?:generate|regenerate|create|draw|render|make|run|execute)"
            r"[^,，。！？；;\n]*",
            " ",
            text,
        )
        return text

    @staticmethod
    def _requested_comfyui_image_model(content: str) -> str | None:
        """Extract a checkpoint explicitly named by the user, if present."""
        text = str(content or "")
        exact = re.search(
            r"([a-z0-9_.-]+\.safetensors)",
            text,
            flags=re.IGNORECASE,
        )
        if exact:
            return exact.group(1)
        alias = re.search(
            r"(?:使用|用)\s*([a-z0-9_.-]{3,})\s*模型",
            text,
            flags=re.IGNORECASE,
        )
        return alias.group(1) if alias else None

    @staticmethod
    def _required_comfyui_tools(
        content: str,
        execution: dict | None = None,
    ) -> list[str]:
        """Return the mandatory ComfyUI sequence for explicit generation requests.

        Merely mentioning an existing image (for example “图片呢”) must not
        silently start a new GPU job. Chinese requests may put the deliverable
        before the verb ("图片生成") or name a checkpoint before describing the
        action ("使用 xxx 模型……进行图片").
        """

        # A mandatory side effect must come from the user's own request. Plan
        # step titles such as “生成单一最终交付” are orchestration language and
        # must never be reinterpreted as a request to generate visual media.
        text = SessionOrchestrator._comfyui_intent_text(content)
        visual_target = (
            r"(?:图片|图像|照片|摄影图|艺术照|肖像|插图|海报|封面|画面|"
            r"视频|动画|影片|短片|图)"
        )
        chinese_creation = re.search(
            rf"(?:重新生成|生成|绘制|制作|创作|渲染|画|出|做)"
            rf"[^,，。！？；;\n]{{0,32}}{visual_target}",
            text,
        )
        chinese_target_first_creation = re.search(
            rf"{visual_target}\s*(?:重新)?生成"
            rf"(?!\s*(?:的\s*)?(?:模型|能力|服务|资源|列表|清单|状态|数量|"
            rf"有哪些|都有什么))",
            text,
        )
        chinese_named_model_creation = re.search(
            rf"(?:使用|用)[^,，。！？；;\n]{{0,48}}"
            rf"(?:[a-z0-9_.-]+\.safetensors|[a-z0-9_.-]{{3,}}\s*模型)"
            rf"[^,，。！？；;\n]{{0,96}}"
            rf"(?:来\s*)?(?:进行|生成|制作|创作|渲染|出图|画)"
            rf"[^,，。！？；;\n]{{0,48}}{visual_target}",
            text,
        )
        english_creation = re.search(
            r"(?:generate|regenerate|create|draw|render|make)"
            r"[^,，。！？；;\n]{0,48}"
            r"(?:image|picture|photo|photograph|illustration|poster|cover|"
            r"video|movie|clip)",
            text,
        )
        if not any((
            chinese_creation,
            chinese_target_first_creation,
            chinese_named_model_creation,
            english_creation,
        )):
            return []

        if any(term in text for term in ("工作流", "workflow")) and any(
            term in text for term in ("运行", "执行", "run", "execute")
        ):
            action_tool = COMFYUI_WORKFLOW_TOOL
        elif any(term in text for term in ("视频", "动画", "video", "movie", "clip")):
            action_tool = COMFYUI_VIDEO_TOOL
        elif (
            any(term in text for term in (
                "图片", "图像", "生成图", "画图", "照片", "摄影", "艺术照", "肖像",
                "插图", "海报", "封面", "画面", "image", "picture", "photo",
                "photograph", "photography", "illustration", "poster", "cover",
            ))
            or re.search(
                r"(?:生成|重新生成|画|绘制|制作|创作|渲染)(?:一|这|那)?(?:张)?图",
                text,
            )
        ):
            action_tool = COMFYUI_IMAGE_TOOL
        else:
            return []
        return [action_tool]

    @staticmethod
    def _route_tools(
        content: str,
        definitions: list[ToolDefinition],
        execution: dict | None = None,
    ) -> list[ToolDefinition]:
        """Keep model prompts small while preserving tools relevant to the request."""

        execution = execution or {}
        text = " ".join((
            str(content or ""),
            str(execution.get("instruction") or ""),
        )).casefold()
        selected: set[str] = set()

        def contains(*terms: str) -> bool:
            return any(term.casefold() in text for term in terms)

        if execution.get("workspace_id"):
            selected.update(WORKSPACE_TOOL_NAMES)
        if execution.get("plan_step_id"):
            # Planned workers and verifiers need a cheap, read-only view of the
            # shared task board even when the user's wording does not literally
            # contain “task”. Side-effecting tools are still filtered by policy.
            selected.add("list_tasks")
        if contains("任务", "task", "待办", "进度", "依赖", "认领", "拆分"):
            selected.update({
                "list_tasks", "create_task", "claim_task", "subdivide_task",
                "get_progress_summary", "plugin__visualize__task_chart",
            })
        if contains(
            "文件", "工件", "artifact", "文档", "代码", "报告", "表格",
            "演示", "幻灯片", "pdf", "docx", "xlsx", "pptx", "csv",
            "保存", "写入", "修改",
        ):
            selected.update({
                "read_artifact", "write_artifact",
                "plugin__pdf__inventory",
                "plugin__documents__inventory",
                "plugin__documents__export_to_local",
                "plugin__presentations__inventory",
                "plugin__spreadsheets__inventory",
            })
        if contains("命令", "终端", "shell", "运行测试", "执行测试", "run command"):
            selected.add("run_command")
        if contains("合并", "merge", "提案", "投票"):
            selected.update({
                "create_merge_proposal", "propose_merge", "vote_on_proposal",
            })
        if contains(
            "comfyui", "comfy", "图片", "图像", "生成图", "画图",
            "视频", "工作流", "seed", "提示词",
        ):
            required_comfyui = SessionOrchestrator._required_comfyui_tools(
                content,
                execution,
            )
            positive_comfyui_text = SessionOrchestrator._comfyui_intent_text(
                content,
            )
            workflow_run = (
                any(term in positive_comfyui_text for term in ("工作流", "workflow"))
                and any(
                    term in positive_comfyui_text
                    for term in ("运行", "执行", "run", "execute")
                )
            )
            if required_comfyui:
                selected.update(required_comfyui)
            elif workflow_run:
                selected.add(COMFYUI_WORKFLOW_TOOL)
            else:
                # Resource questions and negated generation requests are
                # read-only. Do not expose generation tools for the model to
                # call accidentally.
                selected.add(COMFYUI_STATUS_TOOL)
            if contains("工作流", "workflow"):
                selected.add("plugin__comfyui__workflow_schema")
        if contains(
            "电脑", "桌面", "窗口", "鼠标", "键盘", "点击", "粘贴",
            "启动应用", "打开应用", "computer use", "截图",
        ):
            selected.update(
                definition.name
                for definition in definitions
                if definition.name.startswith("plugin__desktop_control__")
            )
        if contains("网页", "浏览器", "链接", "url", "http://", "https://"):
            selected.add("plugin__browser_links__open_url")
        if contains("github", "仓库状态"):
            selected.add("plugin__github_readonly__status")
        if contains("诊断", "健康检查", "workspace health"):
            selected.add("plugin__qa_diagnostics__workspace_health")
        if contains("记住", "记忆", "角色名", "关键实体", "以后使用", "共享实体"):
            selected.add("upsert_memory_entity")
        if contains("摘要", "总结", "summarize"):
            selected.add("summarize_cn")
        if contains("所有工具", "全部工具", "工具清单", "插件清单"):
            selected.update(definition.name for definition in definitions)
        if contains("mcp", "model context protocol"):
            selected.update(
                definition.name
                for definition in definitions
                if definition.name.startswith("mcp__")
            )
        if contains(
            "执行凭证", "运行证据", "真实操作", "操作记录", "工具调用记录",
            "执行审计",
        ):
            selected.update(
                definition.name
                for definition in definitions
                if definition.name.startswith("mcp__aetherchat_local_audit__")
            )

        return [
            definition for definition in definitions
            if definition.name in selected
        ]

    @staticmethod
    def _context_limits(
        provider: LLMProvider,
        tools: list[ToolDefinition],
    ) -> tuple[int, int, int]:
        output_reserve = max(256, int(provider.config.max_tokens or 2048))
        provider_limit = max(2048, int(provider.config.token_limit or 8192))
        if provider.config.type != "local":
            return provider_limit, output_reserve, 0
        try:
            active_limit = int(
                os.getenv("AETHERCHAT_LOCAL_ACTIVE_CONTEXT_TOKENS", "8192")
            )
        except ValueError:
            active_limit = 8192
        try:
            local_output = int(
                os.getenv("AETHERCHAT_LOCAL_MAX_OUTPUT_TOKENS", "2048")
            )
        except ValueError:
            local_output = 2048
        tool_tokens = estimate_tokens(json.dumps(
            [tool.model_dump(mode="json") for tool in tools],
            ensure_ascii=False,
            default=str,
        )) if tools else 0
        output_reserve = min(output_reserve, max(256, local_output))
        message_limit = max(
            3072,
            min(provider_limit, max(4096, active_limit)) - tool_tokens,
        )
        return message_limit, output_reserve, tool_tokens

    @staticmethod
    def _history_message_limit(provider: LLMProvider) -> int | None:
        if provider.config.type != "local":
            return None
        try:
            configured = int(
                os.getenv("AETHERCHAT_LOCAL_MAX_HISTORY_MESSAGES", "8")
            )
        except ValueError:
            configured = 8
        return max(4, min(64, configured))

    @staticmethod
    def _generated_files_from_tool_result(
        tool_name: str,
        value: object,
    ) -> list[dict]:
        results: list[dict] = []

        def append(raw: object) -> None:
            if not isinstance(raw, dict):
                return
            path = str(raw.get("path") or "").strip()
            url = str(raw.get("url") or "").strip()
            if not path and not url:
                return
            try:
                size_bytes = max(0, int(raw.get("size_bytes") or 0))
            except (TypeError, ValueError):
                size_bytes = 0
            results.append({
                "path": path or None,
                "url": url or None,
                "name": str(
                    raw.get("name")
                    or (path.rsplit("/", 1)[-1] if path else url.rsplit("/", 1)[-1])
                    or "generated-file"
                )[:240],
                "mime_type": str(raw.get("mime_type") or "application/octet-stream"),
                "size_bytes": size_bytes,
                "kind": str(raw.get("kind") or "artifact"),
                "source": str(raw.get("source") or tool_name),
            })

        def visit(item: object) -> None:
            if isinstance(item, list):
                for child in item:
                    visit(child)
                return
            if not isinstance(item, dict):
                return
            generated = item.get("generated_files")
            if isinstance(generated, list):
                for child in generated:
                    append(child)
            if tool_name == "write_artifact":
                append(item.get("artifact"))
            for child in item.values():
                if isinstance(child, (dict, list)):
                    visit(child)

        visit(value)
        unique: dict[str, dict] = {}
        for item in results:
            key = str(item.get("path") or item.get("url") or "")
            if key:
                unique[key] = item
        return list(unique.values())

    def _available_tools(self) -> list[ToolDefinition]:
        definitions = [
            ToolDefinition(
                name=tool["name"],
                description=tool.get("description") or tool["name"],
                parameters=tool.get("input_schema") or {"type": "object", "properties": {}},
            )
            for tool in tool_registry.list(self.session_id)
            if tool.get("enabled", True)
        ]
        definitions.extend(plugin_registry.tool_definitions())
        definitions.extend(mcp_registry.tool_definitions())
        unique: dict[str, ToolDefinition] = {}
        for definition in definitions:
            unique[definition.name] = definition
        return list(unique.values())

    @staticmethod
    def _sanitize_tool_result(value):
        images: list[str] = []

        def visit(item):
            if isinstance(item, dict):
                safe = {}
                for key, child in item.items():
                    if key in {"image_data_url", "dataUrl"} and isinstance(child, str) and child.startswith("data:image/"):
                        images.append(child)
                        safe[key] = "[image attached separately]"
                    else:
                        safe[key] = visit(child)
                return safe
            if isinstance(item, list):
                return [visit(child) for child in item]
            if isinstance(item, str) and item.startswith("data:image/") and ";base64," in item:
                images.append(item)
                return "[image attached separately]"
            return item

        return visit(value), images

    def _save_provider_checkpoint(
        self,
        run_id: str,
        model: str,
        messages: list[ChatMessage],
        *,
        next_tool_round: int,
        kind: str,
        plan_step_id: str | None = None,
    ) -> dict:
        serialized_messages = []
        for message in messages[-80:]:
            payload = message.model_dump(mode="json")
            payload["images"] = []
            if len(payload.get("content") or "") > 50_000:
                payload["content"] = (
                    str(payload["content"])[:50_000]
                    + "\n...[checkpoint content truncated]"
                )
            serialized_messages.append(payload)
        return self.run_service.save_checkpoint(
            run_id=run_id,
            session_id=self.session_id,
            model=model,
            kind=kind,
            plan_step_id=plan_step_id,
            state={
                "version": 1,
                "next_tool_round": max(0, int(next_tool_round)),
                "messages": serialized_messages,
            },
        )

    @staticmethod
    def _restore_checkpoint(checkpoint: dict | None) -> tuple[list[ChatMessage], int]:
        if not isinstance(checkpoint, dict):
            return [], 0
        state = checkpoint.get("state")
        if not isinstance(state, dict) or state.get("version") != 1:
            return [], 0
        restored: list[ChatMessage] = []
        for payload in state.get("messages") or []:
            try:
                restored.append(ChatMessage.model_validate(payload))
            except Exception:
                return [], 0
        if not restored:
            return [], 0
        try:
            next_tool_round = max(0, int(state.get("next_tool_round", 0)))
        except (TypeError, ValueError):
            next_tool_round = 0
        return restored, next_tool_round

    @staticmethod
    def _normalize_tool_calls(calls: Sequence[dict | ToolCall]) -> list[ToolCall]:
        normalized: list[ToolCall] = []
        seen_ids: set[str] = set()
        for raw_call in calls:
            if isinstance(raw_call, ToolCall):
                call_id = raw_call.id.strip()
                tool_name = raw_call.name.strip()
                arguments = raw_call.arguments
            else:
                call_id = str(raw_call.get("id") or "").strip()
                tool_name = str(raw_call.get("name") or "").strip()
                arguments = raw_call.get("arguments") or {}
            tool_name = tool_name or "unknown_tool"
            if not isinstance(arguments, dict):
                parsed_arguments = None
                if isinstance(arguments, str):
                    with suppress(json.JSONDecodeError):
                        parsed_arguments = json.loads(arguments)
                arguments = (
                    parsed_arguments
                    if isinstance(parsed_arguments, dict)
                    else {"_raw": arguments}
                )
            if not call_id or call_id in seen_ids:
                call_id = f"call_{uuid4().hex}"
            seen_ids.add(call_id)
            normalized.append(ToolCall(id=call_id, name=tool_name, arguments=arguments))
        return normalized

    @staticmethod
    def _tool_argument_error(
        tool_name: str,
        arguments: dict[str, Any],
        definition: ToolDefinition | None,
    ) -> str | None:
        if "_raw" in arguments:
            guidance = ""
            if tool_name in FILE_WRITE_TOOLS:
                guidance = (
                    " 不要原样重试同一份长内容；请用 write_artifact 分块，"
                    "每块不超过 1200 个汉字，首块 overwrite/sequence=0，"
                    "后续 append/sequence 递增，最后只导出一次。"
                )
            return (
                "工具参数 JSON 在模型输出结束前被截断或格式无效，工具未执行。"
                + guidance
            )

        if definition is not None:
            try:
                tool_registry.validate_arguments(
                    definition.parameters,
                    arguments,
                )
            except ValueError as exc:
                suffix = (
                    "；文件写入未执行，请补齐参数且不要重复发送完整长文"
                    if tool_name in FILE_WRITE_TOOLS
                    else ""
                )
                return f"{exc}{suffix}"

        if tool_name == DESKTOP_WRITE_DOCUMENT_TOOL:
            path_value = str(arguments.get("path") or "").strip()
            if path_value and not Path(path_value).expanduser().is_absolute():
                return (
                    "desktop_control.write_document 的 path 必须是本机绝对路径；"
                    "工作区相对路径请先用 write_artifact，再用 documents.export_to_local。"
                )
        if tool_name == "plugin__documents__export_to_local":
            destination = str(arguments.get("destination_path") or "").strip()
            if destination and not Path(destination).expanduser().is_absolute():
                return "documents.export_to_local 的 destination_path 必须是本机绝对路径"
        if tool_name == "write_artifact":
            mode = str(arguments.get("mode") or "overwrite")
            try:
                sequence = int(arguments.get("sequence", 0))
            except (TypeError, ValueError):
                return "write_artifact 的 sequence 必须是整数"
            if mode == "overwrite" and sequence != 0:
                return "write_artifact overwrite 模式要求 sequence=0"
            if mode == "append" and sequence < 1:
                return "write_artifact append 模式要求 sequence>=1"
        return None

    @staticmethod
    def _sanitize_tool_arguments(tool_name: str, arguments: dict) -> dict:
        safe = dict(arguments)
        if "_raw" in safe:
            safe["_raw"] = (
                "[malformed tool arguments omitted "
                f"chars={len(str(safe['_raw']))}]"
            )
        if not tool_name.startswith("plugin__desktop_control__"):
            return safe
        for key in ("text", "value"):
            if key in safe:
                safe[key] = f"[redacted {len(str(safe[key]))} characters]"
        if "data" in safe:
            safe["data"] = "[document payload redacted]"
        return safe

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

    def _calculate_cost(self, provider: LLMProvider, usage: dict) -> float:
        prompt_tokens = max(0, int(usage.get("prompt_tokens", 0) or 0))
        completion_tokens = max(0, int(usage.get("completion_tokens", 0) or 0))
        input_cost = prompt_tokens * max(0.0, provider.config.cost_per_1k_input) / 1000
        output_cost = completion_tokens * max(0.0, provider.config.cost_per_1k_output) / 1000
        return round(input_cost + output_cost, 6)

    @staticmethod
    def _provider_system_prompt(name: str, provider: LLMProvider) -> str:
        agent_name = " ".join(str(name or "agent").split())[:120]
        persona = " ".join(str(provider.config.persona or "").split())[:1200]
        parts = [
            "你是 AetherChat 多模型团队中的一个独立 Agent。",
            f"你的稳定标识是：{agent_name}。",
        ]
        if persona:
            parts.append(f"你的当前职责是：{persona}。请优先完成与该职责直接相关的工作。")
        parts.append(
            "只报告你实际完成或验证过的工作；不要声称其他 Agent 已经评审、执行或同意，"
            "除非上下文中有对应的可核对结果。"
        )
        return "\n".join(parts)

    def _parse_targets(self, content: str, default_targets: list[str]) -> list[str]:
        """PR6: Parse @mentions for targeting"""
        import re
        mentions = re.findall(r'@(\w+)', content)
        if not mentions:
            if "all" in default_targets:
                return list(self.providers.keys())
            return default_targets
        if "all" in mentions:
            return list(self.providers.keys())
        targets = [m for m in mentions if m in self.providers]
        return targets or default_targets

    def _should_participate(self, name: str, content: str, targets: list[str], explicit_targets: set[str] | None = None) -> bool:
        """Respect participation_policy and mode"""
        provider = self.providers.get(name)
        if not provider:
            return False
        policy = provider.config.participation_policy
        if name not in targets:
            return False
        if self.mode == "casual":
            return True
        if policy == "always":
            return True
        if policy == "mentioned":
            return name in (explicit_targets or set()) or "@" + name in content or "all" in content.lower()
        if policy == "project_autonomous" and self.mode == "project":
            if name in (explicit_targets or set()):
                return True
            # In project mode, autonomous for task-related
            return any(kw in content.lower() for kw in ["task", "claim", "write", "merge", "propose"])
        return False

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
