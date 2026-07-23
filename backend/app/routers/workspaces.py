import asyncio
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from ..services.desktop_service import desktop_token_is_valid
from ..services.git_workspace_service import (
    GitWorkspaceError,
    git_workspace_service,
)

router = APIRouter()


def _require_desktop_token(token: str | None) -> None:
    if not desktop_token_is_valid(token):
        raise HTTPException(
            status_code=403,
            detail="Desktop capability token is missing or invalid",
        )


class RepositoryRegistration(BaseModel):
    session_id: UUID
    path: str = Field(min_length=1, max_length=32767)


class WorkspaceDecision(BaseModel):
    session_id: UUID
    decision: Literal["apply", "reject"]


@router.get("/repositories")
async def list_repositories(session_id: UUID):
    return git_workspace_service.list_repositories(str(session_id))


@router.post("/repositories/register", status_code=201)
async def register_repository(
    payload: RepositoryRegistration,
    x_aether_desktop_token: str | None = Header(default=None),
):
    _require_desktop_token(x_aether_desktop_token)
    try:
        return await asyncio.to_thread(
            git_workspace_service.register_repository,
            session_id=str(payload.session_id),
            selected_path=payload.path,
        )
    except (GitWorkspaceError, OSError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
async def list_run_workspaces(run_id: UUID, session_id: UUID):
    try:
        return git_workspace_service.list_run_workspaces(
            run_id=str(run_id),
            session_id=str(session_id),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


@router.post("/candidates/{workspace_id}/decision")
async def decide_candidate(
    workspace_id: UUID,
    payload: WorkspaceDecision,
    x_aether_desktop_token: str | None = Header(default=None),
):
    _require_desktop_token(x_aether_desktop_token)
    try:
        if payload.decision == "apply":
            return await asyncio.to_thread(
                git_workspace_service.apply_workspace,
                workspace_id=str(workspace_id),
                session_id=str(payload.session_id),
            )
        return await asyncio.to_thread(
            git_workspace_service.reject_workspace,
            workspace_id=str(workspace_id),
            session_id=str(payload.session_id),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except (GitWorkspaceError, OSError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
