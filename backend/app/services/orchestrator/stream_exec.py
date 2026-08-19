from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ...providers.base import LLMProvider
from ..memory_service import memory_service
from ..skills_rules_service import skills_rules_service


class StreamExecMixin:
    async def _execute_selected_providers(
        self,
        *,
        action: dict,
        content: str,
        targets: list,
        explicit_targets: set,
        agent_run_id: str,
        background_job_id: str,
        background_allowed_tools: set[str],
        workspace_service,
        attachment_images,
        attachment_image_paths,
        attachment_context: str,
        reference_context: str,
        project_knowledge_context: str,
    ) -> AsyncIterator[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        selected = [name for name in targets if self._should_participate(name, content, targets, explicit_targets)]
        if selected:
            from ..platform_governance import platform_governance

            selected = platform_governance.filter_model_names(
                selected,
                project_id=self.project_id,
                session_id=self.session_id,
            )
        if not selected:
            yield {
                "type": "error",
                "message": "没有选中可用模型。请选择具体模型，或选择全部模型后重试。若已开启模型白名单，请在「治理」面板检查策略。",
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
        instruction_bundle = skills_rules_service.resolve_for_session(
            self.session_id,
            user_message=str(content or ""),
        )
        context_msgs = self.context_assembler.assemble(
            self.conversation_history,
            memory_context=memory_service.context(self.session_id),
            tool_names=base_tool_names,
            attachment_context=attachment_context,
            reference_context=reference_context,
            project_knowledge_context=project_knowledge_context,
            instruction_context=str(instruction_bundle.get("context") or ""),
            language=str(action.get("language") or "").strip(),
            background_job_id=background_job_id,
            background_allowed_tools=background_allowed_tools,
        )

        async def run_provider(
            name: str,
            provider: LLMProvider,
            execution: dict | None = None,
        ):
            await self._run_selected_provider(
                name,
                provider,
                execution,
                queue=queue,
                content=content,
                action=action,
                available_tool_definitions=available_tool_definitions,
                tool_definitions_by_name=tool_definitions_by_name,
                background_job_id=background_job_id,
                background_allowed_tools=background_allowed_tools,
                context_msgs=context_msgs,
                attachment_images=attachment_images,
                attachment_image_paths=attachment_image_paths,
                agent_run_id=agent_run_id,
            )

        async for event in self._stream_selected_batches(
            selected=selected,
            planned_execution=planned_execution,
            plan_bundle=plan_bundle,
            agent_run_id=agent_run_id,
            queue=queue,
            run_provider=run_provider,
            action=action,
            workspace_service=workspace_service,
            context_msgs=context_msgs,
        ):
            yield event
