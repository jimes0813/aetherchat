import builtins
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..models.schemas import ProviderConfig
from .vault import vault


class ModelCreate(BaseModel):
    name: str
    type: Literal["openai_compat", "anthropic", "xai", "local"] = "openai_compat"
    model: str
    base_url: str | None = None
    api_key: str | None = None
    api_key_ref: str | None = None
    max_tokens: int = 8192
    participation_policy: Literal["always", "mentioned", "project_autonomous"] = "always"
    persona: str | None = None
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    token_limit: int = 128000
    local_runtime: Literal["external", "llama_cpp"] = "external"
    model_path: str | None = None
    runtime_executable: str | None = None
    runtime_args: list[str] = Field(default_factory=list)
    startup_timeout: int = 300


class ModelRegistry:
    def __init__(self):
        self.models: dict[str, dict] = {}
        self.storage_path = self._storage_path()
        if not self._load():
            self._seed()
            self._save()

    def _storage_path(self) -> Path:
        root = Path(
            os.environ.get(
                "AETHERCHAT_DATA_DIR",
                Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AetherChat",
            )
        )
        root.mkdir(parents=True, exist_ok=True)
        return root / "models.json"

    def _seed(self):
        defaults = [
            ModelCreate(name="claude", type="anthropic", model="claude-3-5-sonnet", persona="Architect/Reviewer", api_key_ref="ANTHROPIC_API_KEY"),
            ModelCreate(name="deepseek", type="openai_compat", model="deepseek-chat", base_url="https://api.deepseek.com", persona="Coder/Reasoner", api_key_ref="DEEPSEEK_API_KEY"),
            ModelCreate(name="grok", type="openai_compat", model="grok-2", persona="Creative", api_key_ref="XAI_API_KEY"),
            ModelCreate(name="qwen", type="openai_compat", model="qwen-plus", persona="Chinese/local + Critic", api_key_ref="QWEN_API_KEY"),
            ModelCreate(name="kimi", type="openai_compat", model="kimi-k2", persona="Researcher", api_key_ref="KIMI_API_KEY"),
            ModelCreate(name="gpt", type="openai_compat", model="gpt-4.1", persona="Implementer", api_key_ref="OPENAI_API_KEY"),
        ]
        for item in defaults:
            self.create(item, overwrite=True, persist=False)

    def _load(self) -> bool:
        if not self.storage_path.exists():
            return False
        try:
            rows = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(rows, list):
            return False
        for item in rows:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            self.models[item["name"]] = item
        return True

    def _save(self):
        self.storage_path.write_text(
            json.dumps(list(self.models.values()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list(self):
        return list(self.models.values())

    def get(self, name: str):
        return self.models.get(name)

    def create(self, payload: ModelCreate, overwrite: bool = True, persist: bool = True):
        api_key_ref = payload.api_key_ref or f"MODEL_KEY_{payload.name.upper()}"
        if payload.api_key:
            vault.set_key(api_key_ref, payload.api_key)
        if not overwrite and payload.name in self.models:
            raise ValueError("Model already exists")

        item = payload.model_dump(exclude={"api_key"})
        item["api_key_ref"] = api_key_ref
        item["has_api_key"] = bool(vault.get_key(api_key_ref))
        item["created_at"] = self.models.get(payload.name, {}).get("created_at") or datetime.now(UTC).isoformat()
        item["updated_at"] = datetime.now(UTC).isoformat()
        self.models[payload.name] = item
        if persist:
            self._save()
        return item

    def delete(self, name: str):
        item = self.models.pop(name, None)
        if item:
            self._save()
        return item

    def configs(self) -> builtins.list[ProviderConfig]:
        return [
            ProviderConfig(
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
            for item in self.models.values()
        ]


model_registry = ModelRegistry()
