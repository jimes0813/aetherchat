"""Versioned outbound WebSocket event envelopes.

The builder remains transport-agnostic. Persistent streams may replace its
connection-local sequence while atomically appending the returned envelope.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

EVENT_CONTRACT_VERSION = "1"


def _identifier(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _nested_identifier(
    payload: Mapping[str, Any],
    container: str,
    key: str,
) -> str | None:
    nested = payload.get(container)
    if not isinstance(nested, Mapping):
        return None
    return _identifier(nested.get(key))


class EventEnvelopeBuilder:
    """Add authoritative v1 metadata to outbound events."""

    def __init__(
        self,
        session_id: str,
        *,
        event_id_factory: Callable[[], object] = uuid4,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_id = str(session_id)
        self._event_id_factory = event_id_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sequence = 0

    @property
    def sequence(self) -> int:
        return self._sequence

    @staticmethod
    def _run_id(
        payload: Mapping[str, Any],
        fallback: object | None,
    ) -> str | None:
        return (
            _identifier(payload.get("run_id"))
            or _nested_identifier(payload, "agent_run", "id")
            or _nested_identifier(payload, "plan_step", "run_id")
            or _nested_identifier(payload, "review", "run_id")
            or _nested_identifier(payload, "workspace", "run_id")
            or _identifier(fallback)
        )

    @staticmethod
    def _step_id(payload: Mapping[str, Any]) -> str | None:
        return (
            _identifier(payload.get("step_id"))
            or _identifier(payload.get("plan_step_id"))
            or _nested_identifier(payload, "plan_step", "id")
            or _nested_identifier(payload, "review", "plan_step_id")
            or _nested_identifier(payload, "workspace", "plan_step_id")
        )

    def build(
        self,
        payload: Mapping[str, Any],
        *,
        request_id: object | None = None,
        run_id: object | None = None,
        stream_id: object | None = None,
    ) -> dict[str, Any]:
        """Return a copy with reserved envelope fields set by the server."""

        self._sequence += 1
        occurred_at = self._clock()
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        occurred_at = occurred_at.astimezone(UTC)
        envelope = dict(payload)
        envelope.update({
            "contract_version": EVENT_CONTRACT_VERSION,
            "event_id": str(self._event_id_factory()),
            "sequence": self._sequence,
            "request_id": (
                _identifier(payload.get("request_id"))
                or _identifier(request_id)
            ),
            "session_id": self.session_id,
            "stream_id": _identifier(stream_id),
            "run_id": self._run_id(payload, run_id),
            "step_id": self._step_id(payload),
            "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
        })
        return envelope
