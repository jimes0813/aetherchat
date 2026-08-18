from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any
from uuid import uuid4

from ...models.schemas import Task
from ._shared import (
    _utc_now,
)


class TasksMixin:
    def list_tasks_for_session(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT id, title, description, status, claimed_by, priority, depends_on_json
                   FROM tasks WHERE session_id = ? ORDER BY priority DESC, updated_at DESC""",
                (str(session_id),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["depends_on"] = json.loads(item.pop("depends_on_json", "[]"))
            except (json.JSONDecodeError, TypeError):
                item["depends_on"] = []
            result.append(item)
        return result

    @staticmethod
    def _task_payload(task: Task) -> dict[str, Any]:
        return task.model_dump(mode="json")

    def sync_tasks(self, tasks: Iterable[Task], *, record_events: bool = True) -> None:
        now = _utc_now()
        task_list = list(tasks)
        with self._lock, self._connect() as connection:
            for task in task_list:
                session_id = str(task.session_id)
                self._ensure_session_in_connection(connection, session_id, now)
                previous = connection.execute("SELECT * FROM tasks WHERE id = ?", (str(task.id),)).fetchone()
                payload = self._task_payload(task)
                changed = previous is None or any(
                    previous[key] != payload.get(key)
                    for key in ("title", "description", "status", "claimed_by", "priority")
                )
                encoded_dependencies = json.dumps(
                    [str(value) for value in task.depends_on],
                    ensure_ascii=False,
                )
                changed = changed or (
                    previous is not None
                    and previous["depends_on_json"] != encoded_dependencies
                )
                created_at = previous["created_at"] if previous else now
                connection.execute(
                    """
                    INSERT INTO tasks
                        (id, session_id, title, description, status, claimed_by, priority, depends_on_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        session_id=excluded.session_id, title=excluded.title,
                        description=excluded.description, status=excluded.status,
                        claimed_by=excluded.claimed_by, priority=excluded.priority,
                        depends_on_json=excluded.depends_on_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        str(task.id), session_id, task.title, task.description, task.status,
                        task.claimed_by, task.priority, encoded_dependencies, created_at, now,
                    ),
                )
                if changed and record_events:
                    action = "created" if previous is None else "updated"
                    connection.execute(
                        """
                        INSERT INTO task_events (id, task_id, session_id, action, snapshot_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (str(uuid4()), str(task.id), session_id, action, json.dumps(payload, ensure_ascii=False), now),
                    )
            connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id IN (SELECT DISTINCT session_id FROM tasks)",
                (now,),
            )

    def load_all_tasks(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT id, session_id, title, description, status, claimed_by, priority, depends_on_json
                   FROM tasks ORDER BY created_at"""
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["depends_on"] = json.loads(item.pop("depends_on_json", "[]"))
            except (json.JSONDecodeError, TypeError):
                item["depends_on"] = []
            result.append(item)
        return result

    def list_task_events(self, session_id: str, limit: int = 300) -> list[dict[str, Any]]:
        bounded = max(1, min(2000, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, task_id, session_id, action, snapshot_json, created_at
                FROM task_events WHERE session_id = ? ORDER BY created_at DESC LIMIT ?
                """,
                (str(session_id), bounded),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["task"] = json.loads(item.pop("snapshot_json"))
            result.append(item)
        return result
