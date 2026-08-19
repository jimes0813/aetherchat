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
    project_id: str | None = Field(default=None, max_length=120)


class SessionProjectPatch(BaseModel):
    project_id: str | None = Field(default=None, max_length=120)


def _default_governance_project_id() -> str | None:
    try:
        from ..services.platform_governance import platform_governance

        return platform_governance.state.get("default_project_id") or "default"
    except Exception:
        return "default"


def _validate_project_id(project_id: str | None) -> str | None:
    if project_id is None:
        return None
    clean = str(project_id).strip()
    if not clean:
        return None
    from ..services.platform_governance import platform_governance

    if not platform_governance.get_project(clean):
        raise HTTPException(status_code=400, detail=f"治理项目不存在：{clean}")
    return clean


@router.get("/", response_model=list[SessionSummary])
async def list_sessions(limit: int = 100):
    return chat_database.list_sessions(limit)


@router.post("/", response_model=SessionSummary)
async def create_session(payload: SessionCreate):
    session_id = str(payload.id or uuid4())
    project_id = _validate_project_id(payload.project_id)
    if project_id is None:
        project_id = _default_governance_project_id()
    return chat_database.create_session(
        session_id,
        payload.title,
        payload.mode,
        project_id=project_id,
    )


@router.patch("/{session_id}/project", response_model=SessionSummary)
async def patch_session_project(session_id: UUID, payload: SessionProjectPatch):
    project_id = _validate_project_id(payload.project_id)
    updated = chat_database.set_session_project(str(session_id), project_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return updated


@router.get("/search")
async def search_messages(
    q: str = Query(min_length=1, max_length=300),
    limit: int = Query(default=50, ge=1, le=200),
):
    return chat_database.search_messages(q, limit)


@router.get("/{session_id}/messages")
async def list_messages(session_id: UUID, limit: int = 100):
    return await chat_database.run_in_thread(
        chat_database.list_messages,
        str(session_id),
        limit,
    )


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
