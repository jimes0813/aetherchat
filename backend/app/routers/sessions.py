from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..models.schemas import SessionDeleted, SessionSummary
from ..services.artifact_service import ArtifactPathError, ArtifactService, MergeProposalStore
from ..services.chat_database import chat_database
from ..services.git_workspace_service import GitWorkspaceError, git_workspace_service
from ..services.knowledge_service import KnowledgeError, knowledge_service
from ..services.tool_registry import tool_registry

router = APIRouter()
artifact_service = ArtifactService()
merge_store = MergeProposalStore()


class SessionCreate(BaseModel):
    id: UUID | None = None
    title: str = Field(default="新会话", min_length=1, max_length=200)
    mode: str = Field(default="project", pattern="^(casual|roundtable|project)$")


@router.get("/", response_model=list[SessionSummary])
async def list_sessions(limit: int = 100):
    return chat_database.list_sessions(limit)


@router.post("/", response_model=SessionSummary)
async def create_session(payload: SessionCreate):
    session_id = str(payload.id or uuid4())
    return chat_database.create_session(session_id, payload.title, payload.mode)


@router.get("/search")
async def search_messages(
    q: str = Query(min_length=1, max_length=300),
    limit: int = Query(default=50, ge=1, le=200),
):
    return chat_database.search_messages(q, limit)


@router.get("/{session_id}/messages")
async def list_messages(session_id: UUID, limit: int = 1000):
    return chat_database.list_messages(str(session_id), limit)


@router.get("/{session_id}/task-events")
async def list_task_events(session_id: UUID, limit: int = 300):
    return chat_database.list_task_events(str(session_id), limit)


@router.delete("/{session_id}", response_model=SessionDeleted)
async def delete_session(session_id: UUID):
    session = chat_database.get_session(str(session_id))
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if chat_database.session_has_active_runs(str(session_id)):
        raise HTTPException(
            status_code=409,
            detail="Session has an active Agent run; interrupt it before deleting",
        )
    cleanup: dict[str, object] = {}
    try:
        from .tasks import delete_session_tasks

        cleanup["git_worktrees"] = git_workspace_service.delete_session_worktrees(
            str(session_id)
        )
        cleanup["tasks"] = delete_session_tasks(str(session_id))
        cleanup["knowledge"] = knowledge_service.delete_session(str(session_id))
        cleanup["artifacts"] = artifact_service.delete_session(str(session_id))
        cleanup["merge_proposals"] = merge_store.delete_session(str(session_id))
        cleanup["tools"] = tool_registry.delete_session_data(str(session_id))
    except (ArtifactPathError, GitWorkspaceError, KnowledgeError, OSError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to safely remove session files: {exc}",
        ) from exc
    deleted = chat_database.delete_session(str(session_id))
    return {
        "deleted": deleted,
        "session_id": str(session_id),
        "cleanup": cleanup,
    }
