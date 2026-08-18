from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import uuid4

from ._shared import (
    _utc_now,
)


class WorkspacesMixin:
    @staticmethod
    def _decode_workspace_repository(
        row: sqlite3.Row | dict[str, Any],
    ) -> dict[str, Any]:
        item = dict(row)
        try:
            item["verification_commands"] = json.loads(
                item.pop("verification_commands_json")
            )
        except (json.JSONDecodeError, TypeError):
            item["verification_commands"] = []
        return item

    def upsert_workspace_repository(
        self,
        *,
        session_id: str,
        root_path: str,
        display_name: str,
        head_commit: str,
        branch: str,
        verification_commands: list[str],
    ) -> dict[str, Any]:
        self.ensure_session(session_id)
        now = _utc_now()
        repository_id = str(uuid4())
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO workspace_repositories
                   (id, session_id, root_path, display_name, head_commit,
                    branch, verification_commands_json, status, created_at,
                    updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
                   ON CONFLICT(session_id, root_path) DO UPDATE SET
                       display_name = excluded.display_name,
                       head_commit = excluded.head_commit,
                       branch = excluded.branch,
                       verification_commands_json =
                           excluded.verification_commands_json,
                       status = 'ready',
                       updated_at = excluded.updated_at""",
                (
                    repository_id,
                    str(session_id),
                    str(root_path),
                    str(display_name),
                    str(head_commit),
                    str(branch),
                    json.dumps(
                        list(verification_commands),
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """SELECT * FROM workspace_repositories
                   WHERE session_id = ? AND root_path = ?""",
                (str(session_id), str(root_path)),
            ).fetchone()
        if row is None:
            raise RuntimeError("Git 仓库登记失败")
        return self._decode_workspace_repository(row)

    def get_workspace_repository(
        self,
        repository_id: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspace_repositories WHERE id = ?",
                (str(repository_id),),
            ).fetchone()
        return self._decode_workspace_repository(row) if row else None

    def list_workspace_repositories(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM workspace_repositories
                   WHERE session_id = ? ORDER BY updated_at DESC, rowid DESC""",
                (str(session_id),),
            ).fetchall()
        return [self._decode_workspace_repository(row) for row in rows]

    @staticmethod
    def _decode_agent_workspace(
        row: sqlite3.Row | dict[str, Any],
    ) -> dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("changed_files_json", "changed_files"),
            ("verification_json", "verification"),
        ):
            try:
                item[target] = json.loads(item.pop(source))
            except (json.JSONDecodeError, TypeError):
                item[target] = []
        patch = item.get("diff_patch")
        if isinstance(patch, memoryview):
            item["diff_patch"] = patch.tobytes()
        return item

    def create_agent_workspace(
        self,
        *,
        run_id: str,
        plan_step_id: str,
        repository_id: str,
        session_id: str,
        attempt: int,
        root_path: str,
        base_commit: str,
    ) -> dict[str, Any]:
        workspace_id = str(uuid4())
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_workspaces
                   (id, run_id, plan_step_id, repository_id, session_id,
                    attempt, root_path, base_commit, status, created_at,
                    updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?)
                   ON CONFLICT(run_id, plan_step_id, attempt) DO NOTHING""",
                (
                    workspace_id,
                    str(run_id),
                    str(plan_step_id),
                    str(repository_id),
                    str(session_id),
                    max(1, int(attempt)),
                    str(root_path),
                    str(base_commit),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """SELECT * FROM agent_workspaces
                   WHERE run_id = ? AND plan_step_id = ? AND attempt = ?""",
                (str(run_id), str(plan_step_id), max(1, int(attempt))),
            ).fetchone()
        if row is None:
            raise RuntimeError("Worker worktree 记录创建失败")
        return self._decode_agent_workspace(row)

    def get_agent_workspace(
        self,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_workspaces WHERE id = ?",
                (str(workspace_id),),
            ).fetchone()
        return self._decode_agent_workspace(row) if row else None

    def list_agent_workspaces(
        self,
        run_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_workspaces WHERE run_id = ?
                   ORDER BY created_at, rowid""",
                (str(run_id),),
            ).fetchall()
        return [self._decode_agent_workspace(row) for row in rows]

    def list_session_agent_workspaces(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_workspaces WHERE session_id = ?
                   ORDER BY created_at, rowid""",
                (str(session_id),),
            ).fetchall()
        return [self._decode_agent_workspace(row) for row in rows]

    def update_agent_workspace_evidence(
        self,
        workspace_id: str,
        *,
        status: str,
        changed_files: list[dict[str, Any]],
        diff_patch: bytes | None,
        diff_sha256: str | None,
        verification_status: str,
        verification: list[dict[str, Any]],
        error: str | None = None,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_workspaces
                   SET status = ?, changed_files_json = ?, diff_patch = ?,
                       diff_sha256 = ?, diff_bytes = ?,
                       verification_status = ?, verification_json = ?,
                       error = ?, updated_at = ?, verified_at = ?
                   WHERE id = ?""",
                (
                    str(status),
                    json.dumps(changed_files, ensure_ascii=False, default=str),
                    diff_patch,
                    str(diff_sha256) if diff_sha256 else None,
                    len(diff_patch or b""),
                    str(verification_status),
                    json.dumps(verification, ensure_ascii=False, default=str),
                    str(error or "")[:2000] or None,
                    now,
                    now,
                    str(workspace_id),
                ),
            )
        return self.get_agent_workspace(workspace_id)

    def update_agent_workspace_apply_status(
        self,
        workspace_id: str,
        *,
        apply_status: str,
        status: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_workspaces
                   SET apply_status = ?,
                       status = COALESCE(?, status),
                       error = ?, updated_at = ?,
                       applied_at = CASE WHEN ? = 'applied' THEN ?
                                         ELSE applied_at END
                   WHERE id = ?""",
                (
                    str(apply_status),
                    str(status) if status else None,
                    str(error or "")[:2000] or None,
                    now,
                    str(apply_status),
                    now,
                    str(workspace_id),
                ),
            )
        return self.get_agent_workspace(workspace_id)
