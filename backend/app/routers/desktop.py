import os
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from ..services.desktop_service import (
    DesktopCapabilityError,
    desktop_token_is_valid,
    document_service,
    mouse_service,
)

router = APIRouter()


def _require_token(token: str | None) -> None:
    if not desktop_token_is_valid(token):
        raise HTTPException(status_code=403, detail="Desktop capability token is missing or invalid")


class DocumentReadPayload(BaseModel):
    path: str = Field(min_length=1, max_length=32767)


class DocumentWritePayload(BaseModel):
    path: str = Field(min_length=1, max_length=32767)
    source_path: str | None = Field(default=None, max_length=32767)
    data: dict[str, Any]
    create_backup: bool = True


class MousePayload(BaseModel):
    action: Literal["get", "move", "click"]
    x: int | None = None
    y: int | None = None
    button: Literal["left", "right", "middle"] = "left"


@router.get("/status")
async def desktop_status(x_aether_desktop_token: str | None = Header(default=None)):
    _require_token(x_aether_desktop_token)
    windows_automation = os.name == "nt"
    return {
        "available": True,
        "file_read": True,
        "file_write": True,
        "document_formats": ["txt", "md", "json", "csv", "tsv", "docx", "xlsx", "pptx", "pdf"],
        "platform": "windows" if windows_automation else "linux",
        "mouse": windows_automation,
        "keyboard": windows_automation,
        "window_observation": windows_automation,
        "accessibility_tree": windows_automation,
        "model_computer_use": windows_automation,
        "native_consent": bool(os.environ.get("AETHERCHAT_COMPUTER_CONSENT_URL")),
        "screenshot": "electron",
    }


@router.post("/document/read")
async def read_document(payload: DocumentReadPayload, x_aether_desktop_token: str | None = Header(default=None)):
    _require_token(x_aether_desktop_token)
    try:
        return await document_service.read(payload.path)
    except (DesktopCapabilityError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/document/write")
async def write_document(payload: DocumentWritePayload, x_aether_desktop_token: str | None = Header(default=None)):
    _require_token(x_aether_desktop_token)
    try:
        return await document_service.write(
            payload.path,
            payload.data,
            source_path_value=payload.source_path,
            create_backup=payload.create_backup,
        )
    except (DesktopCapabilityError, OSError, ValueError, ImportError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/mouse")
async def control_mouse(payload: MousePayload, x_aether_desktop_token: str | None = Header(default=None)):
    _require_token(x_aether_desktop_token)
    try:
        return await mouse_service.execute(
            payload.action,
            x=payload.x,
            y=payload.y,
            button=payload.button,
        )
    except (DesktopCapabilityError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
