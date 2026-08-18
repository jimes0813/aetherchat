from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ._shared import (
    _utc_now,
)


class AutomationMixin:
    @staticmethod
    def _decode_automation_schedule(
        row: sqlite3.Row | dict[str, Any],
    ) -> dict[str, Any]:
        item = dict(row)
        for source, target, fallback in (
            ("targets_json", "targets", ["all"]),
            ("permission_snapshot_json", "permission_snapshot", {}),
        ):
            try:
                item[target] = json.loads(item.pop(source))
            except (json.JSONDecodeError, TypeError):
                item[target] = fallback
        item["enabled"] = bool(item.get("enabled"))
        return item

    @staticmethod
    def _decode_background_job(
        row: sqlite3.Row | dict[str, Any],
    ) -> dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("action_json", "action"),
            ("permission_snapshot_json", "permission_snapshot"),
        ):
            try:
                item[target] = json.loads(item.pop(source))
            except (json.JSONDecodeError, TypeError):
                item[target] = {}
        return item

    def create_automation_schedule(
        self,
        *,
        session_id: str,
        name: str,
        prompt: str,
        mode: str,
        targets: list[str],
        trigger_type: str,
        interval_minutes: int | None,
        timezone: str,
        next_run_at: str,
        enabled: bool,
        max_budget_usd: float,
        permission_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        self.ensure_session(session_id, mode=mode)
        schedule_id = str(uuid4())
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO automation_schedules
                   (id, session_id, name, prompt, mode, targets_json,
                    trigger_type, interval_minutes, timezone, next_run_at,
                    enabled, max_budget_usd, permission_snapshot_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    schedule_id,
                    str(session_id),
                    str(name),
                    str(prompt),
                    str(mode),
                    json.dumps(targets, ensure_ascii=False),
                    str(trigger_type),
                    interval_minutes,
                    str(timezone),
                    str(next_run_at),
                    1 if enabled else 0,
                    float(max_budget_usd),
                    json.dumps(permission_snapshot, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
        schedule = self.get_automation_schedule(schedule_id, session_id=session_id)
        if schedule is None:  # pragma: no cover - guarded by the insert above
            raise RuntimeError("定时任务写入后无法读取")
        return schedule

    def get_automation_schedule(
        self,
        schedule_id: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM automation_schedules WHERE id = ?"
        parameters: tuple[Any, ...] = (str(schedule_id),)
        if session_id is not None:
            query += " AND session_id = ?"
            parameters = (str(schedule_id), str(session_id))
        with self._lock, self._connect() as connection:
            row = connection.execute(query, parameters).fetchone()
        return self._decode_automation_schedule(row) if row is not None else None

    def list_automation_schedules(
        self,
        session_id: str,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(500, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM automation_schedules
                   WHERE session_id = ?
                   ORDER BY enabled DESC, next_run_at, updated_at DESC
                   LIMIT ?""",
                (str(session_id), bounded),
            ).fetchall()
        return [self._decode_automation_schedule(row) for row in rows]

    def update_automation_schedule(
        self,
        schedule_id: str,
        session_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        allowed = {
            "name",
            "prompt",
            "mode",
            "targets_json",
            "trigger_type",
            "interval_minutes",
            "timezone",
            "next_run_at",
            "enabled",
            "max_budget_usd",
            "permission_snapshot_json",
            "last_run_at",
            "last_status",
            "last_error",
        }
        normalized = {key: value for key, value in updates.items() if key in allowed}
        if not normalized:
            return self.get_automation_schedule(schedule_id, session_id=session_id)
        normalized["updated_at"] = _utc_now()
        assignments = ", ".join(f"{key} = ?" for key in normalized)
        values = [*normalized.values(), str(schedule_id), str(session_id)]
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE automation_schedules SET {assignments} WHERE id = ? AND session_id = ?",
                values,
            )
        if cursor.rowcount == 0:
            return None
        return self.get_automation_schedule(schedule_id, session_id=session_id)

    def delete_automation_schedule(self, schedule_id: str, session_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM automation_schedules WHERE id = ? AND session_id = ?",
                (str(schedule_id), str(session_id)),
            )
        return cursor.rowcount > 0

    def create_background_job(
        self,
        *,
        schedule_id: str | None,
        session_id: str,
        trigger_type: str,
        action: dict[str, Any],
        permission_snapshot: dict[str, Any],
        max_budget_usd: float,
        scheduled_for: str,
    ) -> dict[str, Any]:
        job_id = str(uuid4())
        now = _utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO background_jobs
                   (id, schedule_id, session_id, trigger_type, status,
                    action_json, permission_snapshot_json, max_budget_usd,
                    scheduled_for, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    str(schedule_id) if schedule_id else None,
                    str(session_id),
                    str(trigger_type),
                    json.dumps(action, ensure_ascii=False, sort_keys=True),
                    json.dumps(permission_snapshot, ensure_ascii=False, sort_keys=True),
                    float(max_budget_usd),
                    str(scheduled_for),
                    now,
                    now,
                ),
            )
            if cursor.rowcount == 0 and schedule_id:
                row = connection.execute(
                    """SELECT background_jobs.*, automation_schedules.name AS schedule_name
                       FROM background_jobs
                       LEFT JOIN automation_schedules
                         ON automation_schedules.id = background_jobs.schedule_id
                       WHERE background_jobs.schedule_id = ?
                         AND background_jobs.scheduled_for = ?""",
                    (str(schedule_id), str(scheduled_for)),
                ).fetchone()
            else:
                row = connection.execute(
                    """SELECT background_jobs.*, automation_schedules.name AS schedule_name
                       FROM background_jobs
                       LEFT JOIN automation_schedules
                         ON automation_schedules.id = background_jobs.schedule_id
                       WHERE background_jobs.id = ?""",
                    (job_id,),
                ).fetchone()
        if row is None:  # pragma: no cover - guarded by the insert/select above
            raise RuntimeError("后台任务写入后无法读取")
        return self._decode_background_job(row)

    def materialize_due_automation_jobs(
        self,
        now: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(200, int(limit)))
        created_ids: list[str] = []
        now_value = datetime.fromisoformat(str(now).replace("Z", "+00:00")).astimezone(UTC)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM automation_schedules
                   WHERE enabled = 1 AND next_run_at IS NOT NULL
                     AND next_run_at <= ?
                   ORDER BY next_run_at, created_at LIMIT ?""",
                (str(now), bounded),
            ).fetchall()
            for row in rows:
                schedule = self._decode_automation_schedule(row)
                scheduled_for = str(schedule["next_run_at"])
                action = {
                    "type": "post_message",
                    "content": schedule["prompt"],
                    "targets": schedule["targets"],
                    "mode": schedule["mode"],
                    "execution_strategy": "direct",
                    "plan_confirmation_required": False,
                    "max_revision_cycles": 0,
                }
                job_id = str(uuid4())
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO background_jobs
                       (id, schedule_id, session_id, trigger_type, status,
                        action_json, permission_snapshot_json, max_budget_usd,
                        scheduled_for, created_at, updated_at)
                       VALUES (?, ?, ?, 'schedule', 'queued', ?, ?, ?, ?, ?, ?)""",
                    (
                        job_id,
                        schedule["id"],
                        schedule["session_id"],
                        json.dumps(action, ensure_ascii=False, sort_keys=True),
                        json.dumps(
                            schedule["permission_snapshot"],
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        float(schedule["max_budget_usd"]),
                        scheduled_for,
                        str(now),
                        str(now),
                    ),
                )
                if cursor.rowcount > 0:
                    created_ids.append(job_id)

                if schedule["trigger_type"] == "interval":
                    interval = max(1, int(schedule.get("interval_minutes") or 1))
                    scheduled_value = datetime.fromisoformat(
                        scheduled_for.replace("Z", "+00:00")
                    ).astimezone(UTC)
                    period = timedelta(minutes=interval)
                    missed = max(
                        1,
                        int((now_value - scheduled_value).total_seconds() // period.total_seconds()) + 1,
                    )
                    next_run_at = (scheduled_value + period * missed).isoformat()
                    enabled = 1
                else:
                    next_run_at = None
                    enabled = 0
                connection.execute(
                    """UPDATE automation_schedules
                       SET next_run_at = ?, enabled = ?, last_status = 'queued',
                           last_error = NULL, updated_at = ?
                       WHERE id = ?""",
                    (next_run_at, enabled, str(now), schedule["id"]),
                )
        return [
            job
            for job_id in created_ids
            if (job := self.get_background_job(job_id)) is not None
        ]

    def get_background_job(
        self,
        job_id: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        query = """SELECT background_jobs.*, automation_schedules.name AS schedule_name
                   FROM background_jobs
                   LEFT JOIN automation_schedules
                     ON automation_schedules.id = background_jobs.schedule_id
                   WHERE background_jobs.id = ?"""
        parameters: tuple[Any, ...] = (str(job_id),)
        if session_id is not None:
            query += " AND background_jobs.session_id = ?"
            parameters = (str(job_id), str(session_id))
        with self._lock, self._connect() as connection:
            row = connection.execute(query, parameters).fetchone()
        return self._decode_background_job(row) if row is not None else None

    def list_background_jobs(
        self,
        session_id: str,
        *,
        limit: int = 200,
        review_only: bool = False,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(500, int(limit)))
        review_filter = (
            "AND background_jobs.completed_at IS NOT NULL "
            "AND background_jobs.reviewed_at IS NULL"
            if review_only else ""
        )
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""SELECT background_jobs.*, automation_schedules.name AS schedule_name
                    FROM background_jobs
                    LEFT JOIN automation_schedules
                      ON automation_schedules.id = background_jobs.schedule_id
                    WHERE background_jobs.session_id = ? {review_filter}
                    ORDER BY background_jobs.updated_at DESC LIMIT ?""",
                (str(session_id), bounded),
            ).fetchall()
        return [self._decode_background_job(row) for row in rows]

    def claim_background_job(
        self,
        *,
        worker_id: str,
        now: str,
        lease_expires_at: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            expired_cancellations = connection.execute(
                """SELECT id, schedule_id FROM background_jobs
                   WHERE status = 'cancel_requested'
                     AND (lease_expires_at IS NULL OR lease_expires_at <= ?)""",
                (str(now),),
            ).fetchall()
            if expired_cancellations:
                connection.execute(
                    """UPDATE background_jobs
                       SET status = 'cancelled', error = '用户取消',
                           lease_owner = NULL, lease_expires_at = NULL,
                           updated_at = ?, completed_at = COALESCE(completed_at, ?)
                       WHERE status = 'cancel_requested'
                         AND (lease_expires_at IS NULL OR lease_expires_at <= ?)""",
                    (str(now), str(now), str(now)),
                )
                for cancelled in expired_cancellations:
                    if cancelled["schedule_id"]:
                        connection.execute(
                            """UPDATE automation_schedules
                               SET last_run_at = ?, last_status = 'cancelled',
                                   last_error = '用户取消', updated_at = ?
                               WHERE id = ?""",
                            (
                                str(now),
                                str(now),
                                str(cancelled["schedule_id"]),
                            ),
                        )
            row = connection.execute(
                """SELECT id FROM background_jobs
                   WHERE status = 'queued'
                      OR (status = 'running' AND lease_expires_at IS NOT NULL
                          AND lease_expires_at <= ?)
                   ORDER BY scheduled_for, created_at LIMIT 1""",
                (str(now),),
            ).fetchone()
            if row is None:
                return None
            job_id = str(row["id"])
            connection.execute(
                """UPDATE background_jobs
                   SET status = 'running', lease_owner = ?, lease_expires_at = ?,
                       attempt = attempt + 1, error = NULL, updated_at = ?,
                       started_at = COALESCE(started_at, ?), completed_at = NULL
                   WHERE id = ?""",
                (str(worker_id), str(lease_expires_at), str(now), str(now), job_id),
            )
        return self.get_background_job(job_id)

    def heartbeat_background_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        now: str,
        lease_expires_at: str,
    ) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE background_jobs
                   SET lease_expires_at = ?, updated_at = ?
                   WHERE id = ? AND status = 'running' AND lease_owner = ?""",
                (str(lease_expires_at), str(now), str(job_id), str(worker_id)),
            )
        return cursor.rowcount > 0

    def attach_background_job_run(
        self,
        job_id: str,
        *,
        worker_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE background_jobs SET agent_run_id = ?, updated_at = ?
                   WHERE id = ? AND status = 'running' AND lease_owner = ?""",
                (str(run_id), _utc_now(), str(job_id), str(worker_id)),
            )
        return self.get_background_job(job_id) if cursor.rowcount else None

    def finish_background_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        status: str,
        summary: str = "",
        error: str | None = None,
        budget_spent_usd: float = 0.0,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE background_jobs
                   SET status = ?, summary = ?, error = ?,
                       budget_spent_usd = ?, lease_owner = NULL,
                       lease_expires_at = NULL, updated_at = ?, completed_at = ?
                   WHERE id = ? AND lease_owner = ?""",
                (
                    str(status),
                    str(summary)[:20_000],
                    str(error or "")[:4000] or None,
                    max(0.0, float(budget_spent_usd)),
                    now,
                    now,
                    str(job_id),
                    str(worker_id),
                ),
            )
            row = connection.execute(
                "SELECT schedule_id FROM background_jobs WHERE id = ?",
                (str(job_id),),
            ).fetchone()
            if cursor.rowcount and row is not None and row["schedule_id"]:
                connection.execute(
                    """UPDATE automation_schedules
                       SET last_run_at = ?, last_status = ?, last_error = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        now,
                        str(status),
                        str(error or "")[:4000] or None,
                        now,
                        str(row["schedule_id"]),
                    ),
                )
        return self.get_background_job(job_id) if cursor.rowcount else None

    def requeue_background_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        error: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE background_jobs
                   SET status = 'queued', error = ?, lease_owner = NULL,
                       lease_expires_at = NULL, updated_at = ?
                   WHERE id = ? AND lease_owner = ?""",
                (str(error)[:4000], _utc_now(), str(job_id), str(worker_id)),
            )
        return self.get_background_job(job_id) if cursor.rowcount else None

    def request_background_job_cancel(
        self,
        job_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM background_jobs WHERE id = ? AND session_id = ?",
                (str(job_id), str(session_id)),
            ).fetchone()
            if row is None:
                return None
            if row["status"] == "queued":
                connection.execute(
                    """UPDATE background_jobs
                       SET status = 'cancelled', error = '用户取消',
                           updated_at = ?, completed_at = ?
                       WHERE id = ?""",
                    (now, now, str(job_id)),
                )
            elif row["status"] == "running":
                connection.execute(
                    """UPDATE background_jobs
                       SET status = 'cancel_requested', updated_at = ?
                       WHERE id = ?""",
                    (now, str(job_id)),
                )
        return self.get_background_job(job_id, session_id=session_id)

    def mark_background_job_reviewed(
        self,
        job_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE background_jobs SET reviewed_at = ?, updated_at = ?
                   WHERE id = ? AND session_id = ? AND completed_at IS NOT NULL""",
                (now, now, str(job_id), str(session_id)),
            )
        return (
            self.get_background_job(job_id, session_id=session_id)
            if cursor.rowcount else None
        )

    def automation_status(self) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            schedule_count = connection.execute(
                "SELECT COUNT(*) AS count FROM automation_schedules WHERE enabled = 1"
            ).fetchone()["count"]
            queued_count = connection.execute(
                "SELECT COUNT(*) AS count FROM background_jobs WHERE status = 'queued'"
            ).fetchone()["count"]
            running_count = connection.execute(
                """SELECT COUNT(*) AS count FROM background_jobs
                   WHERE status IN ('running', 'cancel_requested')"""
            ).fetchone()["count"]
            review_count = connection.execute(
                """SELECT COUNT(*) AS count FROM background_jobs
                   WHERE completed_at IS NOT NULL AND reviewed_at IS NULL"""
            ).fetchone()["count"]
        return {
            "enabled_schedules": int(schedule_count),
            "queued_jobs": int(queued_count),
            "running_jobs": int(running_count),
            "review_items": int(review_count),
        }
