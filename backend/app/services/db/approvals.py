from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ApprovalsMixin:
    """SQLite persistence for tool approvals and session grants (schema v5)."""

    def upsert_pending_approval(self, record: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO pending_approvals (
                    id, session_id, model, tool_name, risk, title, detail,
                    arguments_json, allow_session, created_at, status,
                    decision, feedback, cancel_reason, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    session_id=excluded.session_id,
                    model=excluded.model,
                    tool_name=excluded.tool_name,
                    risk=excluded.risk,
                    title=excluded.title,
                    detail=excluded.detail,
                    arguments_json=excluded.arguments_json,
                    allow_session=excluded.allow_session,
                    created_at=excluded.created_at,
                    status=excluded.status,
                    decision=excluded.decision,
                    feedback=excluded.feedback,
                    cancel_reason=excluded.cancel_reason,
                    resolved_at=excluded.resolved_at
                """,
                (
                    record["id"],
                    record["session_id"],
                    record["model"],
                    record["tool_name"],
                    record["risk"],
                    record["title"],
                    record["detail"],
                    json.dumps(record.get("arguments") or {}, ensure_ascii=False),
                    1 if record.get("allow_session") else 0,
                    record["created_at"],
                    record.get("status") or "pending",
                    record.get("decision"),
                    record.get("feedback") or "",
                    record.get("cancel_reason"),
                    record.get("resolved_at"),
                ),
            )
            connection.commit()

    def mark_approval_resolved(
        self,
        approval_id: str,
        *,
        decision: str | None = None,
        feedback: str = "",
        status: str = "resolved",
        cancel_reason: str | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE pending_approvals
                SET status = ?, decision = ?, feedback = ?, cancel_reason = ?,
                    resolved_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    decision,
                    feedback,
                    cancel_reason,
                    _utc_now(),
                    str(approval_id),
                ),
            )
            connection.commit()

    def delete_pending_approval(self, approval_id: str) -> None:
        """Remove a still-pending row after successful wait completion."""
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM pending_approvals WHERE id = ? AND status = 'pending'",
                (str(approval_id),),
            )
            connection.commit()

    def list_pending_approvals(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, model, tool_name, risk, title, detail,
                       arguments_json, allow_session, created_at, status
                FROM pending_approvals
                WHERE status = 'pending'
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [self._row_to_approval(row) for row in rows]

    def cancel_all_pending_approvals(self, reason: str = "backend_restart") -> list[dict[str, Any]]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, model, tool_name, risk, title, detail,
                       arguments_json, allow_session, created_at, status
                FROM pending_approvals
                WHERE status = 'pending'
                """
            ).fetchall()
            connection.execute(
                """
                UPDATE pending_approvals
                SET status = 'cancelled', decision = 'deny',
                    cancel_reason = ?, resolved_at = ?
                WHERE status = 'pending'
                """,
                (reason, now),
            )
            connection.commit()
        cancelled = [self._row_to_approval(row) for row in rows]
        for item in cancelled:
            item["status"] = "cancelled"
            item["decision"] = "deny"
            item["cancel_reason"] = reason
            item["resolved_at"] = now
        return cancelled

    def list_cancelled_approvals(
        self,
        *,
        reason: str | None = "backend_restart",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT id, session_id, model, tool_name, risk, title, detail,
                   arguments_json, allow_session, created_at, status,
                   decision, feedback, cancel_reason, resolved_at
            FROM pending_approvals
            WHERE status = 'cancelled'
        """
        params: list[Any] = []
        if reason:
            query += " AND cancel_reason = ?"
            params.append(reason)
        query += " ORDER BY resolved_at DESC LIMIT ?"
        params.append(max(1, min(500, int(limit))))
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_approval(row) for row in rows]

    def expire_pending_approvals(self, ttl_seconds: float) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=max(1.0, ttl_seconds))
        cutoff_iso = cutoff.isoformat()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE pending_approvals
                SET status = 'cancelled', decision = 'deny',
                    cancel_reason = 'ttl_expired', resolved_at = ?
                WHERE status = 'pending' AND created_at < ?
                """,
                (_utc_now(), cutoff_iso),
            )
            connection.commit()
            return int(cursor.rowcount or 0)

    def upsert_session_grant(self, session_id: str, risk: str, *, ttl_seconds: float) -> None:
        granted_at = datetime.now(UTC)
        expires_at = granted_at + timedelta(seconds=max(60.0, ttl_seconds))
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO session_grants (session_id, risk, granted_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id, risk) DO UPDATE SET
                    granted_at=excluded.granted_at,
                    expires_at=excluded.expires_at
                """,
                (str(session_id), str(risk), granted_at.isoformat(), expires_at.isoformat()),
            )
            connection.commit()

    def is_session_grant_active(self, session_id: str, risk: str) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM session_grants
                WHERE session_id = ? AND risk = ? AND expires_at > ?
                """,
                (str(session_id), str(risk), now),
            ).fetchone()
        return row is not None

    def list_session_grants(self) -> set[tuple[str, str]]:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, risk FROM session_grants
                WHERE expires_at > ?
                """,
                (now,),
            ).fetchall()
            connection.execute("DELETE FROM session_grants WHERE expires_at <= ?", (now,))
            connection.commit()
        return {(str(row["session_id"]), str(row["risk"])) for row in rows}

    def clear_session_grants(self, session_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM session_grants WHERE session_id = ?",
                (str(session_id),),
            )
            connection.commit()

    def clear_session_grants_for_risks(self, risks: set[str]) -> int:
        normalized = {str(value) for value in risks}
        if not normalized:
            return 0
        placeholders = ",".join("?" for _ in normalized)
        with self._lock, self._connect() as connection:
            before = connection.execute(
                f"SELECT COUNT(*) AS c FROM session_grants WHERE risk IN ({placeholders})",
                tuple(normalized),
            ).fetchone()["c"]
            connection.execute(
                f"DELETE FROM session_grants WHERE risk IN ({placeholders})",
                tuple(normalized),
            )
            connection.commit()
        return int(before or 0)

    @staticmethod
    def _row_to_approval(row: Any) -> dict[str, Any]:
        arguments_raw = row["arguments_json"] if "arguments_json" in row.keys() else "{}"
        try:
            arguments = json.loads(arguments_raw or "{}")
        except json.JSONDecodeError:
            arguments = {}
        keys = set(row.keys())
        result: dict[str, Any] = {
            "id": row["id"],
            "session_id": row["session_id"],
            "model": row["model"],
            "tool_name": row["tool_name"],
            "risk": row["risk"],
            "title": row["title"],
            "detail": row["detail"],
            "arguments": arguments if isinstance(arguments, dict) else {},
            "allow_session": bool(row["allow_session"]),
            "created_at": row["created_at"],
            "status": row["status"],
        }
        for optional in ("decision", "feedback", "cancel_reason", "resolved_at"):
            if optional in keys:
                result[optional] = row[optional]
        return result
