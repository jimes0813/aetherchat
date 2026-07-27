from __future__ import annotations

import asyncio
import builtins
import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, Field

from .artifact_service import _data_root, _lock_for, _utc_now


class ToolCreate(BaseModel):
    name: str
    description: str
    category: str = "custom"
    owner: str = "user"
    kind: Literal["template", "internal"] = "template"
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    output_template: str = "工具 ${name} 已执行，输入：${input}"
    enabled: bool = True
    session_id: str | None = None


class ToolInvoke(BaseModel):
    caller: str = "user"
    arguments: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None


class ToolRegistry:
    """Durable registry and ToolRun audit log.

    System tools are global.  User-created tools may be attached to a session;
    listing with a session includes those tools plus the shared system tools.
    Runs always carry a concrete ``session_id`` and can be exported without
    leaking records from another workspace.
    """

    def __init__(self, base_path: str | os.PathLike[str] | None = None):
        root = _data_root(base_path) / "tools"
        root.mkdir(parents=True, exist_ok=True)
        self.registry_path = root / "registry.json"
        self.runs_path = root / "runs.json"
        self.tools: dict[str, dict[str, Any]] = {}
        self.runs: list[dict[str, Any]] = []
        self._reload()
        self._seed()

    def _seed(self) -> None:
        seeds = [
            ToolCreate(
                name="list_tasks",
                description="列出当前会话任务",
                category="project",
                owner="system",
                kind="internal",
                input_schema={"type": "object", "properties": {}},
                output_template="已读取任务列表。",
            ),
            ToolCreate(
                name="create_task",
                description="在当前会话创建任务",
                category="project",
                owner="system",
                kind="internal",
                input_schema={
                    "type": "object",
                    "required": ["title"],
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "priority": {"type": "integer"},
                    },
                },
            ),
            ToolCreate(
                name="claim_task",
                description="认领当前会话中的待办任务",
                category="project",
                owner="system",
                kind="internal",
                input_schema={
                    "type": "object",
                    "required": ["task_id"],
                    "properties": {"task_id": {"type": "string"}},
                },
            ),
            ToolCreate(
                name="subdivide_task",
                description="把当前会话任务拆分为多个待办子任务",
                category="project",
                owner="system",
                kind="internal",
                input_schema={
                    "type": "object",
                    "required": ["task_id", "subtasks"],
                    "properties": {
                        "task_id": {"type": "string"},
                        "subtasks": {"type": "array", "items": {"type": "string"}},
                    },
                },
            ),
            ToolCreate(
                name="get_progress_summary",
                description="汇总当前会话各任务状态",
                category="project",
                owner="system",
                kind="internal",
                input_schema={"type": "object", "properties": {}},
            ),
            ToolCreate(
                name="read_artifact",
                description="读取当前会话工件内容",
                category="artifact",
                owner="system",
                kind="internal",
                input_schema={
                    "type": "object",
                    "required": ["path"],
                    "properties": {"path": {"type": "string"}},
                },
            ),
            ToolCreate(
                name="write_artifact",
                description="写入当前会话工件并保存完整版本",
                category="artifact",
                owner="system",
                kind="internal",
                input_schema={
                    "type": "object",
                    "required": ["path", "content"],
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "commit_message": {"type": "string"},
                    },
                },
            ),
            ToolCreate(
                name="run_command",
                description="在受控沙箱中运行命令",
                category="sandbox",
                owner="system",
                kind="internal",
                input_schema={
                    "type": "object",
                    "required": ["cmd"],
                    "properties": {
                        "cmd": {"type": "string"},
                        "cwd": {"type": "string"},
                        "timeout": {"type": "integer"},
                    },
                },
            ),
            ToolCreate(
                name="workspace_list_files",
                description="列出当前 Worker 独立 Git worktree 中的文件",
                category="agent_workspace",
                owner="system",
                kind="internal",
                input_schema={"type": "object", "properties": {}},
            ),
            ToolCreate(
                name="workspace_read_file",
                description="读取当前 Worker 独立 Git worktree 中的 UTF-8 文本文件",
                category="agent_workspace",
                owner="system",
                kind="internal",
                input_schema={
                    "type": "object",
                    "required": ["path"],
                    "properties": {"path": {"type": "string"}},
                },
            ),
            ToolCreate(
                name="workspace_write_file",
                description="写入当前 Worker 独立 Git worktree；不会修改用户主仓库",
                category="agent_workspace",
                owner="system",
                kind="internal",
                input_schema={
                    "type": "object",
                    "required": ["path", "content"],
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
            ),
            ToolCreate(
                name="workspace_delete_file",
                description="删除当前 Worker 独立 Git worktree 中的普通文件",
                category="agent_workspace",
                owner="system",
                kind="internal",
                input_schema={
                    "type": "object",
                    "required": ["path"],
                    "properties": {"path": {"type": "string"}},
                },
            ),
            ToolCreate(
                name="workspace_run_command",
                description="在当前 Worker worktree 的断网 Docker 沙箱中运行命令",
                category="agent_workspace",
                owner="system",
                kind="internal",
                input_schema={
                    "type": "object",
                    "required": ["cmd"],
                    "properties": {
                        "cmd": {"type": "string"},
                        "cwd": {"type": "string"},
                        "timeout": {"type": "integer"},
                    },
                },
            ),
            ToolCreate(
                name="workspace_status",
                description="读取当前 Worker 独立 Git worktree 的变更状态",
                category="agent_workspace",
                owner="system",
                kind="internal",
                input_schema={"type": "object", "properties": {}},
            ),
            ToolCreate(
                name="create_merge_proposal",
                description="为当前会话创建合并提案",
                category="merge",
                owner="system",
                kind="internal",
                input_schema={
                    "type": "object",
                    "required": ["artifact_path", "rationale"],
                    "properties": {
                        "artifact_path": {"type": "string"},
                        "rationale": {"type": "string"},
                        "merge_rule": {
                            "type": "string",
                            "enum": ["two_votes", "facilitator", "manual_confirmation"],
                        },
                    },
                },
            ),
            # Alias retained because several prompts use the shorter verb.
            ToolCreate(
                name="propose_merge",
                description="为当前会话创建合并提案",
                category="merge",
                owner="system",
                kind="internal",
                input_schema={
                    "type": "object",
                    "required": ["artifact_path", "rationale"],
                    "properties": {
                        "artifact_path": {"type": "string"},
                        "rationale": {"type": "string"},
                        "merge_rule": {"type": "string"},
                    },
                },
            ),
            ToolCreate(
                name="vote_on_proposal",
                description="对当前会话的合并提案投票",
                category="merge",
                owner="system",
                kind="internal",
                input_schema={
                    "type": "object",
                    "required": ["proposal_id", "approve"],
                    "properties": {
                        "proposal_id": {"type": "string"},
                        "approve": {"type": "boolean"},
                    },
                },
            ),
            ToolCreate(
                name="summarize_cn",
                description="将输入内容整理成中文摘要",
                category="model",
                owner="system",
                kind="template",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
                output_template="中文摘要：${text}",
            ),
        ]

        existing_names = {tool.get("name") for tool in self.tools.values()}
        changed = False
        for payload in seeds:
            if payload.name in existing_names:
                continue
            tool = payload.model_dump()
            tool["id"] = str(uuid5(NAMESPACE_URL, f"aetherchat:system-tool:{payload.name}"))
            tool["created_at"] = _utc_now()
            tool["updated_at"] = tool["created_at"]
            self.tools[tool["id"]] = tool
            existing_names.add(payload.name)
            changed = True
        if changed:
            self._save_tools()

    def list(self, session_id: str | None = None) -> list[dict[str, Any]]:
        self._reload_tools()
        tools = list(self.tools.values())
        if session_id is not None:
            session = str(session_id)
            tools = [tool for tool in tools if tool.get("session_id") in {None, session}]
        return sorted((deepcopy(tool) for tool in tools), key=lambda item: (item.get("category", ""), item.get("name", "")))

    def create(self, payload: ToolCreate) -> dict[str, Any]:
        tool = payload.model_dump()
        tool["session_id"] = str(tool["session_id"]) if tool.get("session_id") else None
        tool["id"] = str(uuid4())
        tool["created_at"] = _utc_now()
        tool["updated_at"] = tool["created_at"]
        with _lock_for(self.registry_path):
            self.tools = self._load_tools_unlocked()
            self.tools[tool["id"]] = tool
            self._save_tools_unlocked()
        return deepcopy(tool)

    def delete(self, tool_id: str) -> dict[str, Any] | None:
        with _lock_for(self.registry_path):
            self.tools = self._load_tools_unlocked()
            deleted = self.tools.pop(tool_id, None)
            if deleted is not None:
                self._save_tools_unlocked()
        return deepcopy(deleted) if deleted is not None else None

    def get(self, tool_id: str) -> dict[str, Any] | None:
        self._reload_tools()
        tool = self.tools.get(tool_id)
        return deepcopy(tool) if tool is not None else None

    def find_by_name(self, name: str, session_id: str | None = None) -> dict[str, Any] | None:
        candidates = [tool for tool in self.list(session_id) if tool.get("name") == name]
        if not candidates:
            return None
        if session_id is not None:
            session_specific = [tool for tool in candidates if tool.get("session_id") == str(session_id)]
            if session_specific:
                return session_specific[0]
        return candidates[0]

    async def invoke_async(
        self,
        tool_id: str,
        payload: ToolInvoke,
        session_id: str | None = None,
        *,
        execution_context: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        tool = self.get(tool_id)
        if not tool:
            raise KeyError(tool_id)
        if not tool.get("enabled", True):
            raise PermissionError("Tool disabled")

        arguments = deepcopy(payload.arguments)
        resolved_session = str(
            session_id
            or payload.session_id
            or arguments.get("session_id")
            or tool.get("session_id")
            or "default"
        )
        arguments.pop("session_id", None)

        if tool.get("kind") == "internal":
            from .tool_executor import execute_tool

            return await execute_tool(
                tool["name"],
                arguments,
                payload.caller,
                resolved_session,
                tool_id=tool["id"],
                registry=self,
                execution_context=execution_context,
            )

        started_at = _utc_now()
        output = self._render_output(tool, arguments)
        return self.record_run(
            tool_id=tool["id"],
            tool_name=tool["name"],
            caller=payload.caller,
            session_id=resolved_session,
            arguments=arguments,
            status="success",
            output=output,
            error=None,
            started_at=started_at,
        )

    def invoke(self, tool_id: str, payload: ToolInvoke) -> dict[str, Any]:
        """Compatibility wrapper for older synchronous callers.

        Internal tools still execute their real async implementation.  When an
        event loop is already active (the legacy orchestrator does this), the
        coroutine is run in a short-lived worker thread rather than falling
        back to a rendered success message.
        """

        tool = self.get(tool_id)
        if not tool:
            raise KeyError(tool_id)
        if not tool.get("enabled", True):
            raise PermissionError("Tool disabled")

        if tool.get("kind") != "internal":
            arguments = deepcopy(payload.arguments)
            resolved_session = str(payload.session_id or arguments.pop("session_id", None) or tool.get("session_id") or "default")
            started_at = _utc_now()
            output = self._render_output(tool, arguments)
            return self.record_run(
                tool_id=tool["id"],
                tool_name=tool["name"],
                caller=payload.caller,
                session_id=resolved_session,
                arguments=arguments,
                status="success",
                output=output,
                error=None,
                started_at=started_at,
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.invoke_async(tool_id, payload))

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="aether-tool") as pool:
            return pool.submit(lambda: asyncio.run(self.invoke_async(tool_id, payload))).result()

    def record_run(
        self,
        *,
        tool_id: str | None,
        tool_name: str,
        caller: str,
        session_id: str,
        arguments: dict[str, Any],
        status: str,
        output: Any,
        error: str | None,
        started_at: str,
    ) -> dict[str, Any]:
        completed_at = _utc_now()
        try:
            started = datetime.fromisoformat(started_at)
            completed = datetime.fromisoformat(completed_at)
            duration_ms = max(0, round((completed - started).total_seconds() * 1000, 3))
        except (TypeError, ValueError):
            duration_ms = 0

        structured_result = deepcopy(output)
        display_output = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str)
        run = {
            "id": str(uuid4()),
            "session_id": str(session_id),
            "tool_id": tool_id,
            "tool_name": tool_name,
            "caller": caller,
            "arguments": deepcopy(arguments),
            "status": status,
            "output": display_output,
            "result": structured_result,
            "error": error,
            "started_at": started_at,
            "completed_at": completed_at,
            "created_at": started_at,
            "duration_ms": duration_ms,
        }
        with _lock_for(self.runs_path):
            self.runs = self._load_runs_unlocked()
            self.runs.insert(0, run)
            self._save_runs_unlocked()
        return deepcopy(run)

    def list_runs(
        self,
        session_id: str | None = None,
        limit: int | None = 50,
    ) -> builtins.list[dict[str, Any]]:
        self._reload_runs()
        runs = self.runs
        if session_id is not None:
            session = str(session_id)
            runs = [run for run in runs if str(run.get("session_id")) == session]
        selected = runs if limit is None else runs[: max(0, limit)]
        return deepcopy(selected)

    def _render_output(self, tool: dict[str, Any], arguments: dict[str, Any]) -> str:
        data = {"name": tool["name"], "input": json.dumps(arguments, ensure_ascii=False), **arguments}
        try:
            return Template(tool.get("output_template") or "").safe_substitute(data)
        except Exception:
            return f"{tool['name']} 已执行。"

    def _reload(self) -> None:
        self._reload_tools()
        self._reload_runs()

    def _reload_tools(self) -> None:
        with _lock_for(self.registry_path):
            self.tools = self._load_tools_unlocked()

    def _reload_runs(self) -> None:
        with _lock_for(self.runs_path):
            self.runs = self._load_runs_unlocked()

    def _load_tools_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self.registry_path.exists():
            return {}
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return {}
        if isinstance(payload, dict) and "tools" in payload:
            payload = payload["tools"]
        if isinstance(payload, list):
            return {str(item["id"]): item for item in payload if isinstance(item, dict) and item.get("id")}
        if isinstance(payload, dict):
            return {str(key): value for key, value in payload.items() if isinstance(value, dict)}
        return {}

    def _load_runs_unlocked(self) -> builtins.list[dict[str, Any]]:
        if not self.runs_path.exists():
            return []
        try:
            payload = json.loads(self.runs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return []
        if isinstance(payload, dict):
            payload = payload.get("runs", [])
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def _save_tools(self) -> None:
        with _lock_for(self.registry_path):
            self._save_tools_unlocked()

    def _save_tools_unlocked(self) -> None:
        payload = {"version": 1, "tools": list(self.tools.values())}
        self._atomic_json_write(self.registry_path, payload)

    def _save_runs_unlocked(self) -> None:
        payload = {"version": 1, "runs": self.runs}
        self._atomic_json_write(self.runs_path, payload)

    @staticmethod
    def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temp_path.replace(path)


tool_registry = ToolRegistry()
