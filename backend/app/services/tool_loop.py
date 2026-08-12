"""Provider-neutral tool-loop protocol and state.

This module owns the deterministic parts of a model tool loop: call repair,
argument checks, persisted-argument redaction, result sanitization, tool-result
context messages, and retry/round state. It intentionally does not invoke
plugins, MCP servers, approval UI, databases, or providers.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models.schemas import ChatMessage, ToolCall, ToolDefinition

MAX_MODEL_TOOL_ROUNDS = 12
MAX_PSEUDO_TOOL_RETRIES = 2
MAX_INVALID_FILE_TOOL_CALLS = 3
MAX_TOOL_RESULT_CHARS = 180_000
MAX_TOOL_RESULT_IMAGES = 4

COMFYUI_STATUS_TOOL = "plugin__comfyui__status"
COMFYUI_IMAGE_TOOL = "plugin__comfyui__generate_image"
COMFYUI_VIDEO_TOOL = "plugin__comfyui__generate_video"
COMFYUI_WORKFLOW_TOOL = "plugin__comfyui__run_workflow"
DESKTOP_WRITE_DOCUMENT_TOOL = "plugin__desktop_control__write_document"
FILE_WRITE_TOOLS = frozenset({
    "write_artifact",
    "plugin__documents__export_to_local",
    DESKTOP_WRITE_DOCUMENT_TOOL,
})

type ArgumentValidator = Callable[[dict[str, Any], dict[str, Any]], None]
type ToolResult = tuple[ToolCall, dict[str, Any], Sequence[str]]


@dataclass(slots=True)
class ToolLoopState:
    """Mutable state for one provider's bounded sequence of tool rounds."""

    tool_round: int = 0
    required_tool_names: tuple[str, ...] = ()
    required_tool_index: int = 0
    required_tool_failed: bool = False
    pseudo_tool_retries: int = 0
    invalid_file_tool_calls: int = 0
    disabled_tool_names: set[str] = field(default_factory=set)
    max_tool_rounds: int = MAX_MODEL_TOOL_ROUNDS
    max_pseudo_tool_retries: int = MAX_PSEUDO_TOOL_RETRIES
    max_invalid_file_tool_calls: int = MAX_INVALID_FILE_TOOL_CALLS

    @property
    def current_required_tool(self) -> str | None:
        if self.required_tool_failed:
            return None
        if self.required_tool_index >= len(self.required_tool_names):
            return None
        return self.required_tool_names[self.required_tool_index]

    @property
    def required_tools_complete(self) -> bool:
        return (
            bool(self.required_tool_names)
            and not self.required_tool_failed
            and self.required_tool_index >= len(self.required_tool_names)
        )

    @property
    def tool_round_limit_reached(self) -> bool:
        return self.tool_round >= self.max_tool_rounds

    @property
    def invalid_file_limit_reached(self) -> bool:
        return self.invalid_file_tool_calls >= self.max_invalid_file_tool_calls

    def enabled_tools(
        self,
        definitions: Sequence[ToolDefinition],
    ) -> list[ToolDefinition]:
        return [
            definition
            for definition in definitions
            if definition.name not in self.disabled_tool_names
        ]

    def record_pseudo_tool_retry(self) -> bool:
        self.pseudo_tool_retries += 1
        return self.pseudo_tool_retries > self.max_pseudo_tool_retries

    def record_invalid_file_arguments(self, tool_name: str) -> int:
        if tool_name not in FILE_WRITE_TOOLS:
            return self.invalid_file_tool_calls
        self.invalid_file_tool_calls += 1
        if tool_name == DESKTOP_WRITE_DOCUMENT_TOOL:
            self.disabled_tool_names.add(tool_name)
        return self.invalid_file_tool_calls

    def record_tool_result(self, tool_name: str, status: object) -> None:
        if str(status or "unknown") != "success":
            return
        if tool_name in FILE_WRITE_TOOLS:
            self.invalid_file_tool_calls = 0
        if tool_name == COMFYUI_STATUS_TOOL:
            self.disabled_tool_names.add(tool_name)

    def mark_required_succeeded(self, tool_name: str) -> None:
        if tool_name == self.current_required_tool:
            self.required_tool_index += 1

    def mark_required_failed(self, tool_name: str) -> None:
        if tool_name == self.current_required_tool:
            self.required_tool_failed = True


