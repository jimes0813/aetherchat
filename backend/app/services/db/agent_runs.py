from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import uuid4

from ._shared import (
    AGENT_RUN_COMPLETION_STATUSES,
    AGENT_RUN_TERMINAL_STATUSES,
    _utc_now,
)


class AgentRunsMixin:
    @staticmethod
    def _decode_agent_goal(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("success_criteria_json", "success_criteria"),
            ("constraints_json", "constraints"),
        ):
            try:
                item[target] = json.loads(item.pop(source))
            except (json.JSONDecodeError, TypeError):
                item[target] = []
        return item

    def create_agent_goal(
        self,
        *,
        run_id: str,
        session_id: str,
        objective: str,
        success_criteria: list[str],
        constraints: list[str],
        deliverable: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_goals
                   (run_id, session_id, objective, success_criteria_json,
                    constraints_json, deliverable, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                   ON CONFLICT(run_id) DO NOTHING""",
                (
                    str(run_id), str(session_id), str(objective),
                    json.dumps(success_criteria, ensure_ascii=False),
                    json.dumps(constraints, ensure_ascii=False),
                    str(deliverable), now, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM agent_goals WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        return self._decode_agent_goal(row)

    def get_agent_goal(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_goals WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        return self._decode_agent_goal(row) if row else None

    def update_agent_goal_status(self, run_id: str, status: str) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE agent_goals SET status = ?, updated_at = ? WHERE run_id = ?",
                (str(status), now, str(run_id)),
            )
        return self.get_agent_goal(run_id)

    @staticmethod
    def _decode_agent_plan_step(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for source, target, fallback in (
            ("dependencies_json", "dependencies", []),
            ("input_contract_json", "input_contract", {}),
            ("output_contract_json", "output_contract", {}),
            ("success_criteria_json", "success_criteria", []),
            ("output_json", "output", None),
        ):
            raw = item.pop(source, None)
            if raw is None:
                item[target] = fallback
                continue
            try:
                item[target] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                item[target] = fallback
        item["allow_failed_dependencies"] = bool(
            item.get("allow_failed_dependencies")
        )
        return item

    def create_agent_plan_steps(
        self,
        *,
        run_id: str,
        session_id: str,
        steps: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            for step in steps:
                step_id = str(step.get("id") or uuid4())
                status = str(step.get("status") or "pending")
                output = step.get("output")
                connection.execute(
                    """INSERT INTO agent_plan_steps
                       (id, run_id, session_id, step_key, sequence, phase, kind,
                        title, description, assigned_model, role, status,
                        dependencies_json, allow_failed_dependencies,
                        input_contract_json, output_contract_json,
                        success_criteria_json, output_json, output_sha256,
                        error, attempt, created_at, updated_at, started_at,
                        completed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(run_id, step_key) DO NOTHING""",
                    (
                        step_id, str(run_id), str(session_id),
                        str(step.get("step_key") or step_id),
                        int(step.get("sequence") or 0),
                        int(step.get("phase") or 0),
                        str(step.get("kind") or "worker"),
                        str(step.get("title") or "未命名步骤"),
                        str(step.get("description") or ""),
                        str(step.get("assigned_model") or "") or None,
                        str(step.get("role") or ""),
                        status,
                        json.dumps(step.get("dependencies") or [], ensure_ascii=False),
                        1 if step.get("allow_failed_dependencies") else 0,
                        json.dumps(step.get("input_contract") or {}, ensure_ascii=False),
                        json.dumps(step.get("output_contract") or {}, ensure_ascii=False),
                        json.dumps(step.get("success_criteria") or [], ensure_ascii=False),
                        (
                            json.dumps(output, ensure_ascii=False, default=str)
                            if output is not None else None
                        ),
                        step.get("output_sha256"),
                        step.get("error"),
                        int(step.get("attempt") or 0),
                        now, now,
                        now if status == "running" else None,
                        now if status in {"completed", "failed", "skipped"} else None,
                    ),
                )
        return self.list_agent_plan_steps(run_id)

    def list_agent_plan_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_plan_steps WHERE run_id = ?
                   ORDER BY phase, sequence, created_at""",
                (str(run_id),),
            ).fetchall()
        return [self._decode_agent_plan_step(row) for row in rows]

    def get_agent_plan_step(self, step_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_plan_steps WHERE id = ?", (str(step_id),)
            ).fetchone()
        return self._decode_agent_plan_step(row) if row else None

    def _plan_snapshot_in_connection(
        cls,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        goal_row = connection.execute(
            "SELECT * FROM agent_goals WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        step_rows = connection.execute(
            """SELECT * FROM agent_plan_steps WHERE run_id = ?
               ORDER BY phase, sequence, created_at""",
            (str(run_id),),
        ).fetchall()
        goal = cls._decode_agent_goal(goal_row) if goal_row else {}
        steps = [cls._decode_agent_plan_step(row) for row in step_rows]
        return goal, steps

    @staticmethod
    def _insert_plan_revision_in_connection(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        session_id: str,
        revision: int,
        source: str,
        status: str,
        change_summary: str,
        goal: dict[str, Any],
        steps: list[dict[str, Any]],
        created_at: str,
        approved_at: str | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO agent_plan_revisions
               (id, run_id, session_id, revision, source, status,
                change_summary, goal_json, steps_json, created_at, approved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id, revision) DO NOTHING""",
            (
                str(uuid4()), str(run_id), str(session_id), int(revision),
                str(source), str(status), str(change_summary)[:4000],
                json.dumps(goal, ensure_ascii=False, default=str),
                json.dumps(steps, ensure_ascii=False, default=str),
                created_at, approved_at,
            ),
        )

    @staticmethod
    def _decode_agent_plan_revision(
        row: sqlite3.Row | dict[str, Any],
    ) -> dict[str, Any]:
        item = dict(row)
        for source, target, fallback in (
            ("goal_json", "goal", {}),
            ("steps_json", "steps", []),
        ):
            try:
                item[target] = json.loads(item.pop(source))
            except (json.JSONDecodeError, TypeError):
                item[target] = fallback
        return item

    def initialize_agent_plan(
        self,
        *,
        run_id: str,
        session_id: str,
        approval_required: bool,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        plan_status = "draft" if approval_required else "approved"
        approved_at = None if approval_required else now
        with self._lock, self._connect() as connection:
            run = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ? AND session_id = ?",
                (str(run_id), str(session_id)),
            ).fetchone()
            if run is None:
                return None
            if int(run["plan_revision"] or 0) > 0:
                return self.get_agent_run(run_id, include_events=False)
            goal, steps = self._plan_snapshot_in_connection(connection, run_id)
            if not goal or not steps:
                raise ValueError("计划快照缺少目标或步骤")
            connection.execute(
                """UPDATE agent_runs
                   SET plan_status = ?, plan_revision = 1,
                       plan_approval_required = ?, plan_approved_at = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (
                    plan_status, 1 if approval_required else 0,
                    approved_at, now, str(run_id),
                ),
            )
            self._insert_plan_revision_in_connection(
                connection,
                run_id=run_id,
                session_id=session_id,
                revision=1,
                source="planner",
                status=plan_status,
                change_summary="确定性 Planner 创建初始计划",
                goal=goal,
                steps=steps,
                created_at=now,
                approved_at=approved_at,
            )
        return self.get_agent_run(run_id, include_events=False)

    def list_agent_plan_revisions(
        self,
        run_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(200, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_plan_revisions WHERE run_id = ?
                   ORDER BY revision DESC LIMIT ?""",
                (str(run_id), bounded),
            ).fetchall()
        return [self._decode_agent_plan_revision(row) for row in rows]

    def revise_agent_plan_draft(
        self,
        *,
        run_id: str,
        session_id: str,
        expected_revision: int,
        goal: dict[str, Any],
        step_updates: list[dict[str, Any]],
        change_summary: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            run = connection.execute(
                """SELECT * FROM agent_runs
                   WHERE id = ? AND session_id = ?""",
                (str(run_id), str(session_id)),
            ).fetchone()
            if run is None:
                raise KeyError("运行记录不存在")
            if (
                run["status"] != "waiting_plan_confirmation"
                or run["plan_status"] != "draft"
            ):
                raise ValueError("只有等待确认的计划草稿可以编辑")
            if int(run["plan_revision"] or 0) != int(expected_revision):
                raise RuntimeError("计划版本已变化，请刷新后重试")
            connection.execute(
                """UPDATE agent_goals
                   SET objective = ?, success_criteria_json = ?,
                       constraints_json = ?, deliverable = ?,
                       updated_at = ?
                   WHERE run_id = ?""",
                (
                    str(goal["objective"]),
                    json.dumps(goal["success_criteria"], ensure_ascii=False),
                    json.dumps(goal["constraints"], ensure_ascii=False),
                    str(goal["deliverable"]),
                    now, str(run_id),
                ),
            )
            for step in step_updates:
                cursor = connection.execute(
                    """UPDATE agent_plan_steps
                       SET title = ?, description = ?, assigned_model = ?,
                           role = ?, dependencies_json = ?,
                           success_criteria_json = ?, updated_at = ?
                       WHERE id = ? AND run_id = ? AND kind != 'planner'""",
                    (
                        str(step["title"]), str(step["description"]),
                        str(step.get("assigned_model") or "") or None,
                        str(step["role"]),
                        json.dumps(step["dependencies"], ensure_ascii=False),
                        json.dumps(step["success_criteria"], ensure_ascii=False),
                        now, str(step["id"]), str(run_id),
                    ),
                )
                if cursor.rowcount != 1:
                    raise KeyError("计划步骤不存在或不可编辑")
            revision = int(expected_revision) + 1
            connection.execute(
                """UPDATE agent_runs
                   SET plan_revision = ?, plan_status = 'draft',
                       plan_approved_at = NULL, updated_at = ?
                   WHERE id = ?""",
                (revision, now, str(run_id)),
            )
            snapshot_goal, snapshot_steps = self._plan_snapshot_in_connection(
                connection, run_id
            )
            self._insert_plan_revision_in_connection(
                connection,
                run_id=run_id,
                session_id=session_id,
                revision=revision,
                source="user_edit",
                status="draft",
                change_summary=change_summary,
                goal=snapshot_goal,
                steps=snapshot_steps,
                created_at=now,
            )
        updated = self.get_agent_run(run_id, include_events=False)
        if updated is None:
            raise KeyError("运行记录不存在")
        return updated

    def approve_agent_plan(
        self,
        *,
        run_id: str,
        session_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            run = connection.execute(
                """SELECT * FROM agent_runs
                   WHERE id = ? AND session_id = ?""",
                (str(run_id), str(session_id)),
            ).fetchone()
            if run is None:
                raise KeyError("运行记录不存在")
            if (
                run["status"] != "waiting_plan_confirmation"
                or run["plan_status"] != "draft"
            ):
                raise ValueError("当前计划不在等待确认状态")
            if int(run["plan_revision"] or 0) != int(expected_revision):
                raise RuntimeError("计划版本已变化，请刷新后重试")
            connection.execute(
                """UPDATE agent_runs
                   SET status = 'running', plan_status = 'approved',
                       plan_approved_at = ?, error = NULL, updated_at = ?,
                       started_at = COALESCE(started_at, ?),
                       completed_at = NULL
                   WHERE id = ?""",
                (now, now, now, str(run_id)),
            )
            connection.execute(
                """UPDATE agent_plan_revisions
                   SET status = 'approved', approved_at = ?
                   WHERE run_id = ? AND revision = ?""",
                (now, str(run_id), int(expected_revision)),
            )
        updated = self.get_agent_run(run_id, include_events=False)
        if updated is None:
            raise KeyError("运行记录不存在")
        return updated

    def set_agent_plan_status(
        self,
        run_id: str,
        plan_status: str,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_runs SET plan_status = ?, updated_at = ?
                   WHERE id = ?""",
                (str(plan_status), now, str(run_id)),
            )
        return self.get_agent_run(run_id, include_events=False)

    def reject_agent_plan(
        self,
        *,
        run_id: str,
        session_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            run = connection.execute(
                """SELECT * FROM agent_runs
                   WHERE id = ? AND session_id = ?""",
                (str(run_id), str(session_id)),
            ).fetchone()
            if run is None:
                raise KeyError("运行记录不存在")
            if (
                run["status"] != "waiting_plan_confirmation"
                or run["plan_status"] != "draft"
            ):
                raise ValueError("当前计划不在等待确认状态")
            if int(run["plan_revision"] or 0) != int(expected_revision):
                raise RuntimeError("计划版本已变化，请刷新后重试")
            connection.execute(
                """UPDATE agent_runs
                   SET status = 'interrupted', plan_status = 'cancelled',
                       error = '用户拒绝执行计划', updated_at = ?,
                       completed_at = ?
                   WHERE id = ?""",
                (now, now, str(run_id)),
            )
            connection.execute(
                """UPDATE agent_goals
                   SET status = 'interrupted', updated_at = ?
                   WHERE run_id = ?""",
                (now, str(run_id)),
            )
            connection.execute(
                """UPDATE agent_plan_steps
                   SET status = 'skipped', error = '用户拒绝执行计划',
                       updated_at = ?, completed_at = ?
                   WHERE run_id = ? AND status IN ('pending', 'ready')""",
                (now, now, str(run_id)),
            )
            connection.execute(
                """UPDATE agent_plan_revisions
                   SET status = 'cancelled'
                   WHERE run_id = ? AND revision = ?""",
                (str(run_id), int(expected_revision)),
            )
        updated = self.get_agent_run(run_id, include_events=False)
        if updated is None:
            raise KeyError("运行记录不存在")
        return updated

    def update_agent_plan_step(
        self,
        step_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM agent_plan_steps WHERE id = ?", (str(step_id),)
            ).fetchone()
            if row is None:
                return None
            starting = status == "running" and row["status"] != "running"
            completed_at = (
                now if status in {"completed", "failed", "interrupted", "blocked", "skipped"}
                else None
            )
            connection.execute(
                """UPDATE agent_plan_steps
                   SET status = ?, error = ?, updated_at = ?,
                       attempt = attempt + ?,
                       started_at = CASE WHEN ? THEN ? ELSE started_at END,
                       completed_at = ?
                   WHERE id = ?""",
                (
                    str(status), error, now, 1 if starting else 0,
                    1 if starting else 0, now, completed_at, str(step_id),
                ),
            )
        return self.get_agent_plan_step(step_id)

    def finish_agent_plan_step(
        self,
        step_id: str,
        *,
        status: str,
        output: dict[str, Any] | None,
        output_sha256: str | None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_plan_steps
                   SET status = ?, output_json = ?, output_sha256 = ?,
                       error = ?, updated_at = ?, completed_at = ?
                   WHERE id = ?""",
                (
                    str(status),
                    (
                        json.dumps(output, ensure_ascii=False, default=str)
                        if output is not None else None
                    ),
                    output_sha256, error, now, now, str(step_id),
                ),
            )
        return self.get_agent_plan_step(step_id)

    def reset_agent_plan_for_retry(self, run_id: str) -> list[dict[str, Any]]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_plan_steps
                   SET status = CASE
                           WHEN kind = 'planner' THEN 'completed'
                           ELSE 'pending'
                       END,
                       output_json = CASE WHEN kind = 'planner' THEN output_json ELSE NULL END,
                       output_sha256 = CASE WHEN kind = 'planner' THEN output_sha256 ELSE NULL END,
                       error = NULL, updated_at = ?, started_at = NULL,
                       completed_at = CASE WHEN kind = 'planner' THEN completed_at ELSE NULL END
                   WHERE run_id = ? AND status != 'completed'""",
                (now, str(run_id)),
            )
        return self.list_agent_plan_steps(run_id)

    def reset_agent_plan_for_review_revision(
        self,
        *,
        run_id: str,
        session_id: str,
        change_summary: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            run = connection.execute(
                """SELECT * FROM agent_runs
                   WHERE id = ? AND session_id = ?""",
                (str(run_id), str(session_id)),
            ).fetchone()
            if run is None:
                raise KeyError("运行记录不存在")
            if run["plan_status"] != "executing":
                raise ValueError("只有执行中的计划可以进入自动修订")
            revision = int(run["plan_revision"] or 0) + 1
            automatic_revision_count = int(
                run["automatic_revision_count"] or 0
            ) + 1
            connection.execute(
                """UPDATE agent_plan_steps
                   SET status = CASE
                           WHEN kind = 'planner' THEN 'completed'
                           ELSE 'pending'
                       END,
                       output_json = CASE
                           WHEN kind IN ('planner', 'worker') THEN output_json
                           ELSE NULL
                       END,
                       output_sha256 = CASE
                           WHEN kind IN ('planner', 'worker') THEN output_sha256
                           ELSE NULL
                       END,
                       error = NULL, updated_at = ?, started_at = NULL,
                       completed_at = CASE
                           WHEN kind = 'planner' THEN completed_at
                           ELSE NULL
                       END
                   WHERE run_id = ?""",
                (now, str(run_id)),
            )
            connection.execute(
                """UPDATE agent_steps
                   SET status = 'pending', error = NULL, updated_at = ?,
                       started_at = NULL, completed_at = NULL
                   WHERE run_id = ?""",
                (now, str(run_id)),
            )
            connection.execute(
                """UPDATE agent_runs
                   SET plan_revision = ?, automatic_revision_count = ?,
                       plan_status = 'executing', updated_at = ?
                   WHERE id = ?""",
                (
                    revision, automatic_revision_count, now, str(run_id),
                ),
            )
            goal, steps = self._plan_snapshot_in_connection(connection, run_id)
            self._insert_plan_revision_in_connection(
                connection,
                run_id=run_id,
                session_id=session_id,
                revision=revision,
                source="verifier_replan",
                status="executing",
                change_summary=change_summary,
                goal=goal,
                steps=steps,
                created_at=now,
            )
        updated = self.get_agent_run(run_id, include_events=False)
        if updated is None:
            raise KeyError("运行记录不存在")
        return updated

    @staticmethod
    def _decode_agent_review(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("evidence_json", "evidence"),
            ("issues_json", "issues"),
        ):
            try:
                item[target] = json.loads(item.pop(source))
            except (json.JSONDecodeError, TypeError):
                item[target] = []
        return item

    def create_agent_review(
        self,
        *,
        run_id: str,
        plan_step_id: str | None = None,
        session_id: str,
        reviewer_model: str,
        verdict: str,
        summary: str,
        evidence: list[Any],
        issues: list[Any],
    ) -> dict[str, Any]:
        review_id = str(uuid4())
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_reviews
                   (id, run_id, plan_step_id, session_id, reviewer_model,
                    verdict, summary, evidence_json, issues_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    review_id, str(run_id), plan_step_id, str(session_id),
                    str(reviewer_model), str(verdict), str(summary)[:20_000],
                    json.dumps(evidence, ensure_ascii=False, default=str),
                    json.dumps(issues, ensure_ascii=False, default=str), now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM agent_reviews WHERE id = ?", (review_id,)
            ).fetchone()
        return self._decode_agent_review(row)

    def list_agent_reviews(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_reviews WHERE run_id = ?
                   ORDER BY created_at, rowid""",
                (str(run_id),),
            ).fetchall()
        return [self._decode_agent_review(row) for row in rows]

    @staticmethod
    def _decode_agent_run(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for source, target in (("input_json", "input"), ("targets_json", "targets")):
            try:
                item[target] = json.loads(item.pop(source))
            except (json.JSONDecodeError, TypeError):
                item[target] = {} if target == "input" else []
        item["plan_approval_required"] = bool(
            item.get("plan_approval_required")
        )
        raw_action = item.get("input")
        action: dict[str, Any] = raw_action if isinstance(raw_action, dict) else {}
        effective_strategy = str(
            action.get("effective_execution_strategy")
            or action.get("execution_strategy")
            or "direct"
        )
        strategy_source = str(
            action.get("execution_strategy_source")
            or (
                "explicit"
                if action.get("execution_strategy") is not None
                else "compatibility_default"
            )
        )
        item["effective_execution_strategy"] = effective_strategy
        item["execution_strategy_source"] = strategy_source
        effect_count = int(item.get("effect_count") or 0)
        verified_effect_count = int(item.get("verified_effect_count") or 0)
        failed_effect_count = int(item.get("failed_effect_count") or 0)
        uncertain_effect_count = int(item.get("uncertain_effect_count") or 0)
        unverified_effect_count = int(item.get("unverified_effect_count") or 0)
        problem_tool_count = int(item.get("problem_tool_count") or 0)
        pending_tool_count = int(item.get("pending_tool_count") or 0)
        if effect_count == 0:
            effect_verification_status = "not_applicable"
        elif uncertain_effect_count:
            effect_verification_status = "uncertain"
        elif failed_effect_count:
            effect_verification_status = "failed"
        elif verified_effect_count == effect_count:
            effect_verification_status = "verified"
        else:
            effect_verification_status = "unverified"
        item["effect_count"] = effect_count
        item["verified_effect_count"] = verified_effect_count
        item["failed_effect_count"] = failed_effect_count
        item["uncertain_effect_count"] = uncertain_effect_count
        item["unverified_effect_count"] = unverified_effect_count
        item["problem_tool_count"] = problem_tool_count
        item["pending_tool_count"] = pending_tool_count
        item["effect_verification_status"] = effect_verification_status
        stored_status = str(item.get("status") or "")
        item["outcome_status"] = (
            AgentRunsMixin.completion_status_for_evidence(item)
            if stored_status == "completed"
            else stored_status
        )
        item["completion_summary"] = AgentRunsMixin.completion_summary(item)
        return item

    @staticmethod
    def completion_status_for_evidence(run: dict[str, Any]) -> str:
        """Classify a structurally complete run from deterministic execution evidence."""
        effect_count = int(run.get("effect_count") or 0)
        verified_effect_count = int(run.get("verified_effect_count") or 0)
        failed_effect_count = int(run.get("failed_effect_count") or 0)
        uncertain_effect_count = int(run.get("uncertain_effect_count") or 0)
        unverified_effect_count = int(run.get("unverified_effect_count") or 0)
        problem_tool_count = int(run.get("problem_tool_count") or 0)
        pending_tool_count = int(run.get("pending_tool_count") or 0)
        tool_call_count = int(run.get("tool_call_count") or 0)

        if uncertain_effect_count or unverified_effect_count or pending_tool_count:
            return "uncertain"
        if failed_effect_count or problem_tool_count:
            return "completed_with_warnings"
        if tool_call_count == 0:
            return "completed_with_warnings"
        if effect_count and verified_effect_count != effect_count:
            return "uncertain"
        return "completed_verified"

    @staticmethod
    def completion_summary(run: dict[str, Any]) -> str | None:
        stored_status = str(run.get("status") or "")
        outcome_status = str(run.get("outcome_status") or stored_status)
        if stored_status not in AGENT_RUN_COMPLETION_STATUSES:
            return None
        if outcome_status == "completed_verified":
            return "工具执行证据完整；所有副作用均已核验。"
        if outcome_status == "uncertain":
            return "输出已生成，但仍有未核验、不确定或尚未结束的执行副作用。"
        if int(run.get("problem_tool_count") or 0) or int(
            run.get("failed_effect_count") or 0
        ):
            return "输出已生成，但存在失败、拒绝或核验失败的工具结果。"
        return "回答已生成，但没有形成可独立核验的工具执行证据。"

    def create_agent_run(self, session_id: str, action: dict[str, Any], targets: list[str]) -> dict[str, Any]:
        self.ensure_session(session_id, mode=str(action.get("mode") or "project"))
        now = _utc_now()
        run_id = str(uuid4())
        session_row = self.get_session(session_id) or {}
        project_id = (
            str(action.get("project_id") or "").strip()
            or str(session_row.get("project_id") or "").strip()
            or None
        )
        safe_action = {
            key: value for key, value in action.items()
            if key in {
                "content", "targets", "mode", "language", "attachment_ids",
                "requested_execution_strategy", "execution_strategy",
                "effective_execution_strategy", "execution_strategy_source",
                "goal_spec",
                "plan_confirmation_required", "max_revision_cycles",
                "repository_id", "repository_name",
                "repository_base_commit", "repository_branch",
                "verification_commands",
                "project_id",
            }
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_runs
                   (id, session_id, status, mode, project_id, input_json, targets_json, created_at, updated_at)
                   VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, str(session_id), str(action.get("mode") or "project"),
                    project_id,
                    json.dumps(safe_action, ensure_ascii=False),
                    json.dumps(list(dict.fromkeys(targets)), ensure_ascii=False), now, now,
                ),
            )
        self.append_agent_event(
            run_id,
            "run_created",
            {"targets": targets, "project_id": project_id},
        )
        return self.get_agent_run(run_id, include_events=False) or {}

    def list_agent_runs(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(500, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT agent_runs.*,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id) AS tool_call_count,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id
                             AND agent_tool_calls.side_effect = 1) AS effect_count,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id
                             AND agent_tool_calls.side_effect = 1
                             AND agent_tool_calls.verification_status = 'verified') AS verified_effect_count,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id
                             AND agent_tool_calls.side_effect = 1
                             AND agent_tool_calls.verification_status = 'failed') AS failed_effect_count,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id
                             AND agent_tool_calls.side_effect = 1
                             AND agent_tool_calls.verification_status = 'uncertain') AS uncertain_effect_count,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id
                             AND agent_tool_calls.side_effect = 1
                             AND agent_tool_calls.verification_status IN ('pending', 'unverified')) AS unverified_effect_count,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id
                             AND (
                               agent_tool_calls.status IN ('failed', 'denied', 'uncertain')
                               OR agent_tool_calls.verification_status = 'failed'
                             )) AS problem_tool_count,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id
                             AND agent_tool_calls.status IN ('pending', 'waiting_approval', 'running')) AS pending_tool_count,
                          (SELECT COUNT(*) FROM agent_checkpoints
                           WHERE agent_checkpoints.run_id = agent_runs.id) AS checkpoint_count,
                          (SELECT COUNT(*) FROM agent_steers
                           WHERE agent_steers.run_id = agent_runs.id) AS steer_count,
                          (SELECT COUNT(*) FROM agent_plan_steps
                           WHERE agent_plan_steps.run_id = agent_runs.id) AS plan_step_count,
                          (SELECT COUNT(*) FROM agent_plan_steps
                           WHERE agent_plan_steps.run_id = agent_runs.id
                             AND agent_plan_steps.status = 'completed') AS completed_plan_step_count,
                          (SELECT COUNT(*) FROM agent_reviews
                           WHERE agent_reviews.run_id = agent_runs.id) AS review_count
                   FROM agent_runs
                   WHERE session_id = ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (str(session_id), bounded),
            ).fetchall()
        result = []
        for row in rows:
            run = self._decode_agent_run(row)
            run["steps"] = self.list_agent_steps(run["id"])
            goal = self.get_agent_goal(run["id"])
            if goal is not None and run["status"] != "waiting_plan_confirmation":
                goal = {
                    **goal,
                    "objective": str(goal.get("objective") or "")[:2000],
                    "deliverable": str(goal.get("deliverable") or "")[:1000],
                    "success_criteria": [],
                    "constraints": [],
                }
            run["goal"] = goal
            run["plan_steps"] = [
                {**step, "output": None}
                for step in self.list_agent_plan_steps(run["id"])
            ]
            reviews = self.list_agent_reviews(run["id"])
            run["reviews"] = [
                {
                    **review,
                    "summary": str(review.get("summary") or "")[:2000],
                    "evidence": [],
                    "issues": [],
                }
                for review in reviews[-1:]
            ]
            result.append(run)
        return result

    def get_agent_run(self, run_id: str, *, include_events: bool = True) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT agent_runs.*,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id) AS tool_call_count,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id
                             AND agent_tool_calls.side_effect = 1) AS effect_count,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id
                             AND agent_tool_calls.side_effect = 1
                             AND agent_tool_calls.verification_status = 'verified') AS verified_effect_count,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id
                             AND agent_tool_calls.side_effect = 1
                             AND agent_tool_calls.verification_status = 'failed') AS failed_effect_count,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id
                             AND agent_tool_calls.side_effect = 1
                             AND agent_tool_calls.verification_status = 'uncertain') AS uncertain_effect_count,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id
                             AND agent_tool_calls.side_effect = 1
                             AND agent_tool_calls.verification_status IN ('pending', 'unverified')) AS unverified_effect_count,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id
                             AND (
                               agent_tool_calls.status IN ('failed', 'denied', 'uncertain')
                               OR agent_tool_calls.verification_status = 'failed'
                             )) AS problem_tool_count,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id
                             AND agent_tool_calls.status IN ('pending', 'waiting_approval', 'running')) AS pending_tool_count,
                          (SELECT COUNT(*) FROM agent_checkpoints
                           WHERE agent_checkpoints.run_id = agent_runs.id) AS checkpoint_count,
                          (SELECT COUNT(*) FROM agent_steers
                           WHERE agent_steers.run_id = agent_runs.id) AS steer_count,
                          (SELECT COUNT(*) FROM agent_plan_steps
                           WHERE agent_plan_steps.run_id = agent_runs.id) AS plan_step_count,
                          (SELECT COUNT(*) FROM agent_plan_steps
                           WHERE agent_plan_steps.run_id = agent_runs.id
                             AND agent_plan_steps.status = 'completed') AS completed_plan_step_count,
                          (SELECT COUNT(*) FROM agent_reviews
                           WHERE agent_reviews.run_id = agent_runs.id) AS review_count
                   FROM agent_runs WHERE id = ?""",
                (str(run_id),),
            ).fetchone()
        if row is None:
            return None
        run = self._decode_agent_run(row)
        run["steps"] = self.list_agent_steps(run["id"])
        run["goal"] = self.get_agent_goal(run["id"])
        run["plan_steps"] = self.list_agent_plan_steps(run["id"])
        run["reviews"] = self.list_agent_reviews(run["id"])
        if include_events:
            run["plan_revisions"] = self.list_agent_plan_revisions(run["id"])
            run["events"] = self.list_agent_events(run["id"])
            run["tool_calls"] = self.list_agent_tool_calls(run["id"])
            run["checkpoints"] = self.list_agent_checkpoints(run["id"])
            run["steers"] = self.list_agent_steers(run["id"])
            run["tool_call_count"] = len(run["tool_calls"])
            run["checkpoint_count"] = len(run["checkpoints"])
            run["steer_count"] = len(run["steers"])
            run["plan_step_count"] = len(run["plan_steps"])
            run["completed_plan_step_count"] = len([
                step for step in run["plan_steps"] if step["status"] == "completed"
            ])
            run["review_count"] = len(run["reviews"])
            run["plan_revision_count"] = len(run["plan_revisions"])
        return run

    def update_agent_run(self, run_id: str, status: str, *, error: str | None = None, increment_retry: bool = False) -> dict[str, Any] | None:
        now = _utc_now()
        started_at = now if status == "running" else None
        completed_at = now if status in AGENT_RUN_TERMINAL_STATUSES else None
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_runs SET status = ?, error = ?, updated_at = ?,
                   retry_count = retry_count + ?,
                   started_at = CASE WHEN ? IS NOT NULL THEN ? ELSE started_at END,
                   completed_at = ? WHERE id = ?""",
                (status, error, now, 1 if increment_retry else 0, started_at, started_at, completed_at, str(run_id)),
            )
        return self.get_agent_run(run_id, include_events=False)

    def list_agent_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_steps WHERE run_id = ? ORDER BY created_at, model", (str(run_id),)
            ).fetchall()
        return [dict(row) for row in rows]

    def update_agent_step(self, run_id: str, model: str, status: str, *, error: str | None = None) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT id, status FROM agent_steps WHERE run_id = ? AND model = ?", (str(run_id), str(model))
            ).fetchone()
            if row is None:
                run = connection.execute("SELECT session_id FROM agent_runs WHERE id = ?", (str(run_id),)).fetchone()
                if run is None:
                    return None
                step_id = str(uuid4())
                connection.execute(
                    """INSERT INTO agent_steps
                       (id, run_id, session_id, model, status, attempt, error, created_at, updated_at, started_at, completed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        step_id, str(run_id), run["session_id"], str(model), status,
                        1 if status == "running" else 0, error, now, now,
                        now if status == "running" else None,
                        now if status in {"completed", "failed", "interrupted"} else None,
                    ),
                )
            else:
                starting = status == "running" and row["status"] != "running"
                connection.execute(
                    """UPDATE agent_steps SET status = ?, error = ?, updated_at = ?,
                       attempt = attempt + ?,
                       started_at = CASE WHEN ? THEN ? ELSE started_at END,
                       completed_at = ? WHERE id = ?""",
                    (
                        status, error, now, 1 if starting else 0, 1 if starting else 0,
                        now, now if status in {"completed", "failed", "interrupted"} else None, row["id"],
                    ),
                )
            updated = connection.execute(
                "SELECT * FROM agent_steps WHERE run_id = ? AND model = ?", (str(run_id), str(model))
            ).fetchone()
        return dict(updated) if updated else None

    def append_agent_event(self, run_id: str, event_type: str, payload: dict[str, Any], step_id: str | None = None) -> dict[str, Any]:
        event = {
            "id": str(uuid4()), "run_id": str(run_id), "step_id": step_id,
            "event_type": str(event_type), "payload": payload, "created_at": _utc_now(),
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_events (id, run_id, step_id, event_type, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    event["id"], event["run_id"], step_id, event["event_type"],
                    json.dumps(payload, ensure_ascii=False, default=str)[:100_000], event["created_at"],
                ),
            )
        return event

    def list_agent_events(self, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        bounded = max(1, min(2000, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_events WHERE run_id = ?
                   ORDER BY created_at, rowid LIMIT ?""", (str(run_id), bounded)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.pop("payload_json"))
            except (json.JSONDecodeError, TypeError):
                item["payload"] = {}
            result.append(item)
        return result

    @staticmethod
    def _decode_agent_tool_call(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for source, target, fallback in (
            ("arguments_json", "arguments", {}),
            ("result_json", "result", None),
            ("evidence_json", "evidence", []),
        ):
            raw = item.pop(source, None)
            if raw is None:
                item[target] = fallback
                continue
            try:
                item[target] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                item[target] = fallback
        item["side_effect"] = bool(item.get("side_effect"))
        return item

    def begin_agent_tool_call(
        self,
        *,
        run_id: str,
        step_id: str | None,
        plan_step_id: str | None = None,
        session_id: str,
        model: str,
        provider_type: str,
        provider_model: str,
        provider_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        arguments_sha256: str,
        idempotency_key: str,
        side_effect: bool,
        round_index: int,
        call_index: int,
    ) -> dict[str, Any]:
        now = _utc_now()
        arguments_json = json.dumps(arguments, ensure_ascii=False, default=str)
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                """SELECT * FROM agent_tool_calls
                   WHERE run_id = ? AND model = ? AND idempotency_key = ?""",
                (str(run_id), str(model), str(idempotency_key)),
            ).fetchone()
            if existing is not None:
                current = self._decode_agent_tool_call(existing)
                if side_effect and current["status"] == "completed":
                    current["disposition"] = "replay"
                    return current
                if side_effect and current["status"] in {
                    "pending", "waiting_approval", "running", "failed", "uncertain",
                }:
                    error = "上次副作用工具执行结果未确认；为避免重复执行，需人工检查后再处理。"
                    connection.execute(
                        """UPDATE agent_tool_calls
                           SET status = 'uncertain', verification_status = 'uncertain',
                               error = ?, updated_at = ?
                           WHERE id = ?""",
                        (error, now, existing["id"]),
                    )
                    updated = connection.execute(
                        "SELECT * FROM agent_tool_calls WHERE id = ?", (existing["id"],)
                    ).fetchone()
                    item = self._decode_agent_tool_call(updated)
                    item["disposition"] = "uncertain"
                    return item
                connection.execute(
                    """UPDATE agent_tool_calls
                       SET step_id = ?, plan_step_id = ?,
                           provider_type = ?, provider_model = ?,
                           provider_call_id = ?, tool_name = ?, arguments_json = ?,
                           arguments_sha256 = ?, side_effect = ?, status = 'pending',
                           attempt = attempt + 1, round_index = ?, call_index = ?,
                           tool_run_id = NULL, result_json = NULL, result_sha256 = NULL,
                           verification_status = ?, evidence_json = '[]', verified_at = NULL,
                           error = NULL, updated_at = ?, completed_at = NULL
                       WHERE id = ?""",
                    (
                        step_id, plan_step_id, str(provider_type), str(provider_model),
                        str(provider_call_id), str(tool_name), arguments_json,
                        str(arguments_sha256), 1 if side_effect else 0,
                        int(round_index), int(call_index),
                        "pending" if side_effect else "not_applicable",
                        now, existing["id"],
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM agent_tool_calls WHERE id = ?", (existing["id"],)
                ).fetchone()
            else:
                call_id = str(uuid4())
                connection.execute(
                    """INSERT INTO agent_tool_calls
                       (id, run_id, step_id, plan_step_id, session_id, model, provider_type,
                        provider_model, provider_call_id, tool_name, arguments_json,
                        arguments_sha256, idempotency_key, side_effect, status,
                        attempt, round_index, call_index, verification_status,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending',
                               1, ?, ?, ?, ?, ?)""",
                    (
                        call_id, str(run_id), step_id, plan_step_id,
                        str(session_id), str(model),
                        str(provider_type), str(provider_model), str(provider_call_id),
                        str(tool_name), arguments_json, str(arguments_sha256),
                        str(idempotency_key), 1 if side_effect else 0,
                        int(round_index), int(call_index),
                        "pending" if side_effect else "not_applicable", now, now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM agent_tool_calls WHERE id = ?", (call_id,)
                ).fetchone()
        item = self._decode_agent_tool_call(row)
        item["disposition"] = "execute"
        return item

    def update_agent_tool_call_status(
        self,
        call_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_tool_calls SET status = ?, error = ?, updated_at = ?
                   WHERE id = ?""",
                (str(status), error, now, str(call_id)),
            )
            row = connection.execute(
                "SELECT * FROM agent_tool_calls WHERE id = ?", (str(call_id),)
            ).fetchone()
        return self._decode_agent_tool_call(row) if row else None

    def finish_agent_tool_call(
        self,
        call_id: str,
        *,
        status: str,
        result: Any,
        result_sha256: str,
        verification_status: str,
        evidence: list[dict[str, Any]],
        tool_run_id: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        result_json = json.dumps(result, ensure_ascii=False, default=str)
        evidence_json = json.dumps(evidence, ensure_ascii=False, default=str)
        verified_at = now if verification_status == "verified" else None
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_tool_calls
                   SET status = ?, result_json = ?, result_sha256 = ?,
                       verification_status = ?, evidence_json = ?, verified_at = ?,
                       tool_run_id = ?, error = ?, updated_at = ?, completed_at = ?
                   WHERE id = ?""",
                (
                    str(status), result_json, str(result_sha256),
                    str(verification_status), evidence_json, verified_at, tool_run_id,
                    error, now, now, str(call_id),
                ),
            )
            row = connection.execute(
                "SELECT * FROM agent_tool_calls WHERE id = ?", (str(call_id),)
            ).fetchone()
        return self._decode_agent_tool_call(row) if row else None

    def get_agent_tool_call(self, call_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_tool_calls WHERE id = ?",
                (str(call_id),),
            ).fetchone()
        return self._decode_agent_tool_call(row) if row else None

    def list_agent_tool_calls(self, run_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        bounded = max(1, min(5000, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_tool_calls WHERE run_id = ?
                   ORDER BY round_index, call_index, created_at LIMIT ?""",
                (str(run_id), bounded),
            ).fetchall()
        return [self._decode_agent_tool_call(row) for row in rows]

    def save_agent_checkpoint(
        self,
        *,
        run_id: str,
        step_id: str | None,
        plan_step_id: str | None,
        session_id: str,
        model: str,
        kind: str,
        state: dict[str, Any],
        state_sha256: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        checkpoint_id = str(uuid4())
        with self._lock, self._connect() as connection:
            sequence = int(connection.execute(
                """SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                   FROM agent_checkpoints WHERE run_id = ? AND model = ?""",
                (str(run_id), str(model)),
            ).fetchone()["next_sequence"])
            connection.execute(
                """INSERT INTO agent_checkpoints
                   (id, run_id, step_id, plan_step_id, session_id, model, sequence, kind,
                    state_json, state_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    checkpoint_id, str(run_id), step_id, plan_step_id,
                    str(session_id), str(model),
                    sequence, str(kind),
                    json.dumps(state, ensure_ascii=False, default=str),
                    str(state_sha256), now,
                ),
            )
        return {
            "id": checkpoint_id,
            "run_id": str(run_id),
            "step_id": step_id,
            "plan_step_id": plan_step_id,
            "session_id": str(session_id),
            "model": str(model),
            "sequence": sequence,
            "kind": str(kind),
            "state": state,
            "state_sha256": str(state_sha256),
            "created_at": now,
        }

    def list_agent_checkpoints(self, run_id: str, limit: int = 200) -> list[dict[str, Any]]:
        bounded = max(1, min(2000, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_checkpoints WHERE run_id = ?
                   ORDER BY model, sequence LIMIT ?""",
                (str(run_id), bounded),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["state"] = json.loads(item.pop("state_json"))
            except (json.JSONDecodeError, TypeError):
                item["state"] = {}
            result.append(item)
        return result

    def latest_agent_checkpoint(
        self,
        run_id: str,
        model: str,
        plan_step_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            if plan_step_id is None:
                row = connection.execute(
                    """SELECT * FROM agent_checkpoints
                       WHERE run_id = ? AND model = ? AND plan_step_id IS NULL
                       ORDER BY sequence DESC LIMIT 1""",
                    (str(run_id), str(model)),
                ).fetchone()
            else:
                row = connection.execute(
                    """SELECT * FROM agent_checkpoints
                       WHERE run_id = ? AND model = ? AND plan_step_id = ?
                       ORDER BY sequence DESC LIMIT 1""",
                    (str(run_id), str(model), str(plan_step_id)),
                ).fetchone()
        if row is None:
            return None
        item = dict(row)
        try:
            item["state"] = json.loads(item.pop("state_json"))
        except (json.JSONDecodeError, TypeError):
            item["state"] = {}
        return item

    def create_agent_steer(
        self,
        *,
        run_id: str,
        session_id: str,
        content: str,
        targets: list[str],
    ) -> dict[str, Any]:
        now = _utc_now()
        steer = {
            "id": str(uuid4()),
            "run_id": str(run_id),
            "session_id": str(session_id),
            "content": str(content),
            "targets": list(dict.fromkeys(str(value) for value in targets if str(value).strip())),
            "applied_models": [],
            "status": "queued",
            "created_at": now,
            "applied_at": None,
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_steers
                   (id, run_id, session_id, content, targets_json,
                    applied_models_json, status, created_at)
                   VALUES (?, ?, ?, ?, ?, '[]', 'queued', ?)""",
                (
                    steer["id"], steer["run_id"], steer["session_id"], steer["content"],
                    json.dumps(steer["targets"], ensure_ascii=False), now,
                ),
            )
        return steer

    def list_agent_steers(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_steers WHERE run_id = ? ORDER BY created_at, rowid",
                (str(run_id),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for source, target in (
                ("targets_json", "targets"),
                ("applied_models_json", "applied_models"),
            ):
                try:
                    item[target] = json.loads(item.pop(source))
                except (json.JSONDecodeError, TypeError):
                    item[target] = []
            result.append(item)
        return result

    def mark_agent_steer_applied(self, steer_id: str, model: str) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_steers WHERE id = ?", (str(steer_id),)
            ).fetchone()
            if row is None:
                return None
            try:
                applied = json.loads(row["applied_models_json"])
            except (json.JSONDecodeError, TypeError):
                applied = []
            if str(model) not in applied:
                applied.append(str(model))
            connection.execute(
                """UPDATE agent_steers
                   SET applied_models_json = ?, status = 'applied', applied_at = ?
                   WHERE id = ?""",
                (json.dumps(applied, ensure_ascii=False), now, str(steer_id)),
            )
        return next(
            (item for item in self.list_agent_steers(row["run_id"]) if item["id"] == str(steer_id)),
            None,
        )

    def recover_incomplete_agent_runs(self) -> int:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            run_ids = [
                row["id"] for row in connection.execute(
                    "SELECT id FROM agent_runs WHERE status IN ('queued', 'running', 'waiting_approval')"
                ).fetchall()
            ]
            if not run_ids:
                return 0
            placeholders = ",".join("?" for _ in run_ids)
            connection.execute(
                f"UPDATE agent_runs SET status = 'recoverable', error = '应用退出前运行尚未完成', updated_at = ? WHERE id IN ({placeholders})",
                (now, *run_ids),
            )
            connection.execute(
                f"UPDATE agent_steps SET status = 'interrupted', error = '应用重启导致步骤中断', updated_at = ?, completed_at = ? WHERE run_id IN ({placeholders}) AND status IN ('pending', 'running', 'waiting_approval')",
                (now, now, *run_ids),
            )
            connection.execute(
                f"""UPDATE agent_tool_calls
                    SET status = CASE WHEN side_effect = 1 THEN 'uncertain' ELSE 'interrupted' END,
                        verification_status = CASE
                            WHEN side_effect = 1 THEN 'uncertain'
                            ELSE 'not_applicable'
                        END,
                        error = CASE
                            WHEN side_effect = 1
                            THEN '应用重启时副作用工具结果未确认；已阻止自动重放'
                            ELSE '应用重启导致工具调用中断'
                        END,
                        updated_at = ?
                    WHERE run_id IN ({placeholders})
                      AND status IN ('pending', 'waiting_approval', 'running')""",
                (now, *run_ids),
            )
            connection.execute(
                f"""UPDATE agent_plan_steps
                    SET status = CASE
                            WHEN status = 'running' THEN 'interrupted'
                            ELSE 'pending'
                        END,
                        error = CASE
                            WHEN status = 'running'
                            THEN '应用重启导致计划步骤中断'
                            ELSE error
                        END,
                        updated_at = ?,
                        completed_at = CASE
                            WHEN status = 'running' THEN ?
                            ELSE completed_at
                        END
                    WHERE run_id IN ({placeholders})
                      AND status IN ('pending', 'ready', 'running')""",
                (now, now, *run_ids),
            )
        for run_id in run_ids:
            self.append_agent_event(run_id, "recovery_required", {"reason": "process_restart"})
        return len(run_ids)
