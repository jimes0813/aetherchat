"""Pure assembly of shared model context.

The orchestrator owns when memory, knowledge, and tools are resolved. This
module owns how those already-resolved values become model instructions. It
has no database, provider, registry, or orchestration dependencies, which
keeps prompt ordering and tool-specific safety guidance independently
testable.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from ..models.schemas import ChatMessage

BROWSER_OPEN_TOOL_NAME = "plugin__browser_links__open_url"

MEMORY_WRITE_GUIDANCE = (
    "当用户确认了后续 Agent 会复用的稳定实体（角色、项目 URL、资源标识或关键事实）时，"
    "使用 upsert_memory_entity 写入会话共享记忆，避免重复确认；不要把密码、令牌或其他秘密写入共享记忆。"
)
BROWSER_GUIDANCE = (
    "浏览器导航要求：只为用户明确要求的目标调用一次 open_url，不要先打开搜索页或无关站点。"
    "用户指定 Firefox/火狐时把 browser 设为 firefox。工具只有在 execution_receipt.success=true "
    "时才表示桌面启动器已接受打开请求；这不证明网页内容、登录状态或加载结果，未经电脑观察工具"
    "核验时不得声称已看见页面内容。"
)
ARTIFACT_WRITE_GUIDANCE = (
    "文件写入要求：不得把长文全文塞进一次工具参数。超过约 1200 个汉字时，"
    "使用 write_artifact 分块写入：首块 mode=overwrite、sequence=0，"
    "后续每块 mode=append、sequence 依次递增，且每块不超过 1200 个汉字。"
    "需要保存到本机时，分块完成后只调用一次 documents.export_to_local，"
    "传工作区相对 path 与本机绝对 destination_path。"
    "不要用 desktop_control.write_document 写工作区相对路径，也不要在失败后"
    "原样重发同一长参数。"
)
DESKTOP_CONTROL_GUIDANCE = (
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
)
COMFYUI_GUIDANCE = (
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
)


class ContextAssembler:
    """Compose shared context from already-resolved, side-effect-free inputs."""

    @staticmethod
    def assemble(
        conversation_history: Sequence[ChatMessage],
        *,
        memory_context: str = "",
        tool_names: Collection[str] = (),
        attachment_context: str = "",
        project_knowledge_context: str = "",
        instruction_context: str = "",
        language: str = "",
        background_job_id: str = "",
        background_allowed_tools: Collection[str] = (),
    ) -> list[ChatMessage]:
        """Return a deep-copied context with deterministic instruction order."""

        messages = [message.model_copy(deep=True) for message in conversation_history]
        selected_tools = frozenset(str(name) for name in tool_names)

        def prepend(content: str) -> None:
            if content:
                messages.insert(0, ChatMessage(role="system", content=content))

        prepend(memory_context)
        prepend(instruction_context)
        if "upsert_memory_entity" in selected_tools:
            prepend(MEMORY_WRITE_GUIDANCE)
        if BROWSER_OPEN_TOOL_NAME in selected_tools:
            prepend(BROWSER_GUIDANCE)
        prepend(attachment_context)
        prepend(project_knowledge_context)
        if language == "中文优先":
            prepend("请优先使用清晰、准确的中文回复。")
        elif language == "中英双语":
            prepend("请使用中英双语回复，先中文后英文。")
        if "write_artifact" in selected_tools:
            prepend(ARTIFACT_WRITE_GUIDANCE)
        if any(name.startswith("plugin__desktop_control__") for name in selected_tools):
            prepend(DESKTOP_CONTROL_GUIDANCE)
        if any(name.startswith("plugin__comfyui__") for name in selected_tools):
            prepend(COMFYUI_GUIDANCE)
        if background_job_id:
            allowed_description = (
                "、".join(sorted(str(name) for name in background_allowed_tools))
                if background_allowed_tools
                else "无"
            )
            prepend(
                "本次请求由 AetherChat 本机后台调度器触发。"
                "无人值守执行必须遵守创建任务时固化的权限快照；"
                f"允许的副作用工具白名单：{allowed_description}。"
                "未列入白名单的副作用不得请求授权、不得尝试绕过，"
                "只能输出分析结果并明确需要人工处理的后续动作。"
            )
        return messages


context_assembler = ContextAssembler()
