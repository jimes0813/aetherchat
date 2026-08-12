"""HTTP API for Skills / Rules instruction layers."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services.instruction_dry_run import catalog_permission_matrix, dry_run_instructions
from ..services.instruction_workflow_service import (
    WorkflowCreate,
    WorkflowUpdate,
    instruction_workflow_service,
)
from ..services.skills_rules_service import (
    PublishRequest,
    RollbackRequest,
    RuleCreate,
    RuleUpdate,
    SkillCreate,
    SkillUpdate,
    skills_rules_service,
)

router = APIRouter()
webhook_router = APIRouter()


class ResolveRequest(BaseModel):
    user_message: str = ""
    session_id: str | None = None
    workspace_roots: list[str] = Field(default_factory=list)


class ImportAgentsRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


@router.get("/rules")
async def list_rules(session_id: str | None = None):
    return skills_rules_service.list_rules(session_id=session_id)


@router.post("/rules", status_code=201)
async def create_rule(payload: RuleCreate):
    return skills_rules_service.create_rule(payload)


@router.patch("/rules/{rule_id}")
async def update_rule(rule_id: str, payload: RuleUpdate):
    item = skills_rules_service.update_rule(rule_id, payload)
    if not item:
        raise HTTPException(status_code=404, detail="Rule not found")
    return item


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str):
    if not skills_rules_service.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"deleted": True, "id": rule_id}


@router.get("/rules/{rule_id}/versions")
async def list_rule_versions(rule_id: str):
    rows = skills_rules_service.list_rule_versions(rule_id)
    if rows is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rows


@router.post("/rules/{rule_id}/publish")
async def publish_rule(rule_id: str, payload: PublishRequest):
    try:
        return skills_rules_service.publish_rule(rule_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Rule not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rules/{rule_id}/rollback")
async def rollback_rule(rule_id: str, payload: RollbackRequest):
    try:
        return skills_rules_service.rollback_rule(rule_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Rule not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/skills")
async def list_skills():
    return skills_rules_service.list_skills()


@router.post("/skills", status_code=201)
async def create_skill(payload: SkillCreate):
    try:
        return skills_rules_service.create_skill(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/skills/{skill_id}")
async def update_skill(skill_id: str, payload: SkillUpdate):
    try:
        item = skills_rules_service.update_skill(skill_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="Skill not found")
    return item


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str):
    if not skills_rules_service.delete_skill(skill_id):
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"deleted": True, "id": skill_id}


@router.get("/skills/{skill_id}/versions")
async def list_skill_versions(skill_id: str):
    rows = skills_rules_service.list_skill_versions(skill_id)
    if rows is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return rows


@router.post("/skills/{skill_id}/publish")
async def publish_skill(skill_id: str, payload: PublishRequest):
    try:
        return skills_rules_service.publish_skill(skill_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Skill not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/skills/{skill_id}/rollback")
async def rollback_skill(skill_id: str, payload: RollbackRequest):
    try:
        return skills_rules_service.rollback_skill(skill_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Skill not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/resolve")
async def resolve_instructions(payload: ResolveRequest):
    if payload.session_id and not payload.workspace_roots:
        return skills_rules_service.resolve_for_session(
            payload.session_id,
            user_message=payload.user_message,
        )
    return skills_rules_service.resolve(
        user_message=payload.user_message,
        session_id=payload.session_id,
        workspace_roots=payload.workspace_roots,
    )


@router.post("/dry-run")
async def dry_run(payload: ResolveRequest):
    """Resolve layers and list implied permissions without executing tools."""
    return dry_run_instructions(
        user_message=payload.user_message,
        session_id=payload.session_id,
        workspace_roots=payload.workspace_roots,
    )


@router.get("/permissions/catalog")
async def permissions_catalog():
    """Static risk matrix for known tools (for UI checklist)."""
    return catalog_permission_matrix()


@router.post("/import-agents-md", status_code=201)
async def import_agents_md(payload: ImportAgentsRequest):
    try:
        return skills_rules_service.import_agents_md_as_rule(payload.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class WorkflowFireRequest(BaseModel):
    message: str | None = Field(default=None, max_length=8_000)


class WebhookFireBody(BaseModel):
    message: str | None = Field(default=None, max_length=8_000)


@router.get("/workflows")
async def list_workflows(session_id: str | None = None):
    rows = instruction_workflow_service.list_workflows(session_id=session_id)
    return [instruction_workflow_service.public_view(item) for item in rows]


@router.post("/workflows", status_code=201)
async def create_workflow(payload: WorkflowCreate):
    try:
        item = instruction_workflow_service.create(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Return token once for webhook workflows
    return instruction_workflow_service.public_view(item, include_token=True)


@router.patch("/workflows/{workflow_id}")
async def update_workflow(workflow_id: str, payload: WorkflowUpdate):
    try:
        item = instruction_workflow_service.update(workflow_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return instruction_workflow_service.public_view(item)


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str):
    if not instruction_workflow_service.delete(workflow_id):
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"deleted": True, "id": workflow_id}


@router.post("/workflows/{workflow_id}/run")
async def run_workflow(workflow_id: str, payload: WorkflowFireRequest | None = None):
    item = instruction_workflow_service.get(workflow_id)
    if not item:
        raise HTTPException(status_code=404, detail="Workflow not found")
    try:
        return instruction_workflow_service.fire(
            item,
            message=(payload.message if payload else None),
            trigger="manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@webhook_router.post("/workflows/hooks/{token}")
async def fire_workflow_webhook(token: str, payload: WebhookFireBody | None = None):
    """Unauthenticated webhook entry; authorized solely by workflow token."""
    item = instruction_workflow_service.find_by_webhook_token(token)
    if not item:
        raise HTTPException(status_code=404, detail="Unknown webhook token")
    if not item.get("enabled", True):
        raise HTTPException(status_code=409, detail="Workflow disabled")
    try:
        return instruction_workflow_service.fire(
            item,
            message=(payload.message if payload else None),
            trigger="webhook",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
