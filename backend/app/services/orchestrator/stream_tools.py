from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ...models.schemas import ToolCall
from ..approval_service import approval_policy
from ..tool_loop import (
    COMFYUI_IMAGE_TOOL,
    COMFYUI_VIDEO_TOOL,
    FILE_WRITE_TOOLS,
)
from ..tool_registry import tool_registry
from ._constants import BROWSER_OPEN_TOOL


class StreamToolsMixin:
    async def _execute_round_tool_calls(
        self,
        *,
        canonical_tool_calls: list,
        name: str,
        provider,
        queue: asyncio.Queue,
        content: str,
        attachment_image_paths,
        tool_loop_state,
        generated_files: list,
        tool_definitions_by_name: dict,
        agent_run_id: str,
        plan_step_id: str | None,
        workspace_id: str | None,
        background_job_id: str,
        background_allowed_tools: set[str],
        forced_tool_name,
        local_context: list,
        record_reasoning,
        tool_is_allowed,
    ) -> list:
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
            await self._invoke_authorized_tool(
                tool_call=tool_call,
                tool_name=tool_name,
                arguments=arguments,
                name=name,
                provider=provider,
                queue=queue,
                tool_loop_state=tool_loop_state,
                forced_tool_name=forced_tool_name,
                agent_run_id=agent_run_id,
                plan_step_id=plan_step_id,
                workspace_id=workspace_id,
                tool_call_records=tool_call_records,
                remember_tool_result=remember_tool_result,
            )

        self.tool_loop.append_results_to_context(
            tool_loop_state,
            local_context,
            tool_results,
        )
        return tool_results
