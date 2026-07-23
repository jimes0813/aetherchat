from fastapi import APIRouter, HTTPException

from ..services.plugin_registry import (
    PluginInvoke,
    PluginManifest,
    PluginUpdate,
    plugin_registry,
)

router = APIRouter()


@router.get("/")
async def list_plugins():
    return plugin_registry.list()


@router.post("/")
async def install_plugin(payload: PluginManifest):
    try:
        return plugin_registry.install(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{plugin_id}")
async def update_plugin(plugin_id: str, payload: PluginUpdate):
    try:
        updated = plugin_registry.update(plugin_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return updated


@router.delete("/{plugin_id}")
async def uninstall_plugin(plugin_id: str):
    try:
        removed = plugin_registry.uninstall(plugin_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if removed is None:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return {"deleted": True, "plugin": removed}


@router.post("/{plugin_id}/invoke")
async def invoke_plugin(plugin_id: str, payload: PluginInvoke):
    try:
        return await plugin_registry.invoke(plugin_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Plugin or action not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
