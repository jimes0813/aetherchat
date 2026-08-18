from __future__ import annotations

import json
from typing import Any

from ._shared import (
    EVENT_STREAM_RETENTION_LIMIT,
    _utc_now,
)


class EventJournalMixin:
    def append_websocket_event(
        self,
        session_id: str,
        stream_id: str,
        event: dict[str, Any],
        *,
        retention_limit: int = EVENT_STREAM_RETENTION_LIMIT,
    ) -> dict[str, Any]:
        """Atomically allocate, persist, and return one stream event."""

        normalized_session_id = str(session_id).strip()
        normalized_stream_id = str(stream_id).strip()
        if not normalized_session_id or not normalized_stream_id:
            raise ValueError("session_id and stream_id are required")
        bounded_retention = max(1, min(100_000, int(retention_limit)))
        self.ensure_session(normalized_session_id)
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO event_streams
                    (session_id, stream_id, last_sequence, created_at, updated_at)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(session_id, stream_id) DO UPDATE SET
                    last_sequence = event_streams.last_sequence + 1,
                    updated_at = excluded.updated_at
                """,
                (normalized_session_id, normalized_stream_id, now, now),
            )
            row = connection.execute(
                """SELECT last_sequence FROM event_streams
                   WHERE session_id = ? AND stream_id = ?""",
                (normalized_session_id, normalized_stream_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("event stream sequence allocation failed")
            sequence = int(row["last_sequence"])
            persisted = {
                **event,
                "session_id": normalized_session_id,
                "stream_id": normalized_stream_id,
                "sequence": sequence,
            }
            event_id = str(persisted.get("event_id") or "").strip()
            event_type = str(persisted.get("type") or "").strip()
            occurred_at = str(persisted.get("occurred_at") or "").strip()
            if not event_id or not event_type or not occurred_at:
                raise ValueError(
                    "persisted websocket events require event_id, type, and occurred_at"
                )
            connection.execute(
                """
                INSERT INTO event_journal
                    (event_id, session_id, stream_id, sequence, event_type,
                     payload_json, occurred_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    normalized_session_id,
                    normalized_stream_id,
                    sequence,
                    event_type,
                    json.dumps(persisted, ensure_ascii=False),
                    occurred_at,
                    now,
                ),
            )
            prune_through = sequence - bounded_retention
            if prune_through > 0:
                connection.execute(
                    """DELETE FROM event_journal
                       WHERE session_id = ? AND stream_id = ? AND sequence <= ?""",
                    (
                        normalized_session_id,
                        normalized_stream_id,
                        prune_through,
                    ),
                )
        return persisted

    def websocket_stream_state(
        self,
        session_id: str,
        stream_id: str,
    ) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT streams.last_sequence,
                       MIN(events.sequence) AS first_available_sequence
                FROM event_streams streams
                LEFT JOIN event_journal events
                  ON events.session_id = streams.session_id
                 AND events.stream_id = streams.stream_id
                WHERE streams.session_id = ? AND streams.stream_id = ?
                GROUP BY streams.session_id, streams.stream_id
                """,
                (str(session_id), str(stream_id)),
            ).fetchone()
        if row is None:
            return {
                "exists": False,
                "first_available_sequence": None,
                "last_sequence": 0,
            }
        first = row["first_available_sequence"]
        return {
            "exists": True,
            "first_available_sequence": int(first) if first is not None else None,
            "last_sequence": int(row["last_sequence"]),
        }

    def list_websocket_events_after(
        self,
        session_id: str,
        stream_id: str,
        cursor: int,
        *,
        limit: int = 256,
    ) -> list[dict[str, Any]]:
        normalized_cursor = max(0, int(cursor))
        bounded_limit = max(1, min(1000, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM event_journal
                WHERE session_id = ? AND stream_id = ? AND sequence > ?
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (
                    str(session_id),
                    str(stream_id),
                    normalized_cursor,
                    bounded_limit,
                ),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict):
                raise RuntimeError("event journal payload is not a JSON object")
            events.append(payload)
        return events
