from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))


from app.services.run_dispatcher import (  # noqa: E402
    RunDispatchCancelled,
    RunDispatcher,
    RunQueueFull,
    RunOwnershipConflict,
)
from app.models.schemas import ProviderConfig  # noqa: E402
from app.services.run_admission import (  # noqa: E402
    admission_resources_for_action,
)


@pytest.mark.asyncio
async def test_dispatcher_grants_capacity_and_promotes_fifo_waiters():
    dispatcher = RunDispatcher(capacity=1, queue_capacity=4)
    first = await dispatcher.submit(owner_id="owner-1", session_id="session-1")
    second = await dispatcher.submit(owner_id="owner-2", session_id="session-2")
    third = await dispatcher.submit(owner_id="owner-3", session_id="session-3")

    assert first.started is True
    assert second.status().queue_position == 1
    assert third.status().queue_position == 2

    await first.release()
    await second.wait_until_started()
    assert second.started is True
    assert third.status().queue_position == 1

    await second.release()
    await third.wait_until_started()
    await third.release()

    assert dispatcher.snapshot().as_dict() == {
        "accepting": True,
        "capacity": 1,
        "queue_capacity": 4,
        "running": 0,
        "queued": 0,
        "submitted": 3,
        "started": 3,
        "completed": 3,
        "cancelled": 0,
        "rejected": 0,
        "queue_high_water": 2,
        "interactive_running": 0,
        "background_running": 0,
        "interactive_queued": 0,
        "background_queued": 0,
        "interactive_weight": 3,
        "provider_capacity": 2,
        "gpu_capacity": 1,
        "resources": {},
    }


@pytest.mark.asyncio
async def test_dispatcher_enforces_one_owner_per_session_until_release():
    dispatcher = RunDispatcher(capacity=2)
    first = await dispatcher.submit(owner_id="owner-1", session_id="shared-session")

    with pytest.raises(RunOwnershipConflict) as error:
        await dispatcher.submit(owner_id="owner-2", session_id="shared-session")

    assert error.value.dispatch_id == first.dispatch_id
    assert error.value.state == "running"
    await first.release()

    replacement = await dispatcher.submit(
        owner_id="owner-2",
        session_id="shared-session",
    )
    assert replacement.started is True
    await replacement.release()


@pytest.mark.asyncio
async def test_dispatcher_rejects_without_displacing_when_queue_is_full():
    dispatcher = RunDispatcher(capacity=1, queue_capacity=1)
    running = await dispatcher.submit(owner_id="owner-1", session_id="session-1")
    queued = await dispatcher.submit(owner_id="owner-2", session_id="session-2")

    with pytest.raises(RunQueueFull, match="容量 1"):
        await dispatcher.submit(owner_id="owner-3", session_id="session-3")

    snapshot = dispatcher.snapshot()
    assert snapshot.running == 1
    assert snapshot.queued == 1
    assert snapshot.rejected == 1
    await queued.cancel()
    await running.release()


@pytest.mark.asyncio
async def test_dispatcher_cancels_queued_admission_and_updates_positions():
    dispatcher = RunDispatcher(capacity=1, queue_capacity=3)
    running = await dispatcher.submit(owner_id="owner-1", session_id="session-1")
    cancelled = await dispatcher.submit(owner_id="owner-2", session_id="session-2")
    survivor = await dispatcher.submit(owner_id="owner-3", session_id="session-3")
    cancelled_wait = asyncio.create_task(cancelled.wait_until_started())

    assert await cancelled.cancel() is True
    with pytest.raises(RunDispatchCancelled, match="已取消"):
        await cancelled_wait
    assert survivor.status().queue_position == 1

    await running.release()
    await survivor.wait_until_started()
    await survivor.release()
    assert dispatcher.snapshot().cancelled == 1


