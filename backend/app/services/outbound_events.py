"""Bounded outbound WebSocket buffering with lossless delta coalescing."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from starlette.websockets import WebSocketDisconnect

DEFAULT_OUTBOUND_QUEUE_CAPACITY = 128
DEFAULT_DELTA_COALESCE_WINDOW_SECONDS = 0.016
DEFAULT_MAX_COALESCED_DELTA_CHARS = 16_384
DEFAULT_OUTBOUND_SEND_TIMEOUT_SECONDS = 10.0
SLOW_CONSUMER_CLOSE_CODE = 1013


class OutboundQueueClosed(RuntimeError):
    """Raised when a producer writes after the connection queue has closed."""


class OutboundDeliveryError(RuntimeError):
    """Raised when the single outbound writer cannot deliver an event."""


class SlowConsumerError(OutboundDeliveryError):
    """Raised when a WebSocket write exceeds the bounded delivery timeout."""


@dataclass(frozen=True)
class OutboundQueueSnapshot:
    capacity: int
    depth: int
    high_water: int
    enqueued: int
    dequeued: int
    emitted: int
    coalesced_fragments: int
    backpressure_waits: int
    closed: bool


@dataclass
class QueuedOutboundEvent:
    payload: dict[str, Any]
    request_id: str | None
    run_id: str | None
    enqueued_at: float
    delivery: asyncio.Future[dict[str, Any]] | None = None

    def resolve(self, outbound: dict[str, Any]) -> None:
        if self.delivery is not None and not self.delivery.done():
            self.delivery.set_result(outbound)

    def reject(self, error: Exception) -> None:
        if self.delivery is not None and not self.delivery.done():
            self.delivery.set_exception(error)


def _identifier(value: object | None) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def _delta_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return fields that must remain identical for adjacent deltas to merge."""

    return {
        key: value
        for key, value in payload.items()
        if key not in {"delta", "ts", "coalesced_count"}
    }


