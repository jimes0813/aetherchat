# backend/app/routers/tasks.py
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import redis.asyncio as redis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..models.schemas import Task
from ..services.chat_database import chat_database
from ..services.redis_lock import with_lock

redis_client: redis.Redis | None = None

router = APIRouter()

class TaskCreate(BaseModel):
    session_id: UUID
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    priority: int = Field(default=0, ge=0, le=3)
    depends_on: list[UUID] = Field(default_factory=list, max_length=50)

class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    status: str | None = None
    claimed_by: str | None = None
    priority: int | None = Field(default=None, ge=0, le=3)
    depends_on: list[UUID] | None = Field(default=None, max_length=50)


DEFAULT_SESSION_ID = UUID("11111111-1111-4111-8111-111111111111")


def _storage_path() -> Path:
    root = Path(
        os.environ.get(
            "AETHERCHAT_DATA_DIR",
            Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AetherChat",
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    return root / "tasks.json"


TASKS_PATH = _storage_path()


def _seed_tasks() -> list[Task]:
    """A new workspace is intentionally empty; examples require explicit opt-in."""
    return []


def _write_tasks(tasks: list[Task], path: Path | None = None):
    target = path or TASKS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps([task.model_dump(mode="json") for task in tasks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)


def _load_tasks(path: Path | None = None) -> list[Task]:
    target = path or TASKS_PATH
    database_rows = chat_database.load_all_tasks()
    if database_rows and path is None:
        return [Task.model_validate(row) for row in database_rows]
    if not target.exists():
        tasks = _seed_tasks()
        _write_tasks(tasks, target)
        chat_database.sync_tasks(tasks)
        return tasks
    try:
        rows = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("tasks.json must contain a list")
        tasks = [Task.model_validate(row) for row in rows]
        chat_database.sync_tasks(tasks)
        return tasks
    except Exception:
        tasks = _seed_tasks()
        _write_tasks(tasks, target)
        chat_database.sync_tasks(tasks)
        return tasks


fake_tasks: list[Task] = _load_tasks()


def _save_tasks():
    _write_tasks(fake_tasks)
    chat_database.sync_tasks(fake_tasks)


async def _save_tasks_async():
    _write_tasks(fake_tasks)
    await chat_database.run_in_thread(chat_database.sync_tasks, fake_tasks)


def delete_session_tasks(session_id: str) -> int:
    retained = [
        task for task in fake_tasks
        if str(task.session_id) != str(session_id)
    ]
    removed = len(fake_tasks) - len(retained)
    if removed:
        fake_tasks[:] = retained
        _save_tasks()
    return removed


COMPLETED_TASK_STATUSES = {"merged", "done"}


def _find_task(task_id: UUID, session_id: str | UUID | None = None) -> tuple[int, Task] | None:
    for index, task in enumerate(fake_tasks):
        if task.id == task_id and (session_id is None or str(task.session_id) == str(session_id)):
            return index, task
    return None


def _blocked_by(task: Task) -> list[UUID]:
    statuses = {
        candidate.id: candidate.status
        for candidate in fake_tasks
        if candidate.session_id == task.session_id
    }
    return [
        dependency
        for dependency in task.depends_on
        if statuses.get(dependency) not in COMPLETED_TASK_STATUSES
    ]


def _public_task(task: Task) -> dict:
    return {
        **task.model_dump(mode="json"),
        "blocked_by": [str(value) for value in _blocked_by(task)],
        "ready": not _blocked_by(task),
    }


def _validate_dependencies(
    *,
    session_id: UUID,
    task_id: UUID,
    dependencies: list[UUID],
) -> list[UUID]:
    unique = list(dict.fromkeys(dependencies))
    if task_id in unique:
        raise ValueError("任务不能依赖自身")
    session_tasks = {
        task.id: task
        for task in fake_tasks
        if task.session_id == session_id
    }
    missing = [value for value in unique if value not in session_tasks]
    if missing:
        raise ValueError(f"依赖任务不存在或不属于当前会话：{', '.join(map(str, missing))}")

    graph = {
        candidate.id: list(candidate.depends_on)
        for candidate in session_tasks.values()
    }
    graph[task_id] = unique

    def reaches(start: UUID, target: UUID, seen: set[UUID]) -> bool:
        if start == target:
            return True
        if start in seen:
            return False
        seen.add(start)
        return any(reaches(child, target, seen) for child in graph.get(start, []))

    if any(reaches(dependency, task_id, set()) for dependency in unique):
        raise ValueError("任务依赖会形成循环")
    return unique


def begin_task_tool(task_id: str, session_id: str, caller: str) -> dict:
    try:
        parsed = UUID(str(task_id))
    except ValueError as exc:
        raise ValueError("task_id 无效") from exc
    match = _find_task(parsed, session_id)
    if match is None:
        raise KeyError("关联任务不存在")
    index, task = match
    blockers = _blocked_by(task)
    if blockers:
        raise ValueError(f"任务仍被依赖阻塞：{', '.join(map(str, blockers))}")
    if task.status in COMPLETED_TASK_STATUSES:
        raise ValueError("关联任务已经完成")
    updated = task.model_copy(update={
        "status": "in_progress",
        "claimed_by": task.claimed_by or caller,
    })
    fake_tasks[index] = updated
    _save_tasks()
    return _public_task(updated)


def finish_task_tool(task_id: str, session_id: str, *, success: bool) -> dict | None:
    try:
        parsed = UUID(str(task_id))
    except ValueError:
        return None
    match = _find_task(parsed, session_id)
    if match is None:
        return None
    index, task = match
    if success and task.status not in COMPLETED_TASK_STATUSES:
        task = task.model_copy(update={"status": "review"})
        fake_tasks[index] = task
        _save_tasks()
    return _public_task(task)

@router.get("/")
async def list_tasks(session_id: UUID):
    return [
        _public_task(task)
        for task in fake_tasks
        if task.session_id == session_id
    ]


@router.delete("/examples")
async def delete_example_tasks(session_id: UUID):
    retained = [
        task for task in fake_tasks
        if task.session_id != session_id or not task.title.startswith("[示例]")
    ]
    removed = len(fake_tasks) - len(retained)
    if removed:
        fake_tasks[:] = retained
        await _save_tasks_async()
    return {"removed": removed}

@router.post("/{task_id}/claim")
async def claim_task(task_id: UUID, by: str = "model:claude"):
    async with with_lock(redis_client, f"claim:{task_id}", timeout=10):
        for index, task in enumerate(fake_tasks):
            if task.id == task_id:
                if task.status != "backlog":
                    raise HTTPException(status_code=400, detail="Task already claimed")
                blockers = _blocked_by(task)
                if blockers:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Task is blocked by: {', '.join(map(str, blockers))}",
                    )
                updated = task.model_copy(update={"status": "claimed", "claimed_by": by or "demo-user"})
                fake_tasks[index] = updated
                await _save_tasks_async()
                return _public_task(updated)
    raise HTTPException(status_code=404, detail="Task not found")

@router.post("/")
async def create_task(payload: TaskCreate):
    task_id = uuid4()
    try:
        dependencies = _validate_dependencies(
            session_id=payload.session_id,
            task_id=task_id,
            dependencies=payload.depends_on,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    task = Task(
        id=task_id,
        session_id=payload.session_id,
        title=payload.title,
        description=payload.description,
        status="backlog",
        priority=payload.priority,
        depends_on=dependencies,
    )
    fake_tasks.append(task)
    await _save_tasks_async()
    return _public_task(task)

@router.patch("/{task_id}")
async def update_task(task_id: UUID, payload: TaskUpdate):
    allowed = {"backlog", "claimed", "in_progress", "review", "merged", "done"}
    for index, task in enumerate(fake_tasks):
        if task.id == task_id:
            data = payload.model_dump(exclude_none=True)
            if "status" in data and data["status"] not in allowed:
                raise HTTPException(status_code=400, detail="Invalid status")
            if "depends_on" in data:
                try:
                    data["depends_on"] = _validate_dependencies(
                        session_id=task.session_id,
                        task_id=task.id,
                        dependencies=data["depends_on"],
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
            candidate = task.model_copy(update=data)
            if candidate.status not in {"backlog"} and _blocked_by(candidate):
                raise HTTPException(status_code=409, detail="Task dependencies are not complete")
            updated = candidate
            fake_tasks[index] = updated
            await _save_tasks_async()
            return _public_task(updated)
    raise HTTPException(status_code=404, detail="Task not found")
