from __future__ import annotations

import hashlib
import json
from typing import Any

from .chat_database import ChatDatabase, chat_database
from .git_workspace_service import (
    WORKSPACE_TOOL_NAMES,
    GitWorkspaceService,
    git_workspace_service,
)

RETRYABLE_STATUSES = {"failed", "interrupted", "recoverable"}
TERMINAL_STEP_STATUSES = {"completed", "failed", "interrupted"}


class AgentRunService:
    """Persists coarse-grained orchestration state without storing token deltas."""

    def __init__(
        self,
        database: ChatDatabase | None = None,
        workspace_service: GitWorkspaceService | None = None,
    ):
        self.database = database or chat_database
        self.workspace_service = workspace_service or (
            git_workspace_service if self.database is chat_database else None
        )
        self._approval_models: dict[str, str] = {}

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @classmethod
    def _digest(cls, value: Any) -> str:
        return hashlib.sha256(cls._canonical_json(value).encode("utf-8")).hexdigest()

    @classmethod
    def _sanitize_persisted_value(cls, value: Any, *, key: str = "") -> Any:
        lowered = key.lower()
        if any(token in lowered for token in (
            "api_key", "apikey", "password", "passwd", "secret",
            "access_token", "refresh_token", "authorization", "cookie",
        )):
            return "[redacted]"
        if isinstance(value, dict):
            return {
                str(child_key): cls._sanitize_persisted_value(child, key=str(child_key))
                for child_key, child in list(value.items())[:200]
            }
        if isinstance(value, list):
            return [cls._sanitize_persisted_value(child, key=key) for child in value[:200]]
        if isinstance(value, str):
            if value.startswith("data:image/") and ";base64," in value:
                return f"[image omitted sha256={hashlib.sha256(value.encode('utf-8')).hexdigest()}]"
            if len(value) > 50_000:
                digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
                return value[:50_000] + f"\n...[truncated sha256={digest}]"
        return value

    @staticmethod
    def _is_side_effecting_tool(tool_name: str) -> bool:
        name = str(tool_name)
        if name in WORKSPACE_TOOL_NAMES:
            # These tools can only resolve an orchestrator-issued opaque
            # worktree context and never target the user's checked-out repo.
            return False
        if name in {"list_tasks", "read_artifact", "get_progress_summary", "summarize_cn"}:
            return False
        read_only_suffixes = {
            "capabilities", "list_apps", "list_windows", "get_state",
            "read_document", "inventory", "status", "workflow_schema", "git_status",
            "workspace_health", "task_chart",
        }
        is_read_only_plugin = (
            name.startswith("plugin__")
            and name.rsplit("__", 1)[-1] in read_only_suffixes
        )
        # Remote MCP tools are treated as side effects unless the connector can
        # eventually provide an explicit read-only annotation.
        return not is_read_only_plugin

    def is_side_effecting_tool(self, tool_name: str) -> bool:
        return self._is_side_effecting_tool(tool_name)

    @staticmethod
    def _targets(action: dict[str, Any]) -> list[str]:
        values = action.get("targets") or []
        if not isinstance(values, list):
            return []
        return [str(value) for value in values if str(value).strip() and value != "all"]

    @staticmethod
    def _string_list(value: Any, *, limit: int = 12, item_limit: int = 1000) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            normalized = " ".join(str(item).split())[:item_limit]
            if normalized and normalized not in result:
                result.append(normalized)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _preferred_model(
        models: list[str],
        profiles: dict[str, str],
        keywords: tuple[str, ...],
        *,
        fallback: str,
    ) -> str:
        for model in models:
            persona = profiles.get(model, "").lower()
            if any(keyword in persona for keyword in keywords):
                return model
        return fallback

    def ensure_plan(
        self,
        *,
        run_id: str,
        session_id: str,
        action: dict[str, Any],
        models: list[str],
        profiles: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        existing_steps = self.database.list_agent_plan_steps(run_id)
        existing_goal = self.database.get_agent_goal(run_id)
        if existing_goal is not None and existing_steps:
            current_run = self.database.get_agent_run(
                run_id, include_events=False
            )
            if current_run and int(current_run.get("plan_revision") or 0) == 0:
                self.database.initialize_agent_plan(
                    run_id=run_id,
                    session_id=session_id,
                    approval_required=bool(
                        action.get("plan_confirmation_required")
                    ),
                )
            return {"goal": existing_goal, "steps": existing_steps}

        selected = list(dict.fromkeys(str(model) for model in models if str(model).strip()))
        if not selected:
            raise ValueError("计划至少需要一个可用模型")
        raw_goal = action.get("goal_spec")
        goal_spec = raw_goal if isinstance(raw_goal, dict) else {}
        objective = " ".join(
            str(goal_spec.get("objective") or action.get("content") or "").split()
        )[:20_000]
        if not objective:
            raise ValueError("计划目标不能为空")
        success_criteria = self._string_list(goal_spec.get("success_criteria"))
        if not success_criteria:
            success_criteria = [
                "最终交付直接回应用户目标，不以过程描述代替结果",
                "关键结论说明依据、工具结果或无法验证的边界",
                "不得把未执行、未检查或失败的动作表述为已经完成",
            ]
        constraints = self._string_list(goal_spec.get("constraints"))
        language = str(action.get("language") or "").strip()
        if language:
            constraints.append(f"语言策略：{language}")
        if action.get("attachment_ids"):
            constraints.append("使用附件时保留文件名与位置引用")
        constraints.append("所有副作用工具继续遵守独立授权和幂等保护")
        if action.get("repository_id"):
            constraints.extend([
                (
                    "代码修改只允许发生在 AetherChat 为当前 Worker 创建的"
                    "独立 Git worktree；不得直接写入主仓库"
                ),
                (
                    "最终应用必须使用确定性 diff/命令证据，并由用户在"
                    "桌面原生确认后执行"
                ),
            ])
            commands = self._string_list(
                action.get("verification_commands"),
                limit=4,
                item_limit=2000,
            )
            if commands:
                constraints.append(
                    "确定性核验命令：" + "；".join(commands)
                )
        constraints = list(dict.fromkeys(constraints))[:12]
        deliverable = " ".join(
            str(
                goal_spec.get("deliverable")
                or "一份经过独立复核的最终答复；如产生工件，列出实际文件与验证状态"
            ).split()
        )[:4000]
        goal = self.database.create_agent_goal(
            run_id=run_id,
            session_id=session_id,
            objective=objective,
            success_criteria=success_criteria,
            constraints=constraints,
            deliverable=deliverable,
        )

        profile_map = {
            str(model): str(persona or "")
            for model, persona in (profiles or {}).items()
        }
        verifier_model = self._preferred_model(
            selected,
            profile_map,
            ("review", "critic", "verify", "architect", "审", "评"),
            fallback=selected[-1],
        )
        synthesizer_model = self._preferred_model(
            selected,
            profile_map,
            ("synth", "lead", "architect", "综合", "主笔"),
            fallback=selected[0],
        )
        worker_keys = [f"worker:{index + 1}:{model}" for index, model in enumerate(selected)]
        planner_output = {
            "strategy": "planner-worker-verifier-synthesizer",
            "worker_models": selected,
            "verifier_model": verifier_model,
            "synthesizer_model": synthesizer_model,
        }
        steps: list[dict[str, Any]] = [{
            "step_key": "planner",
            "sequence": 0,
            "phase": 0,
            "kind": "planner",
            "title": "形成目标与执行计划",
            "description": "确定性规划器根据用户目标、约束和可用模型建立依赖图。",
            "assigned_model": None,
            "role": "Deterministic Planner",
            "status": "completed",
            "dependencies": [],
            "input_contract": {"goal_spec": "AgentGoal"},
            "output_contract": {"plan": "PlanDAG"},
            "success_criteria": ["依赖无环", "每个执行步骤都有模型、输入和输出契约"],
            "output": planner_output,
            "output_sha256": self._digest(planner_output),
        }]
        for index, model in enumerate(selected):
            persona = profile_map.get(model, "").strip() or "通用执行 Agent"
            steps.append({
                "step_key": worker_keys[index],
                "sequence": index + 1,
                "phase": 1,
                "kind": "worker",
                "title": f"{model} 独立执行",
                "description": (
                    f"以互相独立的上下文完成目标；职责：{persona}。"
                    "给出实际结果、证据、风险和未验证项。"
                ),
                "assigned_model": model,
                "role": persona,
                "status": "pending",
                "dependencies": ["planner"],
                "input_contract": {"goal": "AgentGoal", "shared_context": "ChatContext"},
                "output_contract": {
                    "content": "string",
                    "evidence": "explicit in content",
                    "uncertainties": "explicit in content",
                },
                "success_criteria": success_criteria,
            })
        steps.extend([
            {
                "step_key": "verifier",
                "sequence": len(selected) + 1,
                "phase": 2,
                "kind": "verifier",
                "title": "独立核验候选结果",
                "description": (
                    "对 Worker 结果逐项核对目标覆盖、证据、矛盾、工具失败和未验证声明，"
                    "输出结构化 verdict、summary、evidence 与 issues。"
                ),
                "assigned_model": verifier_model,
                "role": "Verifier / Critic",
                "status": "pending",
                "dependencies": worker_keys,
                "allow_failed_dependencies": True,
                "input_contract": {"worker_outputs": "PlanStepOutput[]"},
                "output_contract": {
                    "verdict": "pass|needs_revision|fail|unknown",
                    "summary": "string",
                    "evidence": "array",
                    "issues": "array",
                },
                "success_criteria": [
                    "区分已验证事实、模型主张和未知项",
                    "指出候选之间的冲突与失败依赖",
                    "不得自行虚构工具或测试结果",
                ],
            },
            {
                "step_key": "synthesizer",
                "sequence": len(selected) + 2,
                "phase": 3,
                "kind": "synthesizer",
                "title": "生成单一最终交付",
                "description": (
                    "根据目标、Worker 输出和 Verifier 结论生成一份最终结果；"
                    "吸收可修正问题并明确保留的风险。"
                ),
                "assigned_model": synthesizer_model,
                "role": "Lead Synthesizer",
                "status": "pending",
                "dependencies": [*worker_keys, "verifier"],
                "allow_failed_dependencies": True,
                "input_contract": {
                    "goal": "AgentGoal",
                    "worker_outputs": "PlanStepOutput[]",
                    "review": "AgentReview",
                },
                "output_contract": {"final_deliverable": "string"},
                "success_criteria": success_criteria,
            },
        ])
        created_steps = self.database.create_agent_plan_steps(
            run_id=run_id,
            session_id=session_id,
            steps=steps,
        )
        approval_required = bool(
            action.get("plan_confirmation_required")
        )
        self.database.initialize_agent_plan(
            run_id=run_id,
            session_id=session_id,
            approval_required=approval_required,
        )
        self.database.append_agent_event(
            run_id,
            "plan_created",
            {
                "strategy": planner_output["strategy"],
                "step_count": len(created_steps),
                "worker_models": selected,
                "verifier_model": verifier_model,
                "synthesizer_model": synthesizer_model,
                "approval_required": approval_required,
                "revision": 1,
            },
        )
        return {"goal": goal, "steps": created_steps}

    def start_plan_step(self, run_id: str, step_id: str) -> dict[str, Any]:
        current = self.database.get_agent_plan_step(step_id)
        if current is None or current["run_id"] != str(run_id):
            raise KeyError("计划步骤不存在")
        step = self.database.update_agent_plan_step(step_id, "running")
        if step is None:
            raise KeyError("计划步骤不存在")
        if step.get("assigned_model"):
            self.database.update_agent_step(
                run_id, str(step["assigned_model"]), "running"
            )
        self.database.append_agent_event(
            run_id,
            "plan_step_started",
            {
                "plan_step_id": step["id"],
                "step_key": step["step_key"],
                "kind": step["kind"],
                "model": step.get("assigned_model"),
                "attempt": step["attempt"],
            },
        )
        return step

    def finish_plan_step(
        self,
        run_id: str,
        step_id: str,
        *,
        status: str,
        content: str = "",
        error: str | None = None,
    ) -> dict[str, Any]:
        current = self.database.get_agent_plan_step(step_id)
        if current is None or current["run_id"] != str(run_id):
            raise KeyError("计划步骤不存在")
        output = {"content": str(content)} if content else None
        safe_output = self._sanitize_persisted_value(output) if output is not None else None
        step = self.database.finish_agent_plan_step(
            step_id,
            status=status,
            output=safe_output,
            output_sha256=self._digest(output) if output is not None else None,
            error=str(error or "")[:2000] or None,
        )
        if step is None:
            raise KeyError("计划步骤不存在")
        if step.get("assigned_model"):
            model_status = status if status in {
                "completed", "failed", "interrupted"
            } else ("failed" if status == "blocked" else "running")
            self.database.update_agent_step(
                run_id,
                str(step["assigned_model"]),
                model_status,
                error=str(error or "")[:2000] or None,
            )
        self.database.append_agent_event(
            run_id,
            f"plan_step_{status}",
            {
                "plan_step_id": step["id"],
                "step_key": step["step_key"],
                "kind": step["kind"],
                "model": step.get("assigned_model"),
                "output_sha256": step.get("output_sha256"),
                "error": step.get("error"),
            },
        )
        return step

    def record_review(
        self,
        *,
        run_id: str,
        plan_step_id: str,
        session_id: str,
        reviewer_model: str,
        review: dict[str, Any],
    ) -> dict[str, Any]:
        verdict = str(review.get("verdict") or "unknown").lower()
        if verdict not in {"pass", "needs_revision", "fail", "unknown"}:
            verdict = "unknown"
        evidence = review.get("evidence")
        issues = review.get("issues")
        safe_evidence = self._sanitize_persisted_value(
            evidence[:50] if isinstance(evidence, list) else []
        )
        safe_issues = self._sanitize_persisted_value(
            issues[:50] if isinstance(issues, list) else []
        )
        item = self.database.create_agent_review(
            run_id=run_id,
            plan_step_id=plan_step_id,
            session_id=session_id,
            reviewer_model=reviewer_model,
            verdict=verdict,
            summary=str(review.get("summary") or "Verifier 未返回可解析摘要")[:20_000],
            evidence=safe_evidence if isinstance(safe_evidence, list) else [],
            issues=safe_issues if isinstance(safe_issues, list) else [],
        )
        self.database.append_agent_event(
            run_id,
            "review_recorded",
            {
                "review_id": item["id"],
                "plan_step_id": plan_step_id,
                "reviewer_model": reviewer_model,
                "verdict": verdict,
            },
        )
        return item

    @staticmethod
    def _bounded_text(
        value: Any,
        *,
        field: str,
        limit: int,
        required: bool = False,
    ) -> str:
        normalized = " ".join(str(value or "").split())
        if required and not normalized:
            raise ValueError(f"{field}不能为空")
        return normalized[:limit]

    def edit_plan(
        self,
        *,
        run_id: str,
        session_id: str,
        expected_revision: int,
        goal_spec: dict[str, Any] | None,
        plan_steps: list[dict[str, Any]] | None,
        change_summary: str = "",
    ) -> dict[str, Any]:
        run = self.database.get_agent_run(run_id, include_events=False)
        if run is None or run["session_id"] != str(session_id):
            raise KeyError("运行记录不存在")
        if (
            run["status"] != "waiting_plan_confirmation"
            or run.get("plan_status") != "draft"
        ):
            raise ValueError("只有等待确认的计划草稿可以编辑")
        if int(run.get("plan_revision") or 0) != int(expected_revision):
            raise RuntimeError("计划版本已变化，请刷新后重试")

        current_goal = run.get("goal") or {}
        raw_goal = goal_spec if isinstance(goal_spec, dict) else {}
        goal = {
            "objective": self._bounded_text(
                raw_goal.get("objective", current_goal.get("objective")),
                field="目标",
                limit=20_000,
                required=True,
            ),
            "success_criteria": self._string_list(
                raw_goal.get(
                    "success_criteria",
                    current_goal.get("success_criteria") or [],
                ),
                limit=12,
                item_limit=1000,
            ),
            "constraints": self._string_list(
                raw_goal.get(
                    "constraints",
                    current_goal.get("constraints") or [],
                ),
                limit=12,
                item_limit=1000,
            ),
            "deliverable": self._bounded_text(
                raw_goal.get("deliverable", current_goal.get("deliverable")),
                field="交付物",
                limit=4000,
                required=True,
            ),
        }
        if not goal["success_criteria"]:
            raise ValueError("计划至少需要一条成功条件")

        current_steps = run.get("plan_steps") or []
        editable_steps = [
            step for step in current_steps if step.get("kind") != "planner"
        ]
        by_id = {str(step["id"]): step for step in editable_steps}
        by_key = {str(step["step_key"]): step for step in current_steps}
        requested = plan_steps if isinstance(plan_steps, list) else []
        requested_by_id: dict[str, dict[str, Any]] = {}
        for item in requested:
            if not isinstance(item, dict):
                raise ValueError("计划步骤更新必须是对象")
            step_id = str(item.get("id") or "")
            if step_id not in by_id:
                raise KeyError("计划步骤不存在或不可编辑")
            if step_id in requested_by_id:
                raise ValueError("计划步骤不能重复提交")
            requested_by_id[step_id] = item

        allowed_models = {
            str(model) for model in run.get("targets") or []
            if str(model).strip()
        }
        normalized_steps: list[dict[str, Any]] = []
        for current in editable_steps:
            patch = requested_by_id.get(str(current["id"]), {})
            assigned_model = self._bounded_text(
                patch.get("assigned_model", current.get("assigned_model")),
                field="执行模型",
                limit=200,
                required=True,
            )
            if assigned_model not in allowed_models:
                raise ValueError(
                    f"计划模型不在本 Run 的可用模型中：{assigned_model}"
                )
            dependencies = self._string_list(
                patch.get("dependencies", current.get("dependencies") or []),
                limit=20,
                item_limit=300,
            )
            for dependency in dependencies:
                parent = by_key.get(dependency)
                if parent is None:
                    raise ValueError(f"计划依赖不存在：{dependency}")
                if int(parent["phase"]) >= int(current["phase"]):
                    raise ValueError(
                        f"计划依赖必须来自更早阶段：{dependency}"
                    )
            criteria = self._string_list(
                patch.get(
                    "success_criteria",
                    current.get("success_criteria") or [],
                ),
                limit=12,
                item_limit=1000,
            )
            if not criteria:
                raise ValueError("每个计划步骤至少需要一条成功条件")
            normalized_steps.append({
                "id": current["id"],
                "title": self._bounded_text(
                    patch.get("title", current.get("title")),
                    field="步骤标题",
                    limit=500,
                    required=True,
                ),
                "description": self._bounded_text(
                    patch.get("description", current.get("description")),
                    field="步骤说明",
                    limit=4000,
                ),
                "assigned_model": assigned_model,
                "role": self._bounded_text(
                    patch.get("role", current.get("role")),
                    field="步骤角色",
                    limit=1000,
                    required=True,
                ),
                "dependencies": dependencies,
                "success_criteria": criteria,
            })

        summary = self._bounded_text(
            change_summary or "用户在执行前编辑计划",
            field="修订说明",
            limit=4000,
        )
        updated = self.database.revise_agent_plan_draft(
            run_id=run_id,
            session_id=session_id,
            expected_revision=int(expected_revision),
            goal=goal,
            step_updates=normalized_steps,
            change_summary=summary,
        )
        self.database.append_agent_event(
            run_id,
            "plan_edited",
            {
                "previous_revision": int(expected_revision),
                "revision": updated.get("plan_revision"),
                "change_summary": summary,
            },
        )
        return updated

    def prepare_confirmed_plan(
        self,
        *,
        run_id: str,
        session_id: str,
        expected_revision: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        approved = self.database.approve_agent_plan(
            run_id=run_id,
            session_id=session_id,
            expected_revision=int(expected_revision),
        )
        assigned_models = list(dict.fromkeys(
            str(step.get("assigned_model"))
            for step in approved.get("plan_steps") or []
            if step.get("assigned_model")
        ))
        if not assigned_models:
            raise ValueError("确认后的计划没有可执行模型")
        action = dict(approved.get("input") or {})
        action["targets"] = assigned_models
        action["_aether_retry"] = True
        action["_plan_confirmed"] = True
        action["_pending_steers"] = self.database.list_agent_steers(run_id)
        self.database.append_agent_event(
            run_id,
            "plan_approved",
            {
                "revision": int(expected_revision),
                "models": assigned_models,
            },
        )
        updated = self.database.get_agent_run(
            run_id, include_events=False
        ) or approved
        return action, updated

    def reject_plan(
        self,
        *,
        run_id: str,
        session_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        rejected = self.database.reject_agent_plan(
            run_id=run_id,
            session_id=session_id,
            expected_revision=int(expected_revision),
        )
        self.database.append_agent_event(
            run_id,
            "plan_rejected",
            {"revision": int(expected_revision)},
        )
        return rejected

    def begin_plan_execution(self, run_id: str) -> dict[str, Any] | None:
        run = self.database.get_agent_run(run_id, include_events=False)
        if run is None:
            return None
        if run.get("plan_status") not in {"approved", "executing"}:
            raise ValueError("计划尚未确认，不能执行")
        if run.get("plan_status") != "executing":
            run = self.database.set_agent_plan_status(run_id, "executing")
            self.database.append_agent_event(
                run_id,
                "plan_execution_started",
                {"revision": (run or {}).get("plan_revision")},
            )
        return run

    def maybe_revise_after_review(
        self,
        *,
        run_id: str,
        session_id: str,
        review: dict[str, Any],
    ) -> dict[str, Any] | None:
        verdict = str(review.get("verdict") or "unknown")
        if verdict not in {"needs_revision", "fail"}:
            return None
        run = self.database.get_agent_run(run_id, include_events=False)
        if run is None or run["session_id"] != str(session_id):
            raise KeyError("运行记录不存在")
        configured = (run.get("input") or {}).get("max_revision_cycles", 1)
        try:
            maximum = max(0, min(2, int(configured)))
        except (TypeError, ValueError):
            maximum = 1
        if int(run.get("automatic_revision_count") or 0) >= maximum:
            return None
        issues = review.get("issues") if isinstance(review.get("issues"), list) else []
        summary = str(review.get("summary") or "").strip()
        change_summary = (
            f"Verifier {verdict}：{summary or '需要返工'}"
            + (
                "；问题：" + "；".join(str(issue)[:500] for issue in issues[:8])
                if issues else ""
            )
        )[:4000]
        updated = self.database.reset_agent_plan_for_review_revision(
            run_id=run_id,
            session_id=session_id,
            change_summary=change_summary,
        )
        self.database.append_agent_event(
            run_id,
            "plan_replanned",
            {
                "revision": updated.get("plan_revision"),
                "automatic_revision_count": updated.get(
                    "automatic_revision_count"
                ),
                "trigger_review_id": review.get("id"),
                "verdict": verdict,
            },
        )
        return updated

    def create(self, session_id: str, action: dict[str, Any]) -> dict[str, Any]:
        repository_id = str(action.get("repository_id") or "").strip()
        if repository_id:
            if self.workspace_service is None:
                raise ValueError("当前 AgentRunService 未配置 Git worktree 服务")
            snapshot = self.workspace_service.snapshot_repository_for_run(
                session_id=str(session_id),
                repository_id=repository_id,
                verification_commands=action.get("verification_commands"),
            )
            action.update(snapshot)
        run = self.database.create_agent_run(session_id, action, self._targets(action))
        self.database.update_agent_run(run["id"], "running")
        self.database.append_agent_event(run["id"], "run_started", {})
        return self.database.get_agent_run(run["id"], include_events=False) or run

    def observe(self, run_id: str, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "unknown")
        model = str(event.get("model") or "").strip()
        if event_type == "delta":
            if model:
                self.database.update_agent_step(run_id, model, "running")
            return

        payload: dict[str, Any] = {"type": event_type}
        for key in ("model", "code", "decision", "approval_id", "budget_remaining"):
            if key in event:
                payload[key] = event[key]
        if event_type == "message_complete":
            message = event.get("message") or {}
            author = str(message.get("author_model") or "")
            role = str(message.get("role") or "")
            partial = bool(event.get("partial"))
            payload.update({
                "author_model": author,
                "role": role,
                "message_id": message.get("id"),
                "partial": partial,
                "content_preview": str(message.get("content") or "")[:1000],
            })
            if role == "assistant" and author and not partial:
                self.database.update_agent_step(run_id, author, "completed")
        elif event_type == "plan_step_output":
            payload.update({
                "partial": bool(event.get("partial")),
                "content_preview": str(event.get("content") or "")[:1000],
            })
        elif event_type == "error":
            error = str(event.get("message") or "运行失败")[:2000]
            payload["message"] = error
            if model:
                self.database.update_agent_step(run_id, model, "failed", error=error)
                if event.get("code") == "tool_result_uncertain":
                    self.database.update_agent_run(run_id, "failed", error=error)
            else:
                self.database.update_agent_run(run_id, "failed", error=error)
        elif event_type == "interrupted" and model:
            self.database.update_agent_step(run_id, model, "interrupted", error="用户中断")
        elif event_type == "approval_required":
            approval = event.get("approval") or {}
            approval_id = str(approval.get("id") or "")
            approval_model = str(approval.get("model") or model)
            if approval_id and approval_model:
                self._approval_models[approval_id] = approval_model
                self.database.update_agent_step(run_id, approval_model, "waiting_approval")
                self.database.update_agent_run(run_id, "waiting_approval")
            payload.update({"approval_id": approval_id, "model": approval_model, "tool_name": approval.get("tool_name")})
        elif event_type == "approval_resolved":
            approval_id = str(event.get("approval_id") or "")
            approval_model = self._approval_models.pop(approval_id, "")
            if approval_model:
                self.database.update_agent_step(run_id, approval_model, "running")
                self.database.update_agent_run(run_id, "running")
        elif event_type == "tool_run":
            tool_run = event.get("run") or {}
            payload.update({
                "tool_name": tool_run.get("tool_name"),
                "status": tool_run.get("status"),
                "tool_run_id": tool_run.get("id"),
            })
        elif event_type in {"tool_call", "tool_result", "tool_result_replayed"}:
            for key in (
                "tool_call_id", "tool_name", "round_index", "call_index",
                "idempotency_key", "status", "tool_run_id",
            ):
                if key in event:
                    payload[key] = event[key]
        elif event_type in {"steer_applied", "checkpoint_saved"}:
            for key in ("steer_id", "checkpoint_id", "kind", "sequence"):
                if key in event:
                    payload[key] = event[key]
        self.database.append_agent_event(run_id, event_type, payload)

    def begin_tool_call(
        self,
        *,
        run_id: str,
        session_id: str,
        model: str,
        provider_type: str,
        provider_model: str,
        provider_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        round_index: int,
        call_index: int,
        plan_step_id: str | None = None,
    ) -> dict[str, Any]:
        step = self.database.update_agent_step(run_id, model, "running")
        identity = {
            "round_index": int(round_index),
            "call_index": int(call_index),
            "plan_step_id": str(plan_step_id or ""),
            "tool_name": str(tool_name),
            "arguments": arguments,
        }
        return self.database.begin_agent_tool_call(
            run_id=run_id,
            step_id=step.get("id") if step else None,
            plan_step_id=plan_step_id,
            session_id=session_id,
            model=model,
            provider_type=provider_type,
            provider_model=provider_model,
            provider_call_id=provider_call_id,
            tool_name=tool_name,
            arguments=self._sanitize_persisted_value(arguments),
            arguments_sha256=self._digest(arguments),
            idempotency_key=self._digest(identity),
            side_effect=self._is_side_effecting_tool(tool_name),
            round_index=round_index,
            call_index=call_index,
        )

    def update_tool_call_status(
        self,
        call_record_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        return self.database.update_agent_tool_call_status(
            call_record_id,
            status,
            error=error,
        )

    def finish_tool_call(
        self,
        call_record_id: str,
        payload: dict[str, Any],
        *,
        tool_run_id: str | None = None,
    ) -> dict[str, Any] | None:
        result = self._sanitize_persisted_value(payload)
        payload_status = str(payload.get("status") or "error")
        status = "completed" if payload_status == "success" else (
            "denied" if payload_status == "denied" else "failed"
        )
        return self.database.finish_agent_tool_call(
            call_record_id,
            status=status,
            result=result,
            result_sha256=self._digest(payload),
            tool_run_id=tool_run_id,
            error=str(payload.get("error") or "")[:2000] or None,
        )

    def save_checkpoint(
        self,
        *,
        run_id: str,
        session_id: str,
        model: str,
        kind: str,
        state: dict[str, Any],
        plan_step_id: str | None = None,
    ) -> dict[str, Any]:
        step = next(
            (item for item in self.database.list_agent_steps(run_id) if item["model"] == model),
            None,
        )
        safe_state = self._sanitize_persisted_value(state)
        checkpoint = self.database.save_agent_checkpoint(
            run_id=run_id,
            step_id=step.get("id") if step else None,
            plan_step_id=plan_step_id,
            session_id=session_id,
            model=model,
            kind=kind,
            state=safe_state,
            state_sha256=self._digest(safe_state),
        )
        self.database.append_agent_event(
            run_id,
            "checkpoint_saved",
            {
                "checkpoint_id": checkpoint["id"],
                "model": model,
                "kind": kind,
                "sequence": checkpoint["sequence"],
                "plan_step_id": plan_step_id,
            },
            step_id=step.get("id") if step else None,
        )
        return checkpoint

    def latest_checkpoint(
        self,
        run_id: str,
        model: str,
        plan_step_id: str | None = None,
    ) -> dict[str, Any] | None:
        return self.database.latest_agent_checkpoint(run_id, model, plan_step_id)

    def queue_steer(
        self,
        *,
        run_id: str,
        session_id: str,
        content: str,
        targets: list[str],
    ) -> dict[str, Any]:
        run = self.database.get_agent_run(run_id, include_events=False)
        if run is None or run["session_id"] != str(session_id):
            raise KeyError("运行记录不存在")
        if run["status"] not in {"running", "waiting_approval"}:
            raise ValueError("当前运行不接受转向消息")
        normalized = str(content).strip()
        if not normalized:
            raise ValueError("转向内容不能为空")
        if len(normalized) > 20_000:
            raise ValueError("转向内容不能超过 20,000 个字符")
        steer = self.database.create_agent_steer(
            run_id=run_id,
            session_id=session_id,
            content=normalized,
            targets=targets,
        )
        self.database.append_agent_event(
            run_id,
            "steer_queued",
            {
                "steer_id": steer["id"],
                "targets": steer["targets"],
                "content_sha256": self._digest(normalized),
                "content_preview": normalized[:500],
            },
        )
        return steer

    def mark_steer_applied(self, run_id: str, steer_id: str, model: str) -> dict[str, Any] | None:
        return self.database.mark_agent_steer_applied(steer_id, model)

    def finish(self, run_id: str, *, disconnected: bool = False) -> dict[str, Any] | None:
        current = self.database.get_agent_run(run_id, include_events=False)
        if current is None:
            return None
        if (
            current["status"] == "waiting_plan_confirmation"
            or current.get("plan_status") == "cancelled"
        ):
            return current
        if disconnected and current["status"] not in {"completed", "failed", "interrupted"}:
            updated = self.database.update_agent_run(run_id, "recoverable", error="连接中断，运行结果未确认")
            self.database.append_agent_event(run_id, "recovery_required", {"reason": "connection_lost"})
            return updated
        plan_steps = current.get("plan_steps") or []
        synthesizer = next(
            (step for step in reversed(plan_steps) if step["kind"] == "synthesizer"),
            None,
        )
        if plan_steps and current["status"] != "failed":
            if synthesizer and synthesizer["status"] == "completed":
                self.database.set_agent_plan_status(run_id, "completed")
                updated = self.database.update_agent_run(run_id, "completed", error=None)
                self.database.update_agent_goal_status(run_id, "completed")
                self.database.append_agent_event(run_id, "run_completed", {})
                return updated
            failed_step = next(
                (
                    step for step in plan_steps
                    if step["status"] in {"failed", "blocked"}
                ),
                None,
            )
            interrupted_step = next(
                (step for step in plan_steps if step["status"] == "interrupted"),
                None,
            )
            if failed_step is not None:
                error = failed_step.get("error") or f"计划步骤 {failed_step['title']} 失败"
                self.database.set_agent_plan_status(run_id, "failed")
                updated = self.database.update_agent_run(run_id, "failed", error=error)
                self.database.update_agent_goal_status(run_id, "failed")
                self.database.append_agent_event(run_id, "run_failed", {"error": error})
                return updated
            if interrupted_step is not None:
                self.database.set_agent_plan_status(run_id, "interrupted")
                updated = self.database.update_agent_run(
                    run_id, "interrupted", error="计划执行被中断"
                )
                self.database.update_agent_goal_status(run_id, "interrupted")
                self.database.append_agent_event(
                    run_id, "run_interrupted", {"error": "计划执行被中断"}
                )
                return updated
            error = "计划尚未完成最终综合步骤"
            self.database.set_agent_plan_status(run_id, "failed")
            updated = self.database.update_agent_run(run_id, "failed", error=error)
            self.database.update_agent_goal_status(run_id, "failed")
            self.database.append_agent_event(run_id, "run_failed", {"error": error})
            return updated
        steps = current.get("steps") or []
        statuses = {step["status"] for step in steps}
        if current["status"] == "failed" or "failed" in statuses:
            status = "failed"
            error = current.get("error") or next((step.get("error") for step in steps if step["status"] == "failed"), None)
        elif "interrupted" in statuses:
            status, error = "interrupted", "运行被中断"
        elif steps and statuses == {"completed"}:
            status, error = "completed", None
        else:
            status, error = "failed", current.get("error") or "运行未产生可确认的完成步骤"
        if plan_steps:
            self.database.set_agent_plan_status(run_id, status)
            self.database.update_agent_goal_status(run_id, status)
        updated = self.database.update_agent_run(run_id, status, error=error)
        self.database.append_agent_event(run_id, f"run_{status}", {"error": error} if error else {})
        return updated

    def prepare_retry(self, run_id: str, session_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        run = self.database.get_agent_run(run_id, include_events=False)
        if run is None or run["session_id"] != str(session_id):
            raise KeyError("运行记录不存在")
        if run["status"] not in RETRYABLE_STATUSES:
            raise ValueError("只有失败、中断或等待恢复的运行可以重试")
        plan_steps = run.get("plan_steps") or []
        if plan_steps:
            retry_targets = list(dict.fromkeys(
                str(step.get("assigned_model"))
                for step in plan_steps
                if step["status"] != "completed" and step.get("assigned_model")
            ))
        else:
            retry_targets = [step["model"] for step in run["steps"] if step["status"] != "completed"]
        if not retry_targets and not run["steps"]:
            retry_targets = list(run.get("targets") or [])
        if not retry_targets:
            raise ValueError("没有可重试的步骤")
        action = dict(run["input"])
        action["targets"] = retry_targets
        action["_aether_retry"] = True
        action["_resume_checkpoints"] = {
            model: checkpoint
            for model in retry_targets
            if (checkpoint := self.database.latest_agent_checkpoint(run_id, model)) is not None
        }
        action["_resume_plan_checkpoints"] = {
            step["id"]: checkpoint
            for step in plan_steps
            if step["status"] != "completed" and step.get("assigned_model")
            if (
                checkpoint := self.database.latest_agent_checkpoint(
                    run_id,
                    str(step["assigned_model"]),
                    str(step["id"]),
                )
            ) is not None
        }
        action["_pending_steers"] = self.database.list_agent_steers(run_id)
        for model in retry_targets:
            self.database.update_agent_step(run_id, model, "pending", error=None)
        if plan_steps:
            self.database.reset_agent_plan_for_retry(run_id)
            self.database.update_agent_goal_status(run_id, "active")
            self.database.set_agent_plan_status(run_id, "executing")
        self.database.update_agent_run(run_id, "running", error=None, increment_retry=True)
        self.database.append_agent_event(run_id, "run_retried", {"targets": retry_targets})
        updated = self.database.get_agent_run(run_id, include_events=False) or run
        return action, updated

    def recover(self) -> int:
        return self.database.recover_incomplete_agent_runs()


agent_run_service = AgentRunService()
