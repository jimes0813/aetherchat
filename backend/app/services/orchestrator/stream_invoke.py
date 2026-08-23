from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ..mcp_registry import mcp_registry
from ..plugin_registry import plugin_registry
from ..tool_registry import ToolInvoke, tool_registry


class StreamInvokeMixin:
    async def _invoke_authorized_tool(
        self,
        *,
        tool_call,
        tool_name: str,
        arguments: dict,
        name: str,
        provider,
        queue: asyncio.Queue,
        tool_loop_state,
        forced_tool_name,
        agent_run_id: str,
        plan_step_id: str | None,
        workspace_id: str | None,
        tool_call_records: dict,
        remember_tool_result,
    ) -> None:
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
            return

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
            return

        tool = tool_registry.find_by_name(tool_name, self.session_id)
        if tool is None:
            error = f"模型请求了未注册工具：{tool_name}"
            await queue.put({"type": "error", "code": "unknown_tool", "model": name, "message": error})
            await remember_tool_result(
                tool_call,
                {"tool": tool_name, "status": "error", "error": error},
                [],
            )
            return
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
            return
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
