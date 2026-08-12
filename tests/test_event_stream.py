from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="aetherchat-event-stream-tests-"))
if "app.services.chat_database" not in sys.modules:
    os.environ["AETHERCHAT_DATA_DIR"] = str(TEST_DATA_DIR)


@atexit.register
def _cleanup_test_data() -> None:
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


from app.services.chat_database import ChatDatabase  # noqa: E402
from app.services.event_envelope import EventEnvelopeBuilder  # noqa: E402
from app.services.event_stream import (  # noqa: E402
    MAX_EVENT_CURSOR,
    WebSocketEventStream,
    normalize_stream_id,
    parse_event_cursor,
)


def _draft(event_id: str, stream_id: str) -> dict:
    return EventEnvelopeBuilder(
        "session-1",
        event_id_factory=lambda: event_id,
        clock=lambda: datetime(2026, 8, 11, 8, 0, tzinfo=UTC),
    ).build({"type": "delta", "delta": event_id}, stream_id=stream_id)


def test_event_stream_identity_and_cursor_validation():
    assert normalize_stream_id("tab.stream-1") == "tab.stream-1"
    assert normalize_stream_id("invalid stream id") != "invalid stream id"
    assert parse_event_cursor(None) == 0
    assert parse_event_cursor(str(MAX_EVENT_CURSOR)) == MAX_EVENT_CURSOR
    with pytest.raises(ValueError, match="non-negative"):
        parse_event_cursor("-1")
    with pytest.raises(ValueError, match="supported range"):
        parse_event_cursor(str(MAX_EVENT_CURSOR + 1))


def test_event_journal_replays_across_instances_and_detects_retention_gap(tmp_path):
    database = ChatDatabase(tmp_path / "events.sqlite3")
    for index in range(1, 4):
        persisted = database.append_websocket_event(
            "session-1",
            "stream-1",
            _draft(f"event-{index}", "stream-1"),
            retention_limit=2,
        )
        assert persisted["sequence"] == index

    stream = WebSocketEventStream(database, "session-1", "stream-1")
    state = database.websocket_stream_state("session-1", "stream-1")
    assert state == {
        "exists": True,
        "first_available_sequence": 2,
        "last_sequence": 3,
    }
    assert stream.cursor_is_replayable(0) is False
    assert stream.cursor_is_replayable(1) is True
    assert stream.cursor_is_replayable(4) is False
    assert [event["event_id"] for event in stream.replay_after(1)] == [
        "event-2",
        "event-3",
    ]

    other = WebSocketEventStream(database, "session-1", "stream-2")
    assert other.cursor_is_replayable(0) is True
    assert other.append({"type": "ack"})["sequence"] == 1


def test_event_journal_allocates_unique_monotonic_sequences_concurrently(tmp_path):
    database = ChatDatabase(tmp_path / "concurrent-events.sqlite3")

    def append(index: int) -> int:
        persisted = database.append_websocket_event(
            "session-1",
            "stream-1",
            _draft(f"event-{index}", "stream-1"),
            retention_limit=100,
        )
        return int(persisted["sequence"])

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(append, range(32)))

    assert sorted(sequences) == list(range(1, 33))
    assert [event["sequence"] for event in database.list_websocket_events_after(
        "session-1",
        "stream-1",
        0,
        limit=100,
    )] == list(range(1, 33))
