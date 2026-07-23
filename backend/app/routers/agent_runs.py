from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from ..services.agent_run_service import agent_run_service
from ..services.git_workspace_service import git_workspace_service

router = APIRouter()


@router.get("")
async def list_runs(session_id: UUID, limit: int = Query(default=100, ge=1, le=500)):
    runs = agent_run_service.database.list_agent_runs(str(session_id), limit)
    for run in runs:
        candidates = [
            git_workspace_service.public_workspace(item)
            for item in agent_run_service.database.list_agent_workspaces(
                run["id"]
            )
        ]
        run["workspace_candidates"] = candidates
        run["workspace_candidate_count"] = len(candidates)
    return runs


@router.get("/{run_id}")
async def get_run(run_id: UUID, session_id: UUID):
    run = agent_run_service.database.get_agent_run(str(run_id))
    if run is None or run["session_id"] != str(session_id):
        raise HTTPException(status_code=404, detail="运行记录不存在")
    candidates = [
        git_workspace_service.public_workspace(item)
        for item in agent_run_service.database.list_agent_workspaces(
            run["id"]
        )
    ]
    run["workspace_candidates"] = candidates
    run["workspace_candidate_count"] = len(candidates)
    return run
