from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from ..services.artifact_service import (
    ArtifactPathError,
    ArtifactService,
    MergeProposalStore,
)

router = APIRouter()
artifact_svc = ArtifactService()
merge_store = MergeProposalStore()


class ArtifactWrite(BaseModel):
    session_id: str
    path: str
    content: str
    commit_message: str = "Agent update"
    author: str = "user"
    source_model: str | None = None
    status: str = "ready"
    parent_version: int | None = None


class ArtifactRestore(BaseModel):
    session_id: str
    version: int
    author: str = "user"


class MergeCreate(BaseModel):
    session_id: str
    artifact_path: str
    proposer: str = "facilitator"
    rationale: str
    from_hash: str | None = None
    to_hash: str | None = None
    merge_rule: str = "two_votes"


class VotePayload(BaseModel):
    voter: str
    approve: bool
    role: str | None = None
    manual_confirmation: bool = False
    session_id: str | None = None


def _bad_path(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.post("/write")
async def write_artifact(payload: ArtifactWrite):
    try:
        return await artifact_svc.write(
            payload.session_id,
            payload.path,
            payload.content,
            payload.commit_message,
            author=payload.author,
            source_model=payload.source_model,
            status=payload.status,
            parent_version=payload.parent_version,
        )
    except ArtifactPathError as exc:
        raise _bad_path(exc) from exc


@router.get("/")
async def list_artifacts(session_id: str):
    try:
        return await artifact_svc.list_files(session_id)
    except ArtifactPathError as exc:
        raise _bad_path(exc) from exc


@router.get("/merge/proposals")
async def list_merge_proposals(session_id: str):
    try:
        artifact_svc.normalize_session_id(session_id)
    except ArtifactPathError as exc:
        raise _bad_path(exc) from exc
    return merge_store.list(session_id)


@router.post("/merge/proposals")
async def create_merge_proposal(payload: MergeCreate):
    try:
        artifact_svc.normalize_session_id(payload.session_id)
        artifact_path = artifact_svc.normalize_path(payload.artifact_path)
        return merge_store.create(
            session_id=payload.session_id,
            artifact_path=artifact_path,
            proposer=payload.proposer,
            rationale=payload.rationale,
            from_hash=payload.from_hash,
            to_hash=payload.to_hash,
            merge_rule=payload.merge_rule,
        )
    except (ArtifactPathError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/merge/proposals/{proposal_id}/vote")
async def vote_merge_proposal(proposal_id: str, payload: VotePayload):
    current = merge_store.get(proposal_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Merge proposal not found")
    if payload.session_id is not None and str(current.get("session_id")) != payload.session_id:
        # Do not reveal a proposal from another session when a session boundary
        # is supplied by the caller.
        raise HTTPException(status_code=404, detail="Merge proposal not found")

    proposal, accepted = merge_store.vote(
        proposal_id,
        voter=payload.voter,
        approve=payload.approve,
        role=payload.role,
        manual_confirmation=payload.manual_confirmation,
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Merge proposal not found")
    proposal["vote_accepted"] = accepted
    return proposal


@router.get("/{path:path}/history")
async def artifact_history(session_id: str, path: str):
    try:
        return await artifact_svc.history(session_id, path)
    except ArtifactPathError as exc:
        raise _bad_path(exc) from exc


@router.get("/{path:path}/preview")
async def preview_artifact(session_id: str, path: str):
    try:
        return await artifact_svc.preview(session_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    except (ArtifactPathError, ValueError) as exc:
        raise _bad_path(exc) from exc


@router.get("/{path:path}/native-location")
async def native_artifact_location(session_id: str, path: str):
    try:
        return await artifact_svc.native_location(session_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    except ArtifactPathError as exc:
        raise _bad_path(exc) from exc


@router.get("/{path:path}/diff")
async def diff_artifact(
    session_id: str,
    path: str,
    from_version: int = Query(ge=1),
    to_version: int | None = Query(default=None, ge=1),
):
    try:
        return await artifact_svc.diff(session_id, path, from_version, to_version)
    except ArtifactPathError as exc:
        raise _bad_path(exc) from exc


@router.post("/{path:path}/restore")
async def restore_artifact(path: str, payload: ArtifactRestore):
    try:
        return await artifact_svc.restore(payload.session_id, path, payload.version, payload.author)
    except ArtifactPathError as exc:
        raise _bad_path(exc) from exc


@router.get("/{path:path}/export")
async def export_artifact(session_id: str, path: str, format: str = Query(pattern=r"^[A-Za-z0-9]+$")):
    try:
        data, filename, media_type = await artifact_svc.export(session_id, path, format)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    except (ArtifactPathError, ValueError, json.JSONDecodeError) as exc:
        raise _bad_path(exc) from exc
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.get("/{path:path}")
async def read_artifact(session_id: str, path: str):
    try:
        normalized = artifact_svc.normalize_path(path)
        content = await artifact_svc.read(session_id, normalized)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    except ArtifactPathError as exc:
        raise _bad_path(exc) from exc
    return {"path": normalized, "content": content}
