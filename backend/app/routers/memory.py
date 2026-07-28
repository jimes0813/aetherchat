from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services.memory_service import memory_service

router = APIRouter()


class MemoryPatch(BaseModel):
    project_note: str | None = Field(default=None, max_length=4000)
    decisions: list[str] | None = None
    preferences: list[str] | None = None
    open_items: list[str] | None = None
    entities: list[dict[str, Any]] | None = None


@router.get("/{session_id}")
async def get_memory(session_id: UUID):
    return memory_service.database.get_memory(str(session_id))


@router.patch("/{session_id}")
async def update_memory(session_id: UUID, payload: MemoryPatch):
    try:
        return memory_service.update(str(session_id), payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
