from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from typing import Any

from ...models.schemas import ToolCall, ToolDefinition
from ..approval_service import approval_policy, approval_service
from ..git_workspace_service import (
    WORKSPACE_TOOL_NAMES,
)
from ..mcp_registry import mcp_registry
from ..plugin_registry import plugin_registry
from ..tool_loop import (
    COMFYUI_IMAGE_TOOL,
    COMFYUI_STATUS_TOOL,
    COMFYUI_VIDEO_TOOL,
    COMFYUI_WORKFLOW_TOOL,
    tool_loop,
)
from ..tool_registry import tool_registry
from .receipts import ReceiptsMixin


class ToolsMixin:
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
        safe_arguments = dict(
            self.tool_loop.sanitize_arguments(tool_name, arguments)
        )
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
        text = ReceiptsMixin._comfyui_intent_text(content)
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
            required_comfyui = ToolsMixin._required_comfyui_tools(
                content,
                execution,
            )
            positive_comfyui_text = ReceiptsMixin._comfyui_intent_text(
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
        return tool_loop.sanitize_result(value)

    @staticmethod
    def _normalize_tool_calls(calls: Sequence[dict | ToolCall]) -> list[ToolCall]:
        return tool_loop.normalize_tool_calls(calls)

    @staticmethod
    def _tool_argument_error(
        tool_name: str,
        arguments: dict[str, Any],
        definition: ToolDefinition | None,
    ) -> str | None:
        return tool_loop.argument_error(
            tool_name,
            arguments,
            definition,
            validate_arguments=tool_registry.validate_arguments,
        )

    @staticmethod
    def _sanitize_tool_arguments(tool_name: str, arguments: dict) -> dict:
        return tool_loop.sanitize_arguments(tool_name, arguments)