class ToolLoop:
    """Pure protocol boundary used by the orchestrator's external executor."""

    def __init__(
        self,
        *,
        call_id_factory: Callable[[], str] | None = None,
        max_tool_rounds: int = MAX_MODEL_TOOL_ROUNDS,
        max_pseudo_tool_retries: int = MAX_PSEUDO_TOOL_RETRIES,
        max_invalid_file_tool_calls: int = MAX_INVALID_FILE_TOOL_CALLS,
    ) -> None:
        self._call_id_factory = call_id_factory or (
            lambda: f"call_{uuid4().hex}"
        )
        self.max_tool_rounds = max(1, int(max_tool_rounds))
        self.max_pseudo_tool_retries = max(0, int(max_pseudo_tool_retries))
        self.max_invalid_file_tool_calls = max(
            1,
            int(max_invalid_file_tool_calls),
        )

    def start(
        self,
        *,
        initial_tool_round: int = 0,
        required_tool_names: Sequence[str] = (),
        context: Sequence[ChatMessage] = (),
    ) -> ToolLoopState:
        state = ToolLoopState(
            tool_round=max(0, int(initial_tool_round)),
            required_tool_names=tuple(str(name) for name in required_tool_names),
            max_tool_rounds=self.max_tool_rounds,
            max_pseudo_tool_retries=self.max_pseudo_tool_retries,
            max_invalid_file_tool_calls=self.max_invalid_file_tool_calls,
        )
        for required_name in state.required_tool_names:
            matching_results = [
                message
                for message in context
                if message.role == "tool" and message.name == required_name
            ]
            if any(not message.tool_error for message in matching_results):
                state.required_tool_index += 1
                continue
            if matching_results:
                state.required_tool_failed = True
            break
        return state

    def normalize_tool_calls(
        self,
        calls: Sequence[dict[str, Any] | ToolCall],
    ) -> list[ToolCall]:
        normalized: list[ToolCall] = []
        seen_ids: set[str] = set()
        for raw_call in calls:
            if isinstance(raw_call, ToolCall):
                call_id = raw_call.id.strip()
                tool_name = raw_call.name.strip()
                arguments: object = raw_call.arguments
            else:
                call_id = str(raw_call.get("id") or "").strip()
                tool_name = str(raw_call.get("name") or "").strip()
                arguments = raw_call.get("arguments") or {}
            tool_name = tool_name or "unknown_tool"
            if not isinstance(arguments, dict):
                parsed_arguments: object | None = None
                if isinstance(arguments, str):
                    try:
                        parsed_arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        parsed_arguments = None
                arguments = (
                    parsed_arguments
                    if isinstance(parsed_arguments, dict)
                    else {"_raw": arguments}
                )
            if not call_id or call_id in seen_ids:
                call_id = self._call_id_factory()
                while not call_id or call_id in seen_ids:
                    call_id = self._call_id_factory()
            seen_ids.add(call_id)
            normalized.append(ToolCall(
                id=call_id,
                name=tool_name,
                arguments=arguments,
            ))
        return normalized

    @staticmethod
    def argument_error(
        tool_name: str,
        arguments: dict[str, Any],
        definition: ToolDefinition | None,
        *,
        validate_arguments: ArgumentValidator,
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
                validate_arguments(definition.parameters, arguments)
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
    def sanitize_arguments(
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
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

    @staticmethod
    def sanitize_result(value: object) -> tuple[object, list[str]]:
        images: list[str] = []

        def visit(item: object) -> object:
            if isinstance(item, dict):
                safe: dict[object, object] = {}
                for key, child in item.items():
                    if (
                        key in {"image_data_url", "dataUrl"}
                        and isinstance(child, str)
                        and child.startswith("data:image/")
                    ):
                        images.append(child)
                        safe[key] = "[image attached separately]"
                    else:
                        safe[key] = visit(child)
                return safe
            if isinstance(item, list):
                return [visit(child) for child in item]
            if (
                isinstance(item, str)
                and item.startswith("data:image/")
                and ";base64," in item
            ):
                images.append(item)
                return "[image attached separately]"
            return item

        return visit(value), images

    @staticmethod
    def generated_files_from_result(
        tool_name: str,
        value: object,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

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
                    or (
                        path.rsplit("/", 1)[-1]
                        if path
                        else url.rsplit("/", 1)[-1]
                    )
                    or "generated-file"
                )[:240],
                "mime_type": str(
                    raw.get("mime_type") or "application/octet-stream"
                ),
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
        unique: dict[str, dict[str, Any]] = {}
        for item in results:
            key = str(item.get("path") or item.get("url") or "")
            if key:
                unique[key] = item
        return list(unique.values())

    @staticmethod
    def append_results_to_context(
        state: ToolLoopState,
        context: list[ChatMessage],
        results: Sequence[ToolResult],
    ) -> None:
        for previous_message in context:
            previous_message.images = []
        remaining_result_chars = MAX_TOOL_RESULT_CHARS
        remaining_image_slots = MAX_TOOL_RESULT_IMAGES
        for tool_call, tool_result, result_images in results:
            serialized = json.dumps(
                tool_result,
                ensure_ascii=False,
                default=str,
                indent=2,
            )
            if len(serialized) > remaining_result_chars:
                if remaining_result_chars > 0:
                    serialized = (
                        serialized[:remaining_result_chars]
                        + "\n...[工具结果已截断]"
                    )
                else:
                    serialized = "[工具结果因本轮总长度上限已省略]"
            remaining_result_chars = max(
                0,
                remaining_result_chars - len(serialized),
            )
            attached_images = list(result_images[:remaining_image_slots])
            remaining_image_slots -= len(attached_images)
            context.append(ChatMessage(
                role="tool",
                name=tool_call.name,
                tool_call_id=tool_call.id,
                tool_error=tool_result.get("status") != "success",
                content=(
                    f"第 {state.tool_round + 1} 轮工具 {tool_call.name} 的结果。"
                    "如执行了界面动作，请重新调用 get_state 验证，不要凭旧截图继续。\n"
                    f"{serialized}"
                ),
                images=attached_images,
            ))
        state.tool_round += 1


tool_loop = ToolLoop()
