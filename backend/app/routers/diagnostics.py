from __future__ import annotations

from fastapi import APIRouter, Response

from ..services.automation_service import automation_service
from ..services.diagnostic_service import diagnostic_service
from ..services.run_dispatcher import run_dispatcher

router = APIRouter()


def _runtime() -> dict[str, dict]:
    return {
        "automation": automation_service.liveness_status(),
        "run_dispatcher": run_dispatcher.liveness_status(),
    }


@router.get("/snapshot")
async def diagnostic_snapshot():
    runtime = _runtime()
    return diagnostic_service.snapshot(**runtime)


@router.post("/bundle")
async def diagnostic_bundle():
    runtime = _runtime()
    filename = diagnostic_service.bundle_filename()
    return Response(
        content=diagnostic_service.bundle(**runtime),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
