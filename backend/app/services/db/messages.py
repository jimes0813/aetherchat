from __future__ import annotations

import json
import sqlite3
from typing import Any

from ...models.schemas import Message
from ._shared import (
    _utc_now,
)


class MessagesMixin:
    def save_message(self, message: Message, *, mode: str = "project") -> None:
        session_id = str(message.session_id)
        self.ensure_session(session_id, mode=mode)
        created_at = message.created_at.isoformat()
        summary = " ".join(message.content.strip().split())[:180]
        with self._lock, self._connect() as connection:
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO messages
                    (id, session_id, author_model, role, content, tokens_in, tokens_out,
                     cost_usd, attachments_json, generated_files_json,
                     reasoning_summary_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(message.id), session_id, message.author_model, message.role, message.content,
                    message.tokens_in, message.tokens_out, message.cost_usd,
                    json.dumps(message.attachments, ensure_ascii=False),
                    json.dumps(message.generated_files, ensure_ascii=False, default=str),
                    json.dumps(message.reasoning_summary, ensure_ascii=False, default=str),
                    created_at,
                ),
            ).rowcount
            if not inserted:
                return
            row = connection.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
            title = row["title"] if row else "新会话"
            if message.role == "user" and title in {"新会话", "未命名会话", "新协作会话"}:
                title = summary[:48] or title
            connection.execute(
                """
                UPDATE sessions
                SET title = ?, summary = ?, mode = ?, updated_at = ?, last_message_at = ?
                WHERE id = ?
                """,
                (title, summary, mode, created_at, created_at, session_id),
            )

    def list_messages(self, session_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        self.ensure_session(session_id)
        bounded = max(1, min(5000, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT messages.*, rowid AS _rowid FROM messages WHERE session_id = ?
                    ORDER BY created_at DESC, rowid DESC LIMIT ?
                ) ORDER BY created_at ASC, _rowid ASC
                """,
                (str(session_id), bounded),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.pop("_rowid", None)
            try:
                item["attachments"] = json.loads(item.pop("attachments_json", "[]"))
            except (json.JSONDecodeError, TypeError):
                item["attachments"] = []
            try:
                item["generated_files"] = json.loads(
                    item.pop("generated_files_json", "[]")
                )
            except (json.JSONDecodeError, TypeError):
                item["generated_files"] = []
            try:
                item["reasoning_summary"] = json.loads(
                    item.pop("reasoning_summary_json", "[]")
                )
            except (json.JSONDecodeError, TypeError):
                item["reasoning_summary"] = []
            result.append(item)
        return result

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM messages WHERE id = ?",
                (str(message_id),),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        try:
            item["attachments"] = json.loads(item.pop("attachments_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            item["attachments"] = []
        try:
            item["generated_files"] = json.loads(item.pop("generated_files_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            item["generated_files"] = []
        try:
            item["reasoning_summary"] = json.loads(item.pop("reasoning_summary_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            item["reasoning_summary"] = []
        return item

    def chat_history(self, session_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        return self.list_messages(session_id, limit)

    def get_memory(self, session_id: str) -> dict[str, Any]:
        self.ensure_session(session_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM session_memory WHERE session_id = ?", (str(session_id),)
            ).fetchone()
        if row is None:
            return {
                "session_id": str(session_id), "summary": "", "project_note": "", "decisions": [],
                "preferences": [], "open_items": [], "entities": [], "message_count": 0, "updated_at": None,
            }
        item = dict(row)
        for field in ("decisions", "preferences", "open_items", "entities"):
            try:
                item[field] = json.loads(item.pop(f"{field}_json"))
            except (json.JSONDecodeError, TypeError):
                item[field] = []
        return item

    def save_memory(
        self,
        session_id: str,
        *,
        summary: str,
        project_note: str,
        decisions: list[str],
        preferences: list[str],
        open_items: list[str],
        message_count: int,
        entities: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.ensure_session(session_id)
        now = _utc_now()
        if entities is None:
            entities = list(self.get_memory(session_id).get("entities") or [])
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO session_memory
                   (session_id, summary, project_note, decisions_json, preferences_json, open_items_json, entities_json, message_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     summary=excluded.summary,
                     project_note=excluded.project_note,
                     decisions_json=excluded.decisions_json,
                     preferences_json=excluded.preferences_json,
                     open_items_json=excluded.open_items_json,
                     entities_json=excluded.entities_json,
                     message_count=excluded.message_count,
                     updated_at=excluded.updated_at""",
                (
                    str(session_id), str(summary)[:4000], str(project_note)[:4000],
                    json.dumps(decisions[:30], ensure_ascii=False),
                    json.dumps(preferences[:30], ensure_ascii=False),
                    json.dumps(open_items[:30], ensure_ascii=False),
                    json.dumps(entities[:100], ensure_ascii=False, default=str),
                    max(0, int(message_count)), now,
                ),
            )
        return self.get_memory(session_id)

    @staticmethod
    def _message_search_snippet(content: str, query: str, limit: int = 220) -> str:
        compact = " ".join(str(content or "").split())
        if len(compact) <= limit:
            return compact
        index = compact.casefold().find(query.casefold())
        if index < 0:
            return compact[:limit] + "…"
        radius = max(40, limit // 2)
        start = max(0, index - radius)
        end = min(len(compact), start + limit)
        return ("…" if start else "") + compact[start:end] + ("…" if end < len(compact) else "")

    def search_messages(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        needle = " ".join(str(query or "").split())[:300]
        if not needle:
            return []
        bounded = max(1, min(200, int(limit)))
        with self._lock, self._connect() as connection:
            rows: list[sqlite3.Row] = []
            fts_succeeded = False
            if len(needle) >= 3:
                try:
                    rows = connection.execute(
                        """
                        SELECT m.id AS message_id, m.session_id, m.author_model, m.role,
                               m.content, m.created_at, s.title, s.updated_at
                        FROM message_search f
                        JOIN messages m ON m.id = f.message_id
                        JOIN sessions s ON s.id = m.session_id
                        WHERE message_search MATCH ?
                        ORDER BY m.created_at DESC
                        LIMIT ?
                        """,
                        ('"' + needle.replace('"', '""') + '"', bounded),
                    ).fetchall()
                    fts_succeeded = True
                except sqlite3.OperationalError:
                    rows = []
            pattern = f"%{needle}%"
            if fts_succeeded and rows:
                title_rows = connection.execute(
                    """
                    SELECT m.id AS message_id, m.session_id, m.author_model, m.role,
                           m.content, m.created_at, s.title, s.updated_at
                    FROM messages m
                    JOIN sessions s ON s.id = m.session_id
                    WHERE s.title LIKE ? COLLATE NOCASE
                    ORDER BY m.created_at DESC
                    LIMIT ?
                    """,
                    (pattern, bounded),
                ).fetchall()
                by_id = {
                    str(row["message_id"]): row
                    for row in [*rows, *title_rows]
                }
                rows = sorted(
                    by_id.values(),
                    key=lambda row: str(row["created_at"]),
                    reverse=True,
                )[:bounded]
            elif not rows:
                rows = connection.execute(
                    """
                    SELECT m.id AS message_id, m.session_id, m.author_model, m.role,
                           m.content, m.created_at, s.title, s.updated_at
                    FROM messages m
                    JOIN sessions s ON s.id = m.session_id
                    WHERE m.content LIKE ? COLLATE NOCASE
                       OR m.author_model LIKE ? COLLATE NOCASE
                       OR s.title LIKE ? COLLATE NOCASE
                    ORDER BY m.created_at DESC
                    LIMIT ?
                    """,
                    (pattern, pattern, pattern, bounded),
                ).fetchall()
        return [
            {
                **dict(row),
                "snippet": self._message_search_snippet(row["content"], needle),
            }
            for row in rows
        ]