@pytest.mark.asyncio
async def test_dispatcher_running_cancel_releases_slot_from_task_finally():
    dispatcher = RunDispatcher(capacity=1)
    running = await dispatcher.submit(owner_id="owner-1", session_id="session-1")
    queued = await dispatcher.submit(owner_id="owner-2", session_id="session-2")
    entered = asyncio.Event()

    async def hold_slot():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await running.release(cancelled=True)

    task = asyncio.create_task(hold_slot())
    await running.bind_task(task)
    await entered.wait()
    assert await running.cancel() is True
    with pytest.raises(asyncio.CancelledError):
        await task

    await queued.wait_until_started()
    await queued.release()
    snapshot = dispatcher.snapshot()
    assert snapshot.cancelled == 1
    assert snapshot.completed == 1


@pytest.mark.asyncio
async def test_dispatcher_prioritizes_interactive_without_starving_background():
    dispatcher = RunDispatcher(
        capacity=1,
        queue_capacity=8,
        interactive_weight=2,
    )
    first = await dispatcher.submit(
        owner_id="interactive-1",
        session_id="session-1",
        kind="interactive",
    )
    background = await dispatcher.submit(
        owner_id="background-1",
        session_id="session-2",
        kind="background",
    )
    second = await dispatcher.submit(
        owner_id="interactive-2",
        session_id="session-3",
        kind="interactive",
    )
    third = await dispatcher.submit(
        owner_id="interactive-3",
        session_id="session-4",
        kind="interactive",
    )

    await first.release()
    await second.wait_until_started()
    assert background.queued is True

    await second.release()
    await background.wait_until_started()
    assert third.queued is True

    await background.release()
    await third.wait_until_started()
    await third.release()
    snapshot = dispatcher.snapshot()
    assert snapshot.completed == 4
    assert snapshot.interactive_queued == 0
    assert snapshot.background_queued == 0


@pytest.mark.asyncio
async def test_dispatcher_skips_resource_blocked_head_and_reports_usage():
    dispatcher = RunDispatcher(
        capacity=3,
        provider_capacity=1,
        gpu_capacity=1,
    )
    local = await dispatcher.submit(
        owner_id="local-1",
        session_id="session-local-1",
        resources=("provider:local", "gpu"),
    )
    blocked_local = await dispatcher.submit(
        owner_id="local-2",
        session_id="session-local-2",
        resources=("provider:local", "gpu"),
    )
    remote = await dispatcher.submit(
        owner_id="remote-1",
        session_id="session-remote-1",
        resources=("provider:remote",),
    )

    assert local.started is True
    assert blocked_local.queued is True
    assert remote.started is True
    snapshot = dispatcher.snapshot()
    assert snapshot.running == 2
    assert snapshot.queued == 1
    assert snapshot.resources == {
        "gpu": {"capacity": 1, "running": 1, "queued": 1},
        "provider:local": {"capacity": 1, "running": 1, "queued": 1},
        "provider:remote": {"capacity": 1, "running": 1, "queued": 0},
    }

    await remote.release()
    assert blocked_local.queued is True
    await local.release()
    await blocked_local.wait_until_started()
    await blocked_local.release()


def test_admission_resources_follow_targets_mentions_and_local_gpu():
    configs = [
        ProviderConfig(
            name="cloud",
            type="openai_compat",
            model="cloud-model",
            api_key_ref="CLOUD_KEY",
        ),
        ProviderConfig(
            name="local",
            type="local",
            model="local-model",
            api_key_ref="LOCAL_KEY",
        ),
    ]

    assert admission_resources_for_action(
        {"content": "请协作", "targets": ["all"]},
        configs,
    ) == ("gpu", "provider:cloud", "provider:local")
    assert admission_resources_for_action(
        {"content": "只用云端", "targets": ["cloud"]},
        configs,
    ) == ("provider:cloud",)
    assert admission_resources_for_action(
        {"content": "改由 @local 执行", "targets": ["cloud"]},
        configs,
    ) == ("gpu", "provider:local")
