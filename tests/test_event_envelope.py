from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.event_envelope import EventEnvelopeBuilder  # noqa: E402


def test_event_envelope_assigns_authoritative_monotonic_metadata():
    event_ids = iter(("event-1", "event-2"))
    builder = EventEnvelopeBuilder(
        "session-1",
        event_id_factory=lambda: next(event_ids),
        clock=lambda: datetime(2026, 8, 11, 6, 30, tzinfo=UTC),
    )

    first = builder.build({
        "type": "ack",
        "contract_version": "spoofed",
        "event_id": "spoofed",
        "sequence": 900,
        "session_id": "other-session",
        "stream_id": "spoofed-stream",
    }, request_id="request-1", run_id="run-1", stream_id="stream-1")
    second = builder.build(
        {"type": "delta", "request_id": "request-2"},
        stream_id="stream-1",
    )

    assert first == {
        "type": "ack",
        "contract_version": "1",
        "event_id": "event-1",
        "sequence": 1,
        "request_id": "request-1",
        "session_id": "session-1",
        "stream_id": "stream-1",
        "run_id": "run-1",
        "step_id": None,
        "occurred_at": "2026-08-11T06:30:00Z",
    }
    assert second["event_id"] == "event-2"
    assert second["sequence"] == 2
    assert second["request_id"] == "request-2"
    assert second["run_id"] is None
    assert builder.sequence == 2


def test_event_envelope_extracts_run_and_step_relations_from_event_payloads():
    builder = EventEnvelopeBuilder("session-2", event_id_factory=lambda: "event")

    plan_event = builder.build({
        "type": "plan_step_update",
        "plan_step": {"id": "step-1", "run_id": "run-1"},
    })
    review_event = builder.build({
        "type": "review_recorded",
        "review": {"run_id": "run-2", "plan_step_id": "step-2"},
    })

    assert (plan_event["run_id"], plan_event["step_id"]) == ("run-1", "step-1")
    assert (review_event["run_id"], review_event["step_id"]) == ("run-2", "step-2")
    assert plan_event["occurred_at"].endswith("Z")
