from __future__ import annotations


class StreamPlanReadyMixin:
    def _select_ready_plan_steps(
        self,
        *,
        agent_run_id: str,
        plan_steps: list[dict],
    ) -> tuple[list[dict], list[dict], bool]:
        """Pick the next executable plan steps.

        Returns (events, ready_steps, terminal_error). An empty ready list
        means the DAG loop should stop.
        """
        events: list[dict] = []
        by_key = {step["step_key"]: step for step in plan_steps}
        pending = [
            step for step in plan_steps
            if step["status"] in {"pending", "ready", "interrupted"}
        ]
        if not pending:
            return events, [], False

        ready: list[dict] = []
        for step in pending:
            if (
                step["kind"] in {"verifier", "synthesizer"}
                and all(
                    candidate["status"] in {
                        "completed", "failed", "blocked", "interrupted", "skipped",
                    }
                    for candidate in plan_steps
                    if candidate["kind"] == "worker"
                )
                and not any(
                    candidate["kind"] == "worker"
                    and candidate["status"] == "completed"
                    for candidate in plan_steps
                )
            ):
                blocked = self.run_service.finish_plan_step(
                    agent_run_id,
                    step["id"],
                    status="blocked",
                    error="没有成功完成的 Worker 输出，无法核验或综合",
                )
                events.append({
                    "type": "plan_step_update",
                    "run_id": agent_run_id,
                    "plan_step": self._public_plan_step(blocked),
                })
                continue
            dependency_states = [
                by_key.get(key, {}).get("status")
                for key in step.get("dependencies") or []
            ]
            if all(status == "completed" for status in dependency_states):
                ready.append(step)
                continue
            terminal_dependencies = all(
                status in {
                    "completed", "failed", "blocked", "interrupted", "skipped",
                }
                for status in dependency_states
            )
            if step.get("allow_failed_dependencies") and terminal_dependencies:
                ready.append(step)
                continue
            if terminal_dependencies and any(
                status != "completed" for status in dependency_states
            ):
                blocked = self.run_service.finish_plan_step(
                    agent_run_id,
                    step["id"],
                    status="blocked",
                    error="依赖步骤失败，当前步骤未执行",
                )
                events.append({
                    "type": "plan_step_update",
                    "run_id": agent_run_id,
                    "plan_step": self._public_plan_step(blocked),
                })

        if not ready:
            refreshed = self.run_service.database.list_agent_plan_steps(agent_run_id)
            unresolved = [
                step for step in refreshed
                if step["status"] in {"pending", "ready", "interrupted"}
            ]
            if unresolved:
                for step in unresolved:
                    blocked = self.run_service.finish_plan_step(
                        agent_run_id,
                        step["id"],
                        status="blocked",
                        error="计划依赖无法满足或检测到无进展状态",
                    )
                    events.append({
                        "type": "plan_step_update",
                        "run_id": agent_run_id,
                        "plan_step": self._public_plan_step(blocked),
                    })
                events.append({
                    "type": "error",
                    "code": "plan_blocked",
                    "message": "Plan DAG 没有可执行步骤，已安全停止。",
                })
                return events, [], True
            return events, [], False

        return events, ready, False
