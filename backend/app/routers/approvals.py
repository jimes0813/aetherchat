from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..services.approval_service import approval_service

router = APIRouter()


class ApprovalDecisionPayload(BaseModel):
    session_id: UUID
    decision: Literal["allow_once", "allow_session", "deny"]
    feedback: str = Field(default="", max_length=2000)


@router.get("/restart-cancellations")
async def list_restart_cancellations(limit: int = Query(default=50, ge=1, le=500)):
    """Pending approvals cancelled because the backend restarted (需重新授权)."""
    items = approval_service.list_restart_cancellations(limit=limit)
    return {
        "items": items,
        "count": len(items),
        "message": "需重新授权" if items else "",
        "reason": "backend_restart",
    }


@router.post("/{approval_id}/decision")
async def decide_approval(approval_id: UUID, payload: ApprovalDecisionPayload):
    try:
        return approval_service.decide(
            str(approval_id),
            str(payload.session_id),
            payload.decision,
            payload.feedback,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
