from __future__ import annotations

import asyncio
import os
from collections import Counter, deque
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

RunKind = Literal["interactive", "background"]
RunDispatchState = Literal[
    "queued",
    "running",
    "cancelling",
    "completed",
    "cancelled",
]


def _bounded_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


DEFAULT_RUN_CONCURRENCY = _bounded_env(
    "AETHERCHAT_RUN_CONCURRENCY",
    2,
    minimum=1,
    maximum=8,
)
DEFAULT_RUN_QUEUE_CAPACITY = _bounded_env(
    "AETHERCHAT_RUN_QUEUE_CAPACITY",
    64,
    minimum=1,
    maximum=512,
)
DEFAULT_PROVIDER_CONCURRENCY = _bounded_env(
    "AETHERCHAT_PROVIDER_CONCURRENCY",
    2,
    minimum=1,
    maximum=8,
)
DEFAULT_GPU_CONCURRENCY = _bounded_env(
    "AETHERCHAT_GPU_CONCURRENCY",
    1,
    minimum=1,
    maximum=4,
)
DEFAULT_INTERACTIVE_WEIGHT = _bounded_env(
    "AETHERCHAT_INTERACTIVE_WEIGHT",
    3,
    minimum=1,
    maximum=8,
)
GPU_RESOURCE = "gpu"
PROVIDER_RESOURCE_PREFIX = "provider:"


class RunDispatchError(RuntimeError):
    """Base class for admission failures exposed to WebSocket callers."""


class RunOwnershipConflict(RunDispatchError):
    def __init__(self, session_id: str, dispatch_id: str, state: RunDispatchState):
        super().__init__("当前会话已有运行中的任务；请先中断或等待完成。")
        self.session_id = session_id
        self.dispatch_id = dispatch_id
        self.state = state


class RunQueueFull(RunDispatchError):
    def __init__(self, capacity: int):
        super().__init__(f"运行队列已满（容量 {capacity}）；请稍后重试。")
        self.capacity = capacity


class RunDispatcherStopped(RunDispatchError):
    pass


class RunDispatchCancelled(RunDispatchError):
    pass


@dataclass(frozen=True)
class RunDispatchStatus:
    dispatch_id: str
    owner_id: str
    session_id: str
    kind: RunKind
    state: RunDispatchState
    queue_position: int | None
    enqueued_at: str
    started_at: str | None
    resources: tuple[str, ...]


@dataclass(frozen=True)
class RunDispatcherSnapshot:
    accepting: bool
    capacity: int
    queue_capacity: int
    running: int
    queued: int
    submitted: int
    started: int
    completed: int
    cancelled: int
    rejected: int
    queue_high_water: int
    interactive_running: int
    background_running: int
    interactive_queued: int
    background_queued: int
    interactive_weight: int
    provider_capacity: int
    gpu_capacity: int
    resources: dict[str, dict[str, int]]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _DispatchEntry:
    dispatch_id: str
    owner_id: str
    session_id: str
    kind: RunKind
    state: RunDispatchState
    enqueued_at: str
    started_at: str | None
    resources: tuple[str, ...]
    admission: asyncio.Future[None]
    task: asyncio.Task[Any] | None = None


def _observe_future_exception(future: asyncio.Future[None]) -> None:
    if not future.cancelled():
        future.exception()


class RunDispatchHandle:
    """A single admission lease returned immediately to a caller."""

    def __init__(self, dispatcher: RunDispatcher, entry: _DispatchEntry):
        self._dispatcher = dispatcher
        self._entry = entry

    @property
    def dispatch_id(self) -> str:
        return self._entry.dispatch_id

    @property
    def session_id(self) -> str:
        return self._entry.session_id

    @property
    def started(self) -> bool:
        return self._entry.state in {"running", "cancelling"}

    @property
    def queued(self) -> bool:
        return self._entry.state == "queued"

    def status(self) -> RunDispatchStatus:
        return self._dispatcher.status_for(self._entry)

    async def wait_until_started(self) -> RunDispatchStatus:
        await asyncio.shield(self._entry.admission)
        return self.status()

    async def bind_task(self, task: asyncio.Task[Any]) -> None:
        await self._dispatcher.bind_task(self._entry, task)

    async def cancel(self) -> bool:
        return await self._dispatcher.cancel(self._entry)

    async def release(self, *, cancelled: bool = False) -> None:
        await self._dispatcher.release(self._entry, cancelled=cancelled)


