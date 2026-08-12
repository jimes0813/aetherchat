# backend/app/models/schemas.py
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    name: str
    type: Literal["openai_compat", "anthropic", "xai", "local"]
    model: str
    base_url: str | None = None
    api_key_ref: str
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


class ToolCall(BaseModel):
    """Provider-neutral function call emitted by an assistant."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    name: str | None = None
    images: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    tool_error: bool = False

class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})

class Session(BaseModel):
    id: UUID
    tenant_id: UUID
    owner_id: UUID
    title: str
    mode: Literal["casual", "roundtable", "project"]
    active_providers: list[str]
    config: dict[str, Any] = Field(default_factory=dict)
    budget_usd: float = 10.0
    created_at: datetime

class Message(BaseModel):
    id: UUID
    session_id: UUID
    author_user_id: UUID | None = None
    author_model: str
    role: str
    content: str
    reply_to_id: UUID | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    generated_files: list[dict[str, Any]] = Field(default_factory=list)
    reasoning_summary: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime

class Task(BaseModel):
    id: UUID
    session_id: UUID
    title: str
    description: str
    status: Literal["backlog", "claimed", "in_progress", "review", "merged", "done"]
    claimed_by: str | None = None
    priority: int = 0
    depends_on: list[UUID] = Field(default_factory=list)

class Artifact(BaseModel):
    id: UUID
    session_id: UUID
    path: str
    kind: Literal["code", "doc", "prompt", "dataset", "binary"]
    current_hash: str
    updated_at: datetime | None = None

class MergeProposal(BaseModel):
    id: UUID
    artifact_id: UUID | None = None
    session_id: UUID | None = None
    artifact_path: str | None = None
    proposer: str
    from_hash: str | None = None
    to_hash: str | None = None
    rationale: str
    status: Literal["open", "merged", "rejected"]
    votes: dict[str, bool] = Field(default_factory=dict)

class WSMessage(BaseModel):
    type: str
    model: str | None = None
    delta: str | None = None
    coalesced_count: int | None = None
    seq: int | None = None
    ts: str | None = None
    message: Message | None = None


# --- Public API response models (match runtime SQLite/registry payloads) ---


class SessionSummary(BaseModel):
    """Lightweight session row returned by list/create session endpoints."""

    id: str
    title: str
    summary: str = ""
    mode: str = "project"
    created_at: str | None = None
    updated_at: str | None = None
    last_message_at: str | None = None
    message_count: int = 0
    task_count: int = 0
    open_task_count: int = 0


class ModelRecord(BaseModel):
    """Model registry record returned by list/create/delete model endpoints."""

    name: str
    type: Literal["openai_compat", "anthropic", "xai", "local"]
    model: str
    base_url: str | None = None
    api_key_ref: str
    max_tokens: int = 8192
    participation_policy: Literal["always", "mentioned", "project_autonomous"] = "always"
    persona: str | None = None
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    token_limit: int = 128000
    local_runtime: Literal["external", "llama_cpp"] | None = None
    model_path: str | None = None
    runtime_executable: str | None = None
    runtime_args: list[str] = Field(default_factory=list)
    startup_timeout: int | None = None
    has_api_key: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class ModelVerifyResult(BaseModel):
    ok: bool
    message: str
    sample: str | None = None
    model: str | None = None
    base_url: str | None = None
    runtime: dict[str, Any] | None = None


class ModelRuntimeStatus(BaseModel):
    ready: bool = False
    managed: bool = False
    started: bool = False
    type: str | None = None
    message: str | None = None
    base_url: str | None = None
    pid: int | None = None

    model_config = {"extra": "allow"}


class ModelDeleted(BaseModel):
    deleted: bool
    model: ModelRecord | dict[str, Any]


class HealthStatus(BaseModel):
    status: str
    service: str
    redis: bool = False
    automation: dict[str, Any] = Field(default_factory=dict)
    run_dispatcher: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ReadinessStatus(BaseModel):
    status: str
    ready: bool
    service: str | None = None
    request_id: str | None = None
    checks: dict[str, Any] = Field(default_factory=dict)
    optional_dependencies: dict[str, Any] = Field(default_factory=dict)
    code: str | None = None
    message: str | None = None

    model_config = {"extra": "allow"}


class SessionDeleted(BaseModel):
    deleted: bool
    session_id: str
    cleanup: dict[str, Any] = Field(default_factory=dict)
    task: Task | None = None
    budget_remaining: float | None = None
    content: str | None = None
    step: dict[str, Any] | None = None
    reset: bool | None = None

class ClientAction(BaseModel):
    type: str
    content: str | None = None
    targets: list[str] | None = None
    mode: Literal["casual", "roundtable", "project"] | None = None
    execution_strategy: Literal["direct", "planned"] | None = None
    goal_spec: dict[str, Any] | None = None
    plan_confirmation_required: bool | None = None
    max_revision_cycles: int | None = Field(default=None, ge=0, le=2)
    expected_revision: int | None = Field(default=None, ge=1)
    plan_steps: list[dict[str, Any]] | None = None
    change_summary: str | None = None
    model: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    task_id: str | None = None
    proposal_id: str | None = None
    attachment_ids: list[str] = Field(default_factory=list)


class ProjectPostMessageActionV1(ClientAction):
    type: Literal["post_message"]
    mode: Literal["project"]
    execution_strategy: Literal["direct", "planned"]


class DirectPostMessageActionV1(ClientAction):
    type: Literal["post_message"]
    mode: Literal["casual", "roundtable"]
    execution_strategy: Literal["direct"] = "direct"


PostMessageActionV1 = Annotated[
    ProjectPostMessageActionV1 | DirectPostMessageActionV1,
    Field(discriminator="mode"),
]
