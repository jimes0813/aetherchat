"""Persistent ordered streams for the versioned WebSocket contract."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any
from uuid import uuid4

from .chat_database import ChatDatabase
from .event_envelope import EventEnvelopeBuilder

CURSOR_RESET_CLOSE_CODE = 4009
MAX_EVENT_CURSOR = (2**53) - 1
REPLAY_BATCH_SIZE = 256
_STREAM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def normalize_stream_id(value: object | None) -> str:
    candidate = str(value or "").strip()
    if _STREAM_ID.fullmatch(candidate):
        return candidate
    return str(uuid4())


def parse_event_cursor(value: object | None) -> int:
    candidate = str(value or "0").strip()
    if not candidate.isascii() or not candidate.isdecimal():
        raise ValueError("cursor must be a non-negative integer")
    cursor = int(candidate)
    if cursor > MAX_EVENT_CURSOR:
        raise ValueError("cursor exceeds the supported range")
    return cursor


class WebSocketEventStream:
    """Append-before-send journal and bounded cursor replay for one client."""

    def __init__(
        self,
        database: ChatDatabase,
        session_id: str,
        stream_id: str,
        *,
        envelope_builder: EventEnvelopeBuilder | None = None,
    ) -> None:
        self.database = database
        self.session_id = str(session_id)
        self.stream_id = str(stream_id)
        self.envelope_builder = envelope_builder or EventEnvelopeBuilder(session_id)

    def append(
        self,
        payload: Mapping[str, Any],
        *,
        request_id: object | None = None,
        run_id: object | None = None,
    ) -> dict[str, Any]:
        draft = self.envelope_builder.build(
            payload,
            request_id=request_id,
            run_id=run_id,
            stream_id=self.stream_id,
        )
        return self.database.append_websocket_event(
            self.session_id,
            self.stream_id,
            draft,
        )

    def cursor_is_replayable(self, cursor: int) -> bool:
        state = self.database.websocket_stream_state(
            self.session_id,
            self.stream_id,
        )
        if not state["exists"]:
            return cursor == 0
        last_sequence = int(state["last_sequence"])
        if cursor > last_sequence:
            return False
        first_available = state["first_available_sequence"]
        if first_available is None:
            return cursor == last_sequence
        return cursor >= int(first_available) - 1

    def replay_after(self, cursor: int) -> Iterator[dict[str, Any]]:
        after = max(0, int(cursor))
        while True:
            batch = self.database.list_websocket_events_after(
                self.session_id,
                self.stream_id,
                after,
                limit=REPLAY_BATCH_SIZE,
            )
            if not batch:
                return
            for event in batch:
                after = int(event["sequence"])
                yield event
            if len(batch) < REPLAY_BATCH_SIZE:
                return
