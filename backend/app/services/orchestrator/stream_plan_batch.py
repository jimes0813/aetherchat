from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


class StreamPlanBatchMixin:
    async def _stream_plan_batch(
        self,
        executions: list[dict],
        outcomes: dict[str, dict],
        *,
        agent_run_id: str,
        queue: asyncio.Queue[dict],
        run_provider,
    ) -> AsyncIterator[dict]:
        step_by_model = {
            str(item["model"]): item
            for item in executions
        }
        tasks: list[asyncio.Task[None]] = [
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

