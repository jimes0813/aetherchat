from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))


from app.services.outbound_events import (  # noqa: E402
    OutboundEventQueue,
    OutboundEventWriter,
    OutboundQueueClosed,
    SlowConsumerError,
)


@pytest.mark.asyncio
async def test_queue_losslessly_coalesces_only_compatible_adjacent_deltas():
    queue = OutboundEventQueue(capacity=8, coalesce_window_seconds=0)
    await queue.put(
        {"type": "delta", "model": "model-a", "delta": "你", "ts": "first"},
        request_id="request-1",
        run_id="run-1",
    )
    await queue.put(
        {"type": "delta", "model": "model-a", "delta": "好", "ts": "second"},
        request_id="request-1",
        run_id="run-1",
    )
    await queue.put(
        {"type": "delta", "model": "model-a", "delta": "!"},
        request_id="request-2",
        run_id="run-1",
    )
    await queue.put(
        {"type": "delta", "model": "model-b", "delta": "done"},
        request_id="request-2",
        run_id="run-1",
    )

    first = await queue.get()
    second = await queue.get()
    third = await queue.get()

    assert first.payload == {
        "type": "delta",
        "model": "model-a",
        "delta": "你好",
        "ts": "second",
        "coalesced_count": 2,
    }
    assert first.request_id == "request-1"
    assert second.payload["delta"] == "!"
    assert second.request_id == "request-2"
    assert third.payload["model"] == "model-b"
    assert queue.snapshot().coalesced_fragments == 1


@pytest.mark.asyncio
async def test_queue_applies_backpressure_at_capacity_without_dropping_events():
    queue = OutboundEventQueue(capacity=1, coalesce_window_seconds=0)
    await queue.put({"type": "delta", "model": "model-a", "delta": "first"})
    blocked = asyncio.create_task(queue.put(
        {"type": "delta", "model": "model-b", "delta": "second"}
    ))
    await asyncio.sleep(0)

    assert not blocked.done()
    assert queue.snapshot().backpressure_waits == 1
    assert (await queue.get()).payload["delta"] == "first"
    await blocked
    assert (await queue.get()).payload["delta"] == "second"
    snapshot = queue.snapshot()
    assert snapshot.enqueued == 2
    assert snapshot.high_water == 1


@pytest.mark.asyncio
async def test_writer_persists_before_send_and_acknowledges_critical_events():
    queue = OutboundEventQueue(coalesce_window_seconds=0)
    order: list[str] = []

    def prepare(item):
        order.append("persist")
        return {**item.payload, "sequence": 1}

    async def send(outbound):
        assert outbound["sequence"] == 1
        order.append("send")

    writer = OutboundEventWriter(queue, prepare=prepare, send=send)
    writer_task = asyncio.create_task(writer.run())
    delivered = await queue.put({"type": "ack"}, wait_for_delivery=True)
    await queue.close()
    await writer_task

    assert delivered == {"type": "ack", "sequence": 1}
    assert order == ["persist", "send"]
    assert queue.snapshot().emitted == 1


@pytest.mark.asyncio
async def test_writer_times_out_slow_consumer_and_releases_waiting_producers():
    queue = OutboundEventQueue(coalesce_window_seconds=0)
    failures: list[Exception] = []
    send_started = asyncio.Event()

    def prepare(item):
        return {**item.payload, "sequence": 7}

    async def send(_outbound):
        send_started.set()
        await asyncio.Event().wait()

    async def on_failure(error):
        failures.append(error)

    writer = OutboundEventWriter(
        queue,
        prepare=prepare,
        send=send,
        send_timeout_seconds=0.01,
        on_failure=on_failure,
    )
    writer_task = asyncio.create_task(writer.run())
    producer = asyncio.create_task(queue.put(
        {"type": "message_complete"},
        wait_for_delivery=True,
    ))
    await send_started.wait()

    with pytest.raises(SlowConsumerError, match="send timed out"):
        await producer
    await writer_task

    assert len(failures) == 1
    assert isinstance(failures[0], SlowConsumerError)
    snapshot = queue.snapshot()
    assert snapshot.closed is True
    assert snapshot.emitted == 0


@pytest.mark.asyncio
async def test_closing_queue_releases_capacity_waiters():
    queue = OutboundEventQueue(capacity=1, coalesce_window_seconds=0)
    await queue.put({"type": "ack"})
    blocked = asyncio.create_task(queue.put({"type": "system"}))
    await asyncio.sleep(0)
    await queue.close(OutboundQueueClosed("test shutdown"))

    with pytest.raises(OutboundQueueClosed, match="test shutdown"):
        await blocked