class RunDispatcher:
    """Process-local bounded admission for interactive and background runs.

    The dispatcher owns weighted fairness, resource capacity, and session
    exclusivity only. Durable Run/Job state remains in ChatDatabase, while
    callers retain execution ownership.
    """

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_RUN_CONCURRENCY,
        queue_capacity: int = DEFAULT_RUN_QUEUE_CAPACITY,
        provider_capacity: int = DEFAULT_PROVIDER_CONCURRENCY,
        gpu_capacity: int = DEFAULT_GPU_CONCURRENCY,
        interactive_weight: int = DEFAULT_INTERACTIVE_WEIGHT,
        resource_capacities: Mapping[str, int] | None = None,
    ):
        self.capacity = max(1, min(int(capacity), 8))
        self.queue_capacity = max(1, min(int(queue_capacity), 512))
        self.provider_capacity = max(1, min(int(provider_capacity), 8))
        self.gpu_capacity = max(1, min(int(gpu_capacity), 4))
        self.interactive_weight = max(1, min(int(interactive_weight), 8))
        self._resource_capacities = {
            str(key).strip().casefold(): max(1, min(int(value), 64))
            for key, value in (resource_capacities or {}).items()
            if str(key).strip()
        }
        self._lock = asyncio.Lock()
        self._changed = asyncio.Condition(self._lock)
        self._accepting = True
        self._queue: deque[_DispatchEntry] = deque()
        self._running: dict[str, _DispatchEntry] = {}
        self._resource_running: Counter[str] = Counter()
        self._entries: dict[str, _DispatchEntry] = {}
        self._session_owners: dict[str, str] = {}
        self._interactive_streak = 0
        self._submitted = 0
        self._started = 0
        self._completed = 0
        self._cancelled = 0
        self._rejected = 0
        self._queue_high_water = 0

    async def start(self) -> None:
        if self._entries:
            raise RuntimeError("RunDispatcher cannot restart with active admissions")
        self._lock = asyncio.Lock()
        self._changed = asyncio.Condition(self._lock)
        self._accepting = True
        self._queue.clear()
        self._running.clear()
        self._resource_running.clear()
        self._session_owners.clear()
        self._interactive_streak = 0
        self._submitted = 0
        self._started = 0
        self._completed = 0
        self._cancelled = 0
        self._rejected = 0
        self._queue_high_water = 0

    async def stop(self) -> None:
        running_tasks: list[asyncio.Task[Any]] = []
        async with self._lock:
            self._accepting = False
            for entry in self._queue:
                entry.state = "cancelled"
                if not entry.admission.done():
                    entry.admission.set_exception(
                        RunDispatcherStopped("运行调度器正在关闭")
                    )
                self._cancelled += 1
            for entry in self._running.values():
                entry.state = "cancelled"
                if entry.task is not None and not entry.task.done():
                    running_tasks.append(entry.task)
                self._cancelled += 1
            self._queue.clear()
            self._running.clear()
            self._resource_running.clear()
            self._entries.clear()
            self._session_owners.clear()
            self._changed.notify_all()
        current = asyncio.current_task()
        for task in running_tasks:
            if task is not current:
                task.cancel()
        await asyncio.gather(
            *(task for task in running_tasks if task is not current),
            return_exceptions=True,
        )

    async def submit(
        self,
        *,
        owner_id: str,
        session_id: str,
        kind: RunKind = "interactive",
        resources: Iterable[str] = (),
    ) -> RunDispatchHandle:
        normalized_owner = str(owner_id).strip()
        normalized_session = str(session_id).strip()
        if not normalized_owner or not normalized_session:
            raise ValueError("owner_id and session_id are required")
        if kind not in {"interactive", "background"}:
            raise ValueError("kind must be interactive or background")
        normalized_resources = tuple(sorted({
            normalized
            for value in resources
            if (normalized := str(value).strip().casefold())
        }))
        async with self._lock:
            if not self._accepting:
                self._rejected += 1
                raise RunDispatcherStopped("运行调度器尚未就绪")
            existing_id = self._session_owners.get(normalized_session)
            if existing_id:
                existing = self._entries[existing_id]
                self._rejected += 1
                raise RunOwnershipConflict(
                    normalized_session,
                    existing.dispatch_id,
                    existing.state,
                )
            should_queue = (
                bool(self._queue)
                or len(self._running) >= self.capacity
                or not self._resources_available_locked(normalized_resources)
            )
            if should_queue and len(self._queue) >= self.queue_capacity:
                self._rejected += 1
                raise RunQueueFull(self.queue_capacity)
            admission = asyncio.get_running_loop().create_future()
            admission.add_done_callback(_observe_future_exception)
            entry = _DispatchEntry(
                dispatch_id=str(uuid4()),
                owner_id=normalized_owner,
                session_id=normalized_session,
                kind=kind,
                state="queued",
                enqueued_at=datetime.now(UTC).isoformat(),
                started_at=None,
                resources=normalized_resources,
                admission=admission,
            )
            self._submitted += 1
            self._entries[entry.dispatch_id] = entry
            self._session_owners[normalized_session] = entry.dispatch_id
            if should_queue:
                self._queue.append(entry)
                self._queue_high_water = max(
                    self._queue_high_water,
                    len(self._queue),
                )
                self._promote_locked()
            else:
                self._grant_locked(entry)
            return RunDispatchHandle(self, entry)

    def _resource_capacity(self, resource: str) -> int:
        explicit = self._resource_capacities.get(resource)
        if explicit is not None:
            return explicit
        if resource == GPU_RESOURCE:
            return self.gpu_capacity
        if resource.startswith(PROVIDER_RESOURCE_PREFIX):
            return self.provider_capacity
        return self.capacity

    def _resources_available_locked(self, resources: tuple[str, ...]) -> bool:
        return all(
            self._resource_running[resource] < self._resource_capacity(resource)
            for resource in resources
        )

    def _entry_is_eligible_locked(self, entry: _DispatchEntry) -> bool:
        return (
            entry.state == "queued"
            and len(self._running) < self.capacity
            and self._resources_available_locked(entry.resources)
        )

    def _oldest_eligible_locked(self, kind: RunKind) -> _DispatchEntry | None:
        return next(
            (
                entry
                for entry in self._queue
                if entry.kind == kind and self._entry_is_eligible_locked(entry)
            ),
            None,
        )

    def _next_eligible_locked(self) -> _DispatchEntry | None:
        interactive = self._oldest_eligible_locked("interactive")
        background = self._oldest_eligible_locked("background")
        chosen: _DispatchEntry | None
        if interactive is not None and background is not None:
            chosen = (
                background
                if self._interactive_streak >= self.interactive_weight
                else interactive
            )
        else:
            chosen = interactive or background
        if chosen is not None:
            self._queue.remove(chosen)
        return chosen

    def _grant_locked(self, entry: _DispatchEntry) -> None:
        entry.state = "running"
        entry.started_at = datetime.now(UTC).isoformat()
        self._running[entry.dispatch_id] = entry
        self._resource_running.update(entry.resources)
        if entry.kind == "interactive":
            self._interactive_streak = min(
                self.interactive_weight,
                self._interactive_streak + 1,
            )
        else:
            self._interactive_streak = 0
        self._started += 1
        if not entry.admission.done():
            entry.admission.set_result(None)

    def _promote_locked(self) -> None:
        while self._queue and len(self._running) < self.capacity:
            entry = self._next_eligible_locked()
            if entry is None:
                break
            self._grant_locked(entry)

    async def bind_task(
        self,
        entry: _DispatchEntry,
        task: asyncio.Task[Any],
    ) -> None:
        async with self._lock:
            current = self._entries.get(entry.dispatch_id)
            if current is entry:
                entry.task = task

    async def wait_for_submission_capacity(
        self,
        session_id: str,
        *,
        timeout: float = 1.0,
    ) -> bool:
        """Wait without polling until ownership/queue admission may be retried."""

        normalized_session = str(session_id).strip()

        def ready() -> bool:
            return (
                not self._accepting
                or (
                    normalized_session not in self._session_owners
                    and len(self._queue) < self.queue_capacity
                )
            )

        async with self._changed:
            if not ready():
                try:
                    await asyncio.wait_for(
                        self._changed.wait_for(ready),
                        timeout=max(0.01, float(timeout)),
                    )
                except TimeoutError:
                    return False
            return self._accepting

    async def cancel(self, entry: _DispatchEntry) -> bool:
        task: asyncio.Task[Any] | None = None
        async with self._lock:
            current = self._entries.get(entry.dispatch_id)
            if current is not entry or entry.state in {"completed", "cancelled"}:
                return False
            if entry.state == "queued":
                with suppress(ValueError):
                    self._queue.remove(entry)
                entry.state = "cancelled"
                self._entries.pop(entry.dispatch_id, None)
                if self._session_owners.get(entry.session_id) == entry.dispatch_id:
                    self._session_owners.pop(entry.session_id, None)
                self._cancelled += 1
                if not entry.admission.done():
                    entry.admission.set_exception(
                        RunDispatchCancelled("排队中的运行已取消")
                    )
                self._promote_locked()
                self._changed.notify_all()
                return True
            if entry.state == "running":
                entry.state = "cancelling"
                task = entry.task
            elif entry.state == "cancelling":
                task = entry.task
            else:
                return False
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        return True

    async def release(
        self,
        entry: _DispatchEntry,
        *,
        cancelled: bool = False,
    ) -> None:
        async with self._lock:
            current = self._entries.get(entry.dispatch_id)
            if current is not entry:
                return
            if entry.state == "queued":
                with suppress(ValueError):
                    self._queue.remove(entry)
                cancelled = True
                if not entry.admission.done():
                    entry.admission.set_exception(
                        RunDispatchCancelled("排队中的运行已释放")
                    )
            was_running = self._running.pop(entry.dispatch_id, None) is not None
            if was_running:
                for resource in entry.resources:
                    self._resource_running[resource] -= 1
                    if self._resource_running[resource] <= 0:
                        self._resource_running.pop(resource, None)
            self._entries.pop(entry.dispatch_id, None)
            if self._session_owners.get(entry.session_id) == entry.dispatch_id:
                self._session_owners.pop(entry.session_id, None)
            if cancelled or entry.state == "cancelling":
                entry.state = "cancelled"
                self._cancelled += 1
            else:
                entry.state = "completed"
                self._completed += 1
            self._promote_locked()
            self._changed.notify_all()

    def status_for(self, entry: _DispatchEntry) -> RunDispatchStatus:
        queue_position: int | None
        if entry.state == "queued":
            try:
                queue_position = tuple(self._queue).index(entry) + 1
            except ValueError:
                queue_position = None
        elif entry.state in {"running", "cancelling"}:
            queue_position = 0
        else:
            queue_position = None
        return RunDispatchStatus(
            dispatch_id=entry.dispatch_id,
            owner_id=entry.owner_id,
            session_id=entry.session_id,
            kind=entry.kind,
            state=entry.state,
            queue_position=queue_position,
            enqueued_at=entry.enqueued_at,
            started_at=entry.started_at,
            resources=entry.resources,
        )

    def snapshot(self) -> RunDispatcherSnapshot:
        running_kinds = Counter(entry.kind for entry in self._running.values())
        queued_kinds = Counter(entry.kind for entry in self._queue)
        resource_keys = {
            *self._resource_running,
            *(resource for entry in self._queue for resource in entry.resources),
        }
        resource_usage = {
            resource: {
                "capacity": self._resource_capacity(resource),
                "running": self._resource_running[resource],
                "queued": sum(
                    resource in entry.resources for entry in self._queue
                ),
            }
            for resource in sorted(resource_keys)
        }
        return RunDispatcherSnapshot(
            accepting=self._accepting,
            capacity=self.capacity,
            queue_capacity=self.queue_capacity,
            running=len(self._running),
            queued=len(self._queue),
            submitted=self._submitted,
            started=self._started,
            completed=self._completed,
            cancelled=self._cancelled,
            rejected=self._rejected,
            queue_high_water=self._queue_high_water,
            interactive_running=running_kinds["interactive"],
            background_running=running_kinds["background"],
            interactive_queued=queued_kinds["interactive"],
            background_queued=queued_kinds["background"],
            interactive_weight=self.interactive_weight,
            provider_capacity=self.provider_capacity,
            gpu_capacity=self.gpu_capacity,
            resources=resource_usage,
        )

    def liveness_status(self) -> dict[str, Any]:
        return self.snapshot().as_dict()

run_dispatcher = RunDispatcher()
