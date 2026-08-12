from __future__ import annotations

import asyncio
import builtins
import json
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal, TypedDict
from urllib.parse import urlparse

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl, BaseModel, Field, field_validator, model_validator

from ..models.schemas import ToolDefinition
from .vault import SimpleVault, vault


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    if not normalized or len(normalized) > 64:
        raise ValueError("连接器 ID 必须包含字母或数字，且不超过 64 个字符")
    return normalized


def _tool_segment(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_")
    return normalized[:80] or "tool"


class MCPConnectionCreate(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=120)
    transport: Literal["stdio", "streamable_http"]
    command: str | None = Field(default=None, max_length=1000)
    args: list[str] = Field(default_factory=list, max_length=64)
    cwd: str | None = Field(default=None, max_length=2000)
    url: str | None = Field(default=None, max_length=2000)
    auth_type: Literal["none", "bearer", "oauth_token", "oauth"] = "none"
    token: str | None = Field(default=None, max_length=20_000)
    enabled: bool = True
    allowed_tools: list[str] | None = None

    @field_validator("args")
    @classmethod
    def validate_args(cls, values: list[str]) -> list[str]:
        if any("\x00" in str(value) or len(str(value)) > 4000 for value in values):
            raise ValueError("stdio 参数包含无效字符或过长内容")
        return [str(value) for value in values]

    @model_validator(mode="after")
    def validate_transport(self):
        if self.transport == "stdio":
            if not self.command or "\x00" in self.command:
                raise ValueError("stdio 连接必须提供有效命令")
            if self.url:
                raise ValueError("stdio 连接不能设置 URL")
        else:
            if not self.url:
                raise ValueError("Streamable HTTP 连接必须提供 URL")
            parsed = urlparse(self.url)
            if parsed.username or parsed.password or parsed.fragment:
                raise ValueError("MCP URL 不能包含凭据或片段")
            local = (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
            if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
                raise ValueError("远程 MCP 必须使用 HTTPS；HTTP 仅允许本机地址")
            if self.command or self.args or self.cwd:
                raise ValueError("Streamable HTTP 连接不能设置本地命令")
        return self


class MCPConnectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    allowed_tools: list[str] | None = None
    token: str | None = Field(default=None, max_length=20_000)
    clear_token: bool = False


class OAuthFlow(TypedDict):
    redirect_event: asyncio.Event
    callback_event: asyncio.Event
    authorization_url: str | None
    code: str | None
    state: str | None


class VaultOAuthStorage(TokenStorage):
    def __init__(self, credential_vault: SimpleVault, connection_id: str):
        self.vault = credential_vault
        prefix = connection_id.upper().replace("-", "_")
        self.tokens_ref = f"MCP_OAUTH_TOKENS_{prefix}"
        self.client_ref = f"MCP_OAUTH_CLIENT_{prefix}"

    async def get_tokens(self) -> OAuthToken | None:
        value = self.vault.get_key(self.tokens_ref)
        if not value:
            return None
        try:
            return OAuthToken.model_validate_json(value)
        except Exception:
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.vault.set_key(self.tokens_ref, tokens.model_dump_json())

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        value = self.vault.get_key(self.client_ref)
        if not value:
            return None
        try:
            return OAuthClientInformationFull.model_validate_json(value)
        except Exception:
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.vault.set_key(self.client_ref, client_info.model_dump_json())

    def clear(self) -> None:
        self.vault.delete_key(self.tokens_ref)
        self.vault.delete_key(self.client_ref)


class MCPRegistry:
    def __init__(self, base_path: str | os.PathLike[str] | None = None, credential_vault: SimpleVault | None = None):
        if base_path:
            root = Path(base_path).expanduser().resolve()
        else:
            root = Path(os.environ.get("AETHERCHAT_DATA_DIR", Path.home() / ".local" / "share" / "aetherchat")).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "mcp_connections.json"
        self.vault = credential_vault or vault
        self._lock = RLock()
        self._connections: dict[str, dict[str, Any]] = {}
        self._oauth_flows: dict[str, OAuthFlow] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(rows, list):
            return
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                row["status"] = "ready" if row.get("enabled") and row.get("tools") else "disabled" if not row.get("enabled") else "disconnected"
                row["error"] = None
                self._connections[str(row["id"])] = row

    def _save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        rows = []
        for connection in self._connections.values():
            rows.append({key: value for key, value in connection.items() if key not in {"status", "error", "has_token"}})
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        with suppress(OSError):
            temporary.chmod(0o600)
        temporary.replace(self.path)

    @staticmethod
    def _token_ref(connection_id: str) -> str:
        return f"MCP_TOKEN_{connection_id.upper().replace('-', '_')}"

    def _public(self, connection: dict[str, Any]) -> dict[str, Any]:
        item = deepcopy(connection)
        oauth_tokens = self.vault.get_key(VaultOAuthStorage(self.vault, item["id"]).tokens_ref)
        item["has_token"] = bool(self.vault.get_key(item["token_ref"]) or oauth_tokens)
        item.pop("token", None)
        return item

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public(item) for item in self._connections.values()]

    def get(self, connection_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._connections.get(str(connection_id))
            return self._public(item) if item else None

    async def create(self, payload: MCPConnectionCreate) -> dict[str, Any]:
        connection_id = _safe_id(payload.id or payload.name)
        with self._lock:
            if connection_id in self._connections:
                raise ValueError("MCP 连接器 ID 已存在")
        token_ref = self._token_ref(connection_id)
        if payload.token:
            self.vault.set_key(token_ref, payload.token)
        now = _utc_now()
        item = {
            **payload.model_dump(exclude={"token", "allowed_tools"}),
            "id": connection_id,
            "token_ref": token_ref,
            "allowed_tools": payload.allowed_tools,
            "tools": [],
            "server_info": {},
            "status": "disconnected",
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._connections[connection_id] = item
            self._save()
        try:
            if payload.enabled:
                await self.refresh(connection_id)
        except Exception:
            pass
        return self.get(connection_id) or {}

    def update(self, connection_id: str, payload: MCPConnectionPatch) -> dict[str, Any] | None:
        with self._lock:
            item = self._connections.get(str(connection_id))
            if item is None:
                return None
            patch = payload.model_dump(exclude_none=True, exclude={"token", "clear_token"})
            item.update(patch)
            item["updated_at"] = _utc_now()
            if not item["enabled"]:
                item["status"] = "disabled"
            self._save()
        if payload.token is not None:
            self.vault.set_key(item["token_ref"], payload.token)
        if payload.clear_token:
            self.vault.delete_key(item["token_ref"])
        return self.get(connection_id)

    def delete(self, connection_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._connections.pop(str(connection_id), None)
            if item is None:
                return None
            self._save()
        self.vault.delete_key(item["token_ref"])
        VaultOAuthStorage(self.vault, item["id"]).clear()
        return self._public(item)

    def _connection(self, connection_id: str) -> dict[str, Any]:
        with self._lock:
            item = deepcopy(self._connections.get(str(connection_id)))
        if item is None:
            raise KeyError(connection_id)
        if not item.get("enabled"):
            raise PermissionError("MCP 连接器已停用")
        return item

    @asynccontextmanager
    async def _session(self, connection: dict[str, Any]) -> AsyncIterator[tuple[ClientSession, Any]]:
        if connection["transport"] == "stdio":
            cwd = None
            if connection.get("cwd"):
                cwd_path = Path(connection["cwd"]).expanduser().resolve()
                if not cwd_path.is_dir():
                    raise ValueError("stdio 工作目录不存在")
                cwd = str(cwd_path)
            env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}}
            params = StdioServerParameters(
                command=connection["command"], args=connection.get("args") or [], env=env, cwd=cwd,
            )
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                initialized = await session.initialize()
                yield session, initialized
            return

        headers = {"Accept": "application/json, text/event-stream"}
        token = self.vault.get_key(connection["token_ref"])
        if connection["auth_type"] in {"bearer", "oauth_token"}:
            if not token:
                raise PermissionError("MCP 连接器缺少访问令牌")
            headers["Authorization"] = f"Bearer {token}"
        oauth_provider = None
        if connection["auth_type"] == "oauth":
            redirect_uri = connection.get("oauth_redirect_uri")
            if not redirect_uri:
                raise PermissionError("MCP 连接器尚未完成 OAuth 授权")
            oauth_provider = self._oauth_provider(connection, redirect_uri)
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with (
            httpx.AsyncClient(
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                auth=oauth_provider,
            ) as client,
            streamable_http_client(connection["url"], http_client=client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            initialized = await session.initialize()
            yield session, initialized

    def _oauth_provider(
        self,
        connection: dict[str, Any],
        redirect_uri: str,
        redirect_handler=None,
        callback_handler=None,
    ) -> OAuthClientProvider:
        metadata = OAuthClientMetadata(
            redirect_uris=[AnyUrl(redirect_uri)],
            client_name="AetherChat Desktop",
            software_id="aetherchat-desktop",
            software_version="0.2.0",
        )
        return OAuthClientProvider(
            connection["url"], metadata,
            storage=VaultOAuthStorage(self.vault, connection["id"]),
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            timeout=300.0,
        )

    @staticmethod
    def _validate_oauth_callback(callback_url: str, connection_id: str) -> str:
        parsed = urlparse(callback_url)
        if parsed.scheme != "http" or (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("OAuth 回调必须使用本机 HTTP 地址")
        if not parsed.path.endswith(f"/mcp-connections/oauth/callback/{connection_id}"):
            raise ValueError("OAuth 回调路径与连接器不匹配")
        return callback_url

    async def start_oauth(self, connection_id: str, callback_url: str) -> dict[str, Any]:
        connection = self._connection(connection_id)
        if connection["transport"] != "streamable_http" or connection["auth_type"] != "oauth":
            raise ValueError("只有 OAuth Streamable HTTP 连接器可以启动浏览器授权")
        callback_url = self._validate_oauth_callback(callback_url, connection_id)
        if connection_id in self._oauth_flows:
            raise ValueError("此连接器已有进行中的 OAuth 授权")
        flow: OAuthFlow = {
            "redirect_event": asyncio.Event(), "callback_event": asyncio.Event(),
            "authorization_url": None, "code": None, "state": None,
        }
        self._oauth_flows[connection_id] = flow

        async def redirect_handler(url: str) -> None:
            flow["authorization_url"] = url
            flow["redirect_event"].set()

        async def callback_handler() -> tuple[str, str | None]:
            await asyncio.wait_for(flow["callback_event"].wait(), timeout=300)
            return str(flow["code"]), flow["state"]

        async def authorize() -> None:
            try:
                provider = self._oauth_provider(connection, callback_url, redirect_handler, callback_handler)
                timeout = httpx.Timeout(30.0, connect=10.0)
                async with (
                    httpx.AsyncClient(
                        auth=provider,
                        timeout=timeout,
                        follow_redirects=False,
                        trust_env=False,
                    ) as client,
                    streamable_http_client(connection["url"], http_client=client) as (read, write, _),
                    ClientSession(read, write) as session,
                ):
                    initialized = await session.initialize()
                    tools_result = await session.list_tools()
                    tools: builtins.list[dict[str, Any]] = [{
                        "name": tool.name, "title": getattr(tool, "title", None),
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
                    } for tool in tools_result.tools]
                    exposed = [self.tool_name(connection_id, tool["name"]) for tool in tools]
                    if len(exposed) != len(set(exposed)):
                        raise ValueError("MCP 服务器工具名称规范化后发生冲突")
                    with self._lock:
                        active_connection = self._connections[connection_id]
                        active_connection["tools"] = tools
                        active_connection["server_info"] = initialized.serverInfo.model_dump(mode="json")
                        active_connection["oauth_redirect_uri"] = callback_url
                        active_connection["status"] = "ready"
                        active_connection["error"] = None
                        active_connection["updated_at"] = _utc_now()
                        self._save()
            except Exception as exc:
                with self._lock:
                    failed_connection = self._connections.get(connection_id)
                    if failed_connection:
                        failed_connection["status"] = "error"
                        failed_connection["error"] = f"OAuth 授权失败：{str(exc)[:1800]}"
                        failed_connection["updated_at"] = _utc_now()
                        self._save()
            finally:
                self._oauth_flows.pop(connection_id, None)

        with self._lock:
            current = self._connections[connection_id]
            current["status"] = "authorizing"
            current["error"] = None
        asyncio.create_task(authorize())
        try:
            await asyncio.wait_for(flow["redirect_event"].wait(), timeout=30)
        except TimeoutError as exc:
            self._oauth_flows.pop(connection_id, None)
            raise RuntimeError("OAuth 服务器未及时返回授权地址") from exc
        return {"connection_id": connection_id, "authorization_url": flow["authorization_url"]}

    def complete_oauth(self, connection_id: str, code: str, state: str | None) -> None:
        flow = self._oauth_flows.get(connection_id)
        if flow is None:
            raise KeyError("OAuth 授权请求不存在或已过期")
        if not code:
            raise ValueError("OAuth 回调缺少授权码")
        flow["code"] = code
        flow["state"] = state
        flow["callback_event"].set()

    async def refresh(self, connection_id: str) -> dict[str, Any]:
        connection = self._connection(connection_id)
        try:
            async with asyncio.timeout(35):
                async with self._session(connection) as (session, initialized):
                    result = await session.list_tools()
                    tools: builtins.list[dict[str, Any]] = [
                        {
                            "name": tool.name,
                            "title": getattr(tool, "title", None),
                            "description": tool.description or "",
                            "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
                        }
                        for tool in result.tools
                    ]
                    exposed_names = [self.tool_name(connection_id, tool["name"]) for tool in tools]
                    if len(exposed_names) != len(set(exposed_names)):
                        raise ValueError("MCP 服务器工具名称规范化后发生冲突，已拒绝暴露")
                    server_info = initialized.serverInfo.model_dump(mode="json") if initialized else {}
        except Exception as exc:
            with self._lock:
                current = self._connections[connection_id]
                current["status"] = "error"
                current["error"] = str(exc)[:2000]
                current["updated_at"] = _utc_now()
                self._save()
            raise
        with self._lock:
            current = self._connections[connection_id]
            current["tools"] = tools
            current["server_info"] = server_info
            current["status"] = "ready"
            current["error"] = None
            current["updated_at"] = _utc_now()
            self._save()
        return self.get(connection_id) or {}

    def tool_definitions(self) -> builtins.list[ToolDefinition]:
        definitions: builtins.list[ToolDefinition] = []
        for connection in self.list():
            if not connection["enabled"] or connection["status"] != "ready":
                continue
            allowed = connection.get("allowed_tools")
            for tool in connection.get("tools") or []:
                if allowed is not None and tool["name"] not in allowed:
                    continue
                definitions.append(ToolDefinition(
                    name=self.tool_name(connection["id"], tool["name"]),
                    description=f"MCP {connection['name']}：{tool.get('description') or tool['name']}",
                    parameters=deepcopy(tool.get("input_schema") or {"type": "object", "properties": {}}),
                ))
        return definitions

    def resolve_tool_name(self, exposed_name: str) -> tuple[str, str] | None:
        for connection in self.list():
            for tool in connection.get("tools") or []:
                if self.tool_name(connection["id"], tool["name"]) == exposed_name:
                    return connection["id"], tool["name"]
        return None

    @staticmethod
    def tool_name(connection_id: str, remote_name: str) -> str:
        return f"mcp__{_tool_segment(connection_id)}__{_tool_segment(remote_name)}"

    async def invoke_tool(
        self,
        exposed_name: str,
        arguments: dict[str, Any],
        *,
        session_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        match = self.resolve_tool_name(exposed_name)
        if match is None:
            raise KeyError(exposed_name)
        from .platform_governance import platform_governance

        platform_governance.require_tool_invoke(
            exposed_name,
            project_id=project_id,
            session_id=session_id,
        )
        connection_id, remote_name = match
        connection = self._connection(connection_id)
        allowed = connection.get("allowed_tools")
        if allowed is not None and remote_name not in allowed:
            raise PermissionError("此 MCP 工具未获准暴露给模型")
        async with asyncio.timeout(120):
            async with self._session(connection) as (session, _initialized):
                result = await session.call_tool(remote_name, arguments)
        content = []
        for item in result.content[:50]:
            payload = item.model_dump(mode="json")
            if payload.get("type") == "image" and payload.get("data") and payload.get("mimeType"):
                payload["image_data_url"] = f"data:{payload['mimeType']};base64,{payload.pop('data')}"
            content.append(payload)
        return {
            "connection_id": connection_id,
            "tool_name": remote_name,
            "status": "error" if result.isError else "success",
            "is_error": bool(result.isError),
            "content": content,
            "structured_content": getattr(result, "structuredContent", None),
        }


mcp_registry = MCPRegistry()
