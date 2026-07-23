from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services.approval_service import approval_service

router = APIRouter()


class ApprovalDecisionPayload(BaseModel):
    session_id: UUID
    decision: Literal["allow_once", "allow_session", "deny"]


@router.post("/{approval_id}/decision")
async def decide_approval(approval_id: UUID, payload: ApprovalDecisionPayload):
    try:
        return approval_service.decide(str(approval_id), str(payload.session_id), payload.decision)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
