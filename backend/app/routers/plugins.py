from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services.comfyui_runtime import comfyui_runtime
from ..services.comfyui_service import ComfyUIError, ComfyUIService
from ..services.plugin_registry import (
    PluginInvoke,
    PluginManifest,
    PluginUpdate,
    plugin_registry,
)

router = APIRouter()


class ComfyUISelection(BaseModel):
    image_model: str | None = Field(default=None, max_length=500)
    video_pipeline: Literal["wan-i2v", "ltx-t2v", "minimax-h3"] | None = None


def _comfyui_selection() -> dict[str, str | None]:
    plugin = plugin_registry.get("comfyui") or {}
    raw_config = plugin.get("config")
    config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
    return {
        "image_model": str(config.get("image_model") or "").strip() or None,
        "video_pipeline": str(config.get("video_pipeline") or "").strip() or None,
    }


async def _comfyui_overview(*, start: bool) -> dict:
    service = ComfyUIService()
    runtime = (
        await service.ensure_ready()
        if start
        else await comfyui_runtime.status(service.base_url)
    )
    catalog = (
        await service.model_catalog(_ensure_runtime=False)
        if runtime["ready"]
        else None
    )
    return {
        "ready": runtime["ready"],
        "runtime": runtime,
        "diagnostics": runtime.get("diagnostics", {}),
        "selection": _comfyui_selection(),
        "catalog": catalog,
    }


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


@router.get("/comfyui/runtime/status")
async def comfyui_runtime_status():
    try:
        return await _comfyui_overview(start=False)
    except ComfyUIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/comfyui/runtime/start")
async def start_comfyui_runtime():
    try:
        return await _comfyui_overview(start=True)
    except ComfyUIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/comfyui/selection")
async def update_comfyui_selection(payload: ComfyUISelection):
    service = ComfyUIService()
    try:
        catalog = await service.model_catalog()
    except ComfyUIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    image_models = {
        str(item.get("name") or "")
        for item in catalog["image_models"]
    }
    if payload.image_model and payload.image_model not in image_models:
        raise HTTPException(status_code=422, detail="所选图像模型当前不可用")
    pipeline = next(
        (
            item
            for item in catalog["video_pipelines"]
            if item["id"] == payload.video_pipeline
        ),
        None,
    )
    if payload.video_pipeline and (
        pipeline is None or not pipeline["installed"]
    ):
        raise HTTPException(status_code=422, detail="所选视频管线缺少必要模型或节点")
    updated = plugin_registry.update(
        "comfyui",
        PluginUpdate(config=payload.model_dump()),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="ComfyUI plugin not found")
    return await _comfyui_overview(start=False)


@router.post("/comfyui/runtime/stop")
async def stop_comfyui_runtime():
    return await comfyui_runtime.shutdown()


@router.post("/{plugin_id}/invoke")
async def invoke_plugin(plugin_id: str, payload: PluginInvoke):
    try:
        return await plugin_registry.invoke(plugin_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Plugin or action not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ComfyUIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
