from fastapi import APIRouter, HTTPException, Query

from ..services.tool_registry import ToolCreate, ToolInvoke, tool_registry

router = APIRouter()


@router.get("/")
async def list_tools(session_id: str | None = None):
    return tool_registry.list(session_id)


@router.get("/runs")
async def list_tool_runs(
    session_id: str | None = None,
    limit: int | None = Query(default=50, ge=1, le=5000),
):
    return tool_registry.list_runs(session_id=session_id, limit=limit)


@router.post("/")
async def create_tool(payload: ToolCreate):
    return tool_registry.create(payload)


@router.delete("/{tool_id}")
async def delete_tool(tool_id: str):
    deleted = tool_registry.delete(tool_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Tool not found")
    return {"deleted": True, "tool": deleted}


@router.post("/{tool_id}/invoke")
async def invoke_tool(
    tool_id: str,
    payload: ToolInvoke,
    session_id: str | None = None,
):
    tool = tool_registry.get(tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    resolved_session = session_id or payload.session_id or payload.arguments.get("session_id") or tool.get("session_id") or "default"
    if tool.get("session_id") is not None and str(tool["session_id"]) != str(resolved_session):
        raise HTTPException(status_code=404, detail="Tool not found")

    try:
        return await tool_registry.invoke_async(tool_id, payload, session_id=str(resolved_session))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Tool not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
