from fastapi import APIRouter, HTTPException

from ..models.schemas import (
    ModelDeleted,
    ModelRecord,
    ModelRuntimeStatus,
    ModelVerifyResult,
    ProviderConfig,
)
from ..providers.base import get_provider
from ..services.local_model_runtime import LocalModelRuntimeError, local_model_runtime
from ..services.model_registry import ModelCreate, model_registry

router = APIRouter()


def _provider_config(item: dict) -> ProviderConfig:
    return ProviderConfig(
        name=item["name"],
        type=item["type"],
        model=item["model"],
        base_url=item.get("base_url"),
        api_key_ref=item["api_key_ref"],
        max_tokens=item.get("max_tokens", 8192),
        participation_policy=item.get("participation_policy", "always"),
        persona=item.get("persona"),
        cost_per_1k_input=item.get("cost_per_1k_input", 0.0),
        cost_per_1k_output=item.get("cost_per_1k_output", 0.0),
        token_limit=item.get("token_limit", 128000),
        local_runtime=item.get("local_runtime", "external"),
        model_path=item.get("model_path"),
        runtime_executable=item.get("runtime_executable"),
        runtime_args=item.get("runtime_args", []),
        startup_timeout=item.get("startup_timeout", 300),
    )


@router.get("/", response_model=list[ModelRecord])
async def list_models():
    return model_registry.list()


@router.post("/", response_model=ModelRecord)
async def create_model(payload: ModelCreate):
    try:
        return model_registry.create(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{name}/verify", response_model=ModelVerifyResult)
async def verify_model(name: str):
    item = model_registry.get(name)
    if not item:
        raise HTTPException(status_code=404, detail="Model not found")
    try:
        provider = get_provider(_provider_config(item))
        return await provider.verify_connection()
    except HTTPException:
        raise
    except Exception as exc:
        return {"ok": False, "message": f"真实对话检测失败：{exc}"}


@router.get("/{name}/runtime", response_model=ModelRuntimeStatus)
async def model_runtime_status(name: str):
    item = model_registry.get(name)
    if not item:
        raise HTTPException(status_code=404, detail="Model not found")
    return await local_model_runtime.status(_provider_config(item))


@router.post("/{name}/start", response_model=ModelRuntimeStatus)
async def start_model(name: str):
    item = model_registry.get(name)
    if not item:
        raise HTTPException(status_code=404, detail="Model not found")
    config = _provider_config(item)
    if config.type != "local":
        return {"ready": True, "managed": False, "started": False, "type": config.type}
    try:
        return await local_model_runtime.ensure_ready(config)
    except LocalModelRuntimeError as exc:
        raise HTTPException(status_code=503, detail=exc.as_detail()) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/{name}", response_model=ModelDeleted)
async def delete_model(name: str):
    deleted = model_registry.delete(name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Model not found")
    return {"deleted": True, "model": deleted}
