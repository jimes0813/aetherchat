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

class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    status: str | None = None
    claimed_by: str | None = None
    priority: int | None = Field(default=None, ge=0, le=3)


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
    return [
        Task(id=uuid4(), session_id=DEFAULT_SESSION_ID, title="补齐 WebSocket 流式消息", description="前后端 ack、delta、complete、cost_update 串联", status="in_progress", claimed_by="gpt", priority=2),
        Task(id=uuid4(), session_id=DEFAULT_SESSION_ID, title="Artifact Git MVP 验证", description="写入、读取、提交 hash 可追踪", status="review", claimed_by="deepseek", priority=1),
        Task(id=uuid4(), session_id=DEFAULT_SESSION_ID, title="Facilitator 循环检测提示词", description="从 docs 模板接入 orchestrator", status="backlog", priority=0),
    ]


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

@router.get("/")
async def list_tasks(session_id: UUID):
    return [
        task.model_dump(mode="json")
        for task in fake_tasks
        if task.session_id == session_id
    ]

@router.post("/{task_id}/claim")
async def claim_task(task_id: UUID, by: str = "model:claude"):
    async with with_lock(redis_client, f"claim:{task_id}", timeout=10):
        for index, task in enumerate(fake_tasks):
            if task.id == task_id:
                if task.status != "backlog":
                    raise HTTPException(status_code=400, detail="Task already claimed")
                updated = task.model_copy(update={"status": "claimed", "claimed_by": by or "demo-user"})
                fake_tasks[index] = updated
                _save_tasks()
                return updated.model_dump(mode="json")
    raise HTTPException(status_code=404, detail="Task not found")

@router.post("/")
async def create_task(payload: TaskCreate):
    task = Task(id=uuid4(), session_id=payload.session_id, title=payload.title, description=payload.description, status="backlog", priority=payload.priority)
    fake_tasks.append(task)
    _save_tasks()
    return task.model_dump(mode="json")

@router.patch("/{task_id}")
async def update_task(task_id: UUID, payload: TaskUpdate):
    allowed = {"backlog", "claimed", "in_progress", "review", "merged", "done"}
    for index, task in enumerate(fake_tasks):
        if task.id == task_id:
            data = payload.model_dump(exclude_none=True)
            if "status" in data and data["status"] not in allowed:
                raise HTTPException(status_code=400, detail="Invalid status")
            updated = task.model_copy(update=data)
            fake_tasks[index] = updated
            _save_tasks()
            return updated.model_dump(mode="json")
    raise HTTPException(status_code=404, detail="Task not found")
