from __future__ import annotations

import sqlite3
from typing import Any

from ._shared import (
    _utc_now,
)


class SessionsMixin:
    def ensure_session(
        self,
        session_id: str,
        *,
        title: str = "新会话",
        mode: str = "project",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (id, title, summary, mode, project_id, created_at, updated_at)
                VALUES (?, ?, '', ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    str(session_id),
                    title.strip()[:200] or "新会话",
                    mode,
                    (str(project_id).strip() if project_id else None) or None,
                    now,
                    now,
                ),
            )
            # Fill project_id on existing rows that still lack binding.
            if project_id:
                connection.execute(
                    """
                    UPDATE sessions
                    SET project_id = COALESCE(NULLIF(project_id, ''), ?),
                        updated_at = ?
                    WHERE id = ? AND (project_id IS NULL OR project_id = '')
                    """,
                    (str(project_id).strip(), now, str(session_id)),
                )
            row = connection.execute("SELECT * FROM sessions WHERE id = ?", (str(session_id),)).fetchone()
        return dict(row) if row else {}

    def create_session(
        self,
        session_id: str,
        title: str,
        mode: str = "project",
        *,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.ensure_session(session_id, title=title, mode=mode, project_id=project_id)

    def set_session_project(self, session_id: str, project_id: str | None) -> dict[str, Any] | None:
        """Bind or clear governance project_id for a session."""
        now = _utc_now()
        clean = (str(project_id).strip() if project_id is not None else None) or None
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT id FROM sessions WHERE id = ?",
                (str(session_id),),
            ).fetchone()
            if existing is None:
                return None
            connection.execute(
                """
                UPDATE sessions
                SET project_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (clean, now, str(session_id)),
            )
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (str(session_id),),
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(500, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*,
                       COUNT(DISTINCT m.id) AS message_count,
                       COUNT(DISTINCT t.id) AS task_count,
                       COUNT(DISTINCT CASE WHEN t.status NOT IN ('done', 'merged') THEN t.id END) AS open_task_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                LEFT JOIN tasks t ON t.session_id = s.id
                GROUP BY s.id
                ORDER BY COALESCE(s.last_message_at, s.updated_at) DESC
                LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (str(session_id),),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, session_id: str) -> bool:
        with self._lock, self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM sessions WHERE id = ?",
                (str(session_id),),
            ).rowcount
        return bool(deleted)

    def session_has_active_runs(self, session_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM agent_runs
                WHERE session_id = ?
                  AND status IN (
                    'queued', 'running', 'waiting_approval',
                    'waiting_plan_confirmation', 'recoverable'
                  )
                LIMIT 1
                """,
                (str(session_id),),
            ).fetchone()
        return row is not None

    @staticmethod
    def _ensure_session_in_connection(
        connection: sqlite3.Connection,
        session_id: str,
        now: str,
        *,
        mode: str = "project",
    ) -> None:
        connection.execute(
            """
            INSERT INTO sessions (id, title, summary, mode, project_id, created_at, updated_at)
            VALUES (?, '新会话', '', ?, NULL, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (session_id, mode, now, now),
        )
