"""Phase 3 / WP-8 platform governance API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..services.platform_governance import (
    AuthorizeRequest,
    PolicyPatch,
    PrincipalCreate,
    platform_governance,
)

router = APIRouter()


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=64)


@router.get("/status")
async def platform_status():
    return platform_governance.status()


@router.get("/tenants")
async def list_tenants():
    return platform_governance.list_tenants()


@router.post("/tenants", status_code=201)
async def create_tenant(payload: TenantCreate):
    try:
        return platform_governance.create_tenant(payload.name, slug=payload.slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tenants/{tenant_id}")
async def get_tenant(tenant_id: str):
    item = platform_governance.get_tenant(tenant_id)
    if not item:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return item


@router.patch("/tenants/{tenant_id}/policy")
async def patch_tenant_policy(tenant_id: str, payload: PolicyPatch):
    try:
        return platform_governance.patch_policy(tenant_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Tenant not found") from exc


@router.get("/principals")
async def list_principals(tenant_id: str | None = None):
    return platform_governance.list_principals(tenant_id=tenant_id)


@router.post("/principals", status_code=201)
async def create_principal(payload: PrincipalCreate):
    try:
        return platform_governance.create_principal(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/roles")
async def list_roles():
    return platform_governance.list_roles()


@router.post("/authorize")
async def authorize(payload: AuthorizeRequest):
    return platform_governance.authorize(payload)


@router.get("/audit")
async def list_audit(
    limit: int = Query(default=50, ge=1, le=500),
    tenant_id: str | None = None,
):
    return platform_governance.list_audit(limit=limit, tenant_id=tenant_id)


@router.get("/audit/export")
async def export_audit(
    limit: int = Query(default=500, ge=1, le=5000),
    tenant_id: str | None = None,
    format: str = Query(default="json", pattern="^(json|jsonl)$"),
):
    """Export recent audit events as JSON object or JSONL body."""
    return platform_governance.export_audit(
        limit=limit,
        tenant_id=tenant_id,
        format=format,  # type: ignore[arg-type]
    )
