from __future__ import annotations

import asyncio
import json

from ...models.schemas import ChatMessage
from ..git_workspace_service import WORKSPACE_TOOL_NAMES


class StreamPseudoMixin:
    async def _recover_textual_tool_call(
        self,
        *,
        round_text: str,
        requested_name: str,
        provider_tools: list,
        available_tool_definitions,
        workspace_id: str | None,
        allow_side_effects: bool,
        queue: asyncio.Queue,
        name: str,
        tool_loop_state,
        local_context: list,
    ) -> bool:
        """Nudge a model that described a tool in text. True means stop the turn loop."""
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
            return True
        if tool_loop_state.record_pseudo_tool_retry():
            await queue.put({
                "type": "error",
                "code": "tool_call_format",
                "model": name,
                "message": f"{name} 连续以文本描述工具调用，已停止；请重试或检查本地模型工具调用模板。",
            })
            return True
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
        return False
