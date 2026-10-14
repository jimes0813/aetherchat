import os
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from ..services.computer_use_policy import computer_use_policy
from ..services.computer_use_service import ComputerUseError, computer_use_service
from ..services.desktop_service import (
    DesktopCapabilityError,
    desktop_token_is_valid,
    document_service,
    mouse_service,
)

router = APIRouter()



def _require_computer_use_enabled() -> None:
    if not computer_use_policy.is_enabled():
        raise HTTPException(
            status_code=403,
            detail="Computer Use 未启用：请先在设置中开启，或确认未设置 AETHERCHAT_DISABLE_COMPUTER_USE=1",
        )

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


class ComputerActionPayload(BaseModel):
    action: Literal[
        "screenshot",
        "mouse_move",
        "left_click",
        "right_click",
        "middle_click",
        "double_click",
        "triple_click",
        "left_click_drag",
        "left_mouse_down",
        "left_mouse_up",
        "scroll",
        "type",
        "key",
        "hold_key",
        "wait",
        "zoom",
        "cursor_position",
    ]
    coordinate: list[int] | None = None
    start_coordinate: list[int] | None = None
    region: list[int] | None = None
    text: str | None = Field(default=None, max_length=20_000)
    duration: float | None = Field(default=None, ge=0, le=30)
    scroll_direction: Literal["up", "down", "left", "right"] | None = None
    scroll_amount: int | None = Field(default=None, ge=0, le=5_000)
    delta_x: int | None = Field(default=None, ge=-5_000, le=5_000)
    delta_y: int | None = Field(default=None, ge=-5_000, le=5_000)
    modifiers: list[str] = Field(default_factory=list, max_length=4)


@router.get("/status")
async def desktop_status(x_aether_desktop_token: str | None = Header(default=None)):
    _require_token(x_aether_desktop_token)
    computer_automation = mouse_service.available()
    cu_policy = computer_use_policy.status()
    cu_enabled = bool(cu_policy.get("enabled"))
    return {
        "available": True,
        "file_read": True,
        "file_write": True,
        "document_formats": ["txt", "md", "json", "csv", "tsv", "docx", "xlsx", "pptx", "pdf"],
        "platform": "windows" if os.name == "nt" else "linux",
        "session_type": "win32" if os.name == "nt" else os.environ.get("XDG_SESSION_TYPE", "unknown"),
        "mouse": computer_automation and cu_enabled,
        "keyboard": computer_automation and cu_enabled,
        "window_observation": computer_automation and cu_enabled,
        "accessibility_tree": os.name == "nt" and cu_enabled,
        "model_computer_use": computer_automation and cu_enabled,
        "computer_use_enabled": cu_enabled,
        "computer_use_policy": cu_policy,
        "computer_protocol": "computer_20251124",
        "computer_actions": computer_use_service.supported_actions(),
        "enable_zoom": True,
        "post_action_verification": True,
        "interruptible": True,
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
    _require_computer_use_enabled()
    try:
        return await mouse_service.execute(
            payload.action,
            x=payload.x,
            y=payload.y,
            button=payload.button,
        )
    except (DesktopCapabilityError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/computer/action")
async def run_computer_action(
    payload: ComputerActionPayload,
    x_aether_desktop_token: str | None = Header(default=None),
):
    _require_token(x_aether_desktop_token)
    _require_computer_use_enabled()
    try:
        return await computer_use_service.execute_action(
            payload.action,
            payload.model_dump(exclude={"action"}, exclude_none=True),
        )
    except (ComputerUseError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/computer/cancel")
async def cancel_computer_action(x_aether_desktop_token: str | None = Header(default=None)):
    _require_token(x_aether_desktop_token)
    from ..services.approval_service import approval_service

    revoked = approval_service.clear_risks({"computer_observation", "computer_control"})
    return {
        **(await computer_use_service.cancel_active_action()),
        "session_grants_revoked": revoked,
    }


@router.get("/computer/history")
async def computer_action_history(
    after: int = 0,
    x_aether_desktop_token: str | None = Header(default=None),
):
    _require_token(x_aether_desktop_token)
    return computer_use_service.action_history(after)


class ComputerUsePolicyPayload(BaseModel):
    enabled: bool


@router.get("/computer-use/policy")
async def get_computer_use_policy(x_aether_desktop_token: str | None = Header(default=None)):
    _require_token(x_aether_desktop_token)
    return computer_use_policy.status()


@router.put("/computer-use/policy")
async def put_computer_use_policy(
    payload: ComputerUsePolicyPayload,
    x_aether_desktop_token: str | None = Header(default=None),
):
    _require_token(x_aether_desktop_token)
    try:
        return computer_use_policy.set_enabled(payload.enabled)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
