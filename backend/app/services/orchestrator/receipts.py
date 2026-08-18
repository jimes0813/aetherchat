from __future__ import annotations

import re
from typing import Any

from ...models.schemas import ChatMessage
from ..tool_loop import (
    tool_loop,
)
from ._constants import BROWSER_OPEN_TOOL


class ReceiptsMixin:
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
    def _requested_comfyui_video_model(content: str) -> str | None:
        """Extract an explicitly named video model without trusting the LLM.

        Video requests often contain the exact local ``.safetensors`` name.
        Preserve that user choice in a hidden server-side argument just as the
        image path does for image checkpoints.
        """

        matches: list[str] = re.findall(
            r"([a-z0-9_.-]+\.safetensors)",
            str(content or ""),
            flags=re.IGNORECASE,
        )
        for value in matches:
            lowered = value.casefold()
            if any(
                marker in lowered
                for marker in ("wan", "ltx", "i2v", "t2v", "video", "minimax")
            ):
                return value
        return matches[0] if matches else None

    @staticmethod
    def _generated_files_from_tool_result(
        tool_name: str,
        value: object,
    ) -> list[dict]:
        return tool_loop.generated_files_from_result(tool_name, value)

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
