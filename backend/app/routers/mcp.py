from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..services.mcp_registry import MCPConnectionCreate, MCPConnectionPatch, mcp_registry

router = APIRouter()
callback_router = APIRouter()


class OAuthStart(BaseModel):
    callback_url: str


@router.get("")
async def list_connections():
    return mcp_registry.list()


@router.post("", status_code=201)
async def create_connection(payload: MCPConnectionCreate):
    try:
        return await mcp_registry.create(payload)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{connection_id}")
async def update_connection(connection_id: str, payload: MCPConnectionPatch):
    try:
        updated = mcp_registry.update(connection_id, payload)
    except (ValueError, PermissionError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="MCP 连接器不存在")
    if payload.enabled:
        try:
            updated = await mcp_registry.refresh(connection_id)
        except Exception:
            updated = mcp_registry.get(connection_id) or updated
    return updated


@router.post("/{connection_id}/refresh")
async def refresh_connection(connection_id: str):
    try:
        return await mcp_registry.refresh(connection_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="MCP 连接器不存在") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MCP 连接失败：{exc}") from exc


@router.delete("/{connection_id}")
async def delete_connection(connection_id: str):
    removed = mcp_registry.delete(connection_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="MCP 连接器不存在")
    return {"deleted": True, "connection": removed}


@router.post("/{connection_id}/oauth/start")
async def start_oauth(connection_id: str, payload: OAuthStart):
    try:
        return await mcp_registry.start_oauth(connection_id, payload.callback_url)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="MCP 连接器不存在") from exc
    except (ValueError, PermissionError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@callback_router.get("/oauth/callback/{connection_id}", response_class=HTMLResponse)
async def oauth_callback(connection_id: str, request: Request, code: str = "", state: str | None = None, error: str | None = None):
    if error:
        return HTMLResponse(f"<h1>AetherChat OAuth 授权失败</h1><p>{escape(error)}</p>", status_code=400)
    try:
        mcp_registry.complete_oauth(connection_id, code, state)
    except (KeyError, ValueError) as exc:
        return HTMLResponse(f"<h1>AetherChat OAuth 回调无效</h1><p>{escape(str(exc))}</p>", status_code=400)
    return HTMLResponse("<h1>AetherChat OAuth 授权已接收</h1><p>可以关闭此窗口并返回 AetherChat。</p>")