def _fragment_count(payload: Mapping[str, Any]) -> int:
    value = payload.get("coalesced_count", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 1
    return int(value)


class OutboundEventQueue:
    """A bounded FIFO that only coalesces compatible adjacent text deltas."""

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_OUTBOUND_QUEUE_CAPACITY,
        coalesce_window_seconds: float = DEFAULT_DELTA_COALESCE_WINDOW_SECONDS,
        max_coalesced_delta_chars: int = DEFAULT_MAX_COALESCED_DELTA_CHARS,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if coalesce_window_seconds < 0:
            raise ValueError("coalesce_window_seconds cannot be negative")
        if max_coalesced_delta_chars < 1:
            raise ValueError("max_coalesced_delta_chars must be at least 1")
        self.capacity = capacity
        self.coalesce_window_seconds = coalesce_window_seconds
        self.max_coalesced_delta_chars = max_coalesced_delta_chars
        self._condition = asyncio.Condition()
        self._items: deque[QueuedOutboundEvent] = deque()
        self._closed = False
        self._closed_error: Exception = OutboundQueueClosed(
            "outbound event queue is closed"
        )
        self._high_water = 0
        self._enqueued = 0
        self._dequeued = 0
        self._emitted = 0
        self._coalesced_fragments = 0
        self._backpressure_waits = 0

    @staticmethod
    def _is_delta(payload: Mapping[str, Any]) -> bool:
        return payload.get("type") == "delta" and isinstance(
            payload.get("delta"), str
        )

    def _merge_tail(
        self,
        payload: dict[str, Any],
        *,
        request_id: str | None,
        run_id: str | None,
    ) -> bool:
        if not self._items or not self._is_delta(payload):
            return False
        tail = self._items[-1]
        if (
            not self._is_delta(tail.payload)
            or tail.request_id != request_id
            or tail.run_id != run_id
            or _delta_metadata(tail.payload) != _delta_metadata(payload)
        ):
            return False
        previous_delta = str(tail.payload["delta"])
        next_delta = str(payload["delta"])
        if len(previous_delta) + len(next_delta) > self.max_coalesced_delta_chars:
            return False
        merged = {**tail.payload, **payload}
        merged["delta"] = previous_delta + next_delta
        merged["coalesced_count"] = (
            _fragment_count(tail.payload) + _fragment_count(payload)
        )
        tail.payload = merged
        self._enqueued += 1
        self._coalesced_fragments += 1
        self._condition.notify_all()
        return True

    async def put(
        self,
        payload: Mapping[str, Any],
        *,
        request_id: object | None = None,
        run_id: object | None = None,
        wait_for_delivery: bool = False,
    ) -> dict[str, Any] | None:
        """Enqueue one event, blocking at capacity instead of dropping data."""

        copied = dict(payload)
        normalized_request_id = _identifier(request_id)
        normalized_run_id = _identifier(run_id)
        waited_for_capacity = False
        delivery: asyncio.Future[dict[str, Any]] | None = None
        async with self._condition:
            while True:
                if self._closed:
                    raise self._closed_error
                if self._merge_tail(
                    copied,
                    request_id=normalized_request_id,
                    run_id=normalized_run_id,
                ):
                    return None
                if len(self._items) < self.capacity:
                    if wait_for_delivery:
                        delivery = asyncio.get_running_loop().create_future()
                    self._items.append(QueuedOutboundEvent(
                        payload=copied,
                        request_id=normalized_request_id,
                        run_id=normalized_run_id,
                        enqueued_at=asyncio.get_running_loop().time(),
                        delivery=delivery,
                    ))
                    self._enqueued += 1
                    self._high_water = max(self._high_water, len(self._items))
                    self._condition.notify_all()
                    break
                if not waited_for_capacity:
                    self._backpressure_waits += 1
                    waited_for_capacity = True
                await self._condition.wait()
        if delivery is None:
            return None
        return await delivery

    async def get(self) -> QueuedOutboundEvent:
        """Return the next event after a short, bounded delta merge window."""

        async with self._condition:
            while True:
                while not self._items:
                    if self._closed:
                        raise OutboundQueueClosed(
                            "outbound event queue is closed"
                        )
                    await self._condition.wait()
                first = self._items[0]
                if not self._is_delta(first.payload) or len(self._items) > 1:
                    break
                remaining = (
                    first.enqueued_at
                    + self.coalesce_window_seconds
                    - asyncio.get_running_loop().time()
                )
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                except TimeoutError:
                    break
            item = self._items.popleft()
            self._dequeued += 1
            self._condition.notify_all()
            return item

    def mark_emitted(self) -> None:
        self._emitted += 1

    async def close(self, error: Exception | None = None) -> None:
        pending: list[QueuedOutboundEvent]
        async with self._condition:
            if self._closed:
                return
            self._closed = True
            self._closed_error = error or OutboundQueueClosed(
                "outbound event queue is closed"
            )
            pending = list(self._items)
            self._items.clear()
            self._condition.notify_all()
        for item in pending:
            item.reject(self._closed_error)

    def snapshot(self) -> OutboundQueueSnapshot:
        return OutboundQueueSnapshot(
            capacity=self.capacity,
            depth=len(self._items),
            high_water=self._high_water,
            enqueued=self._enqueued,
            dequeued=self._dequeued,
            emitted=self._emitted,
            coalesced_fragments=self._coalesced_fragments,
            backpressure_waits=self._backpressure_waits,
            closed=self._closed,
        )


PrepareOutbound = Callable[[QueuedOutboundEvent], dict[str, Any]]
SendOutbound = Callable[[dict[str, Any]], Awaitable[None]]
HandleWriterFailure = Callable[[Exception], Awaitable[None]]


class OutboundEventWriter:
    """Drain one outbound queue through one append-before-send code path."""

    def __init__(
        self,
        queue: OutboundEventQueue,
        *,
        prepare: PrepareOutbound,
        send: SendOutbound,
        send_timeout_seconds: float = DEFAULT_OUTBOUND_SEND_TIMEOUT_SECONDS,
        on_failure: HandleWriterFailure | None = None,
    ) -> None:
        if send_timeout_seconds <= 0:
            raise ValueError("send_timeout_seconds must be positive")
        self.queue = queue
        self.prepare = prepare
        self.send = send
        self.send_timeout_seconds = send_timeout_seconds
        self.on_failure = on_failure

    @staticmethod
    def _producer_error(error: Exception) -> Exception:
        if isinstance(
            error,
            (OutboundDeliveryError, OutboundQueueClosed, WebSocketDisconnect),
        ):
            return error
        return OutboundDeliveryError("outbound WebSocket delivery failed")

    async def _fail(
        self,
        item: QueuedOutboundEvent,
        error: Exception,
    ) -> None:
        producer_error = self._producer_error(error)
        item.reject(producer_error)
        await self.queue.close(producer_error)
        if self.on_failure is not None:
            with suppress(Exception):
                await self.on_failure(error)

    async def run(self) -> None:
        while True:
            try:
                item = await self.queue.get()
            except OutboundQueueClosed:
                return
            try:
                outbound = self.prepare(item)
                try:
                    await asyncio.wait_for(
                        self.send(outbound),
                        timeout=self.send_timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise SlowConsumerError(
                        "outbound WebSocket send timed out"
                    ) from exc
            except asyncio.CancelledError:
                stopped = OutboundQueueClosed("outbound event writer stopped")
                item.reject(stopped)
                await self.queue.close(stopped)
                raise
            except Exception as exc:
                await self._fail(item, exc)
                return
            self.queue.mark_emitted()
            item.resolve(outbound)
