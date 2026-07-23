from uuid import UUID, uuid4

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services.chat_database import chat_database

router = APIRouter()


class SessionCreate(BaseModel):
    id: UUID | None = None
    title: str = Field(default="新会话", min_length=1, max_length=200)
    mode: str = Field(default="project", pattern="^(casual|roundtable|project)$")


@router.get("/")
async def list_sessions(limit: int = 100):
    return chat_database.list_sessions(limit)


@router.post("/")
async def create_session(payload: SessionCreate):
    session_id = str(payload.id or uuid4())
    return chat_database.create_session(session_id, payload.title, payload.mode)


@router.get("/{session_id}/messages")
async def list_messages(session_id: UUID, limit: int = 1000):
    return chat_database.list_messages(str(session_id), limit)


@router.get("/{session_id}/task-events")
async def list_task_events(session_id: UUID, limit: int = 300):
    return chat_database.list_task_events(str(session_id), limit)
