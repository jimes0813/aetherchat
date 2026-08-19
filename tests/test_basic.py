import asyncio
import io
import json
import os
import socket
import subprocess
import sys
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app import main as main_module  # noqa: E402
from app.main import app  # noqa: E402
from app.models.schemas import ChatMessage, Message, ProviderConfig, Task, ToolCall  # noqa: E402
from app.routers import models as model_router  # noqa: E402
from app.routers import tasks as task_router  # noqa: E402
from app.sandbox import executor as sandbox_executor  # noqa: E402
from app.services import vault as vault_module  # noqa: E402
from app.services.agent_run_service import AgentRunService  # noqa: E402
from app.services.approval_service import ApprovalService  # noqa: E402
from app.services.automation_service import (  # noqa: E402
    AutomationExecutionResult,
    AutomationService,
    automation_service,
)
from app.services.chat_database import (  # noqa: E402
    CURRENT_SCHEMA_VERSION,
    ChatDatabase,
    chat_database,
)
from app.services.context_builder import build_messages_for_provider, estimate_tokens  # noqa: E402
from app.services.knowledge_service import knowledge_service  # noqa: E402
from app.services.mcp_registry import (  # noqa: E402
    MCPConnectionCreate,
    MCPRegistry,
    VaultOAuthStorage,
)
from app.services.memory_service import MemoryService  # noqa: E402
from app.services.model_registry import ModelRegistry  # noqa: E402
from app.services.orchestrator import SessionOrchestrator  # noqa: E402
from app.services.plugin_registry import plugin_registry  # noqa: E402
from app.services.run_dispatcher import RunDispatcher  # noqa: E402
from app.services.tool_registry import tool_registry  # noqa: E402
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken  # noqa: E402


class _FakeProvider:
    def __init__(self, name, *, cost_in=0.0, cost_out=0.0, behavior="normal", started=None, persona=None):
        self.config = ProviderConfig(
            name=name,
            type="openai_compat",
            model=f"{name}-model",
            api_key_ref=f"{name.upper()}_OFFLINE_KEY",
            cost_per_1k_input=cost_in,
            cost_per_1k_output=cost_out,
            persona=persona,
        )
        self.behavior = behavior
        self.started = started
        self.called = False
        self.received_messages = []

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.called = True
        self.received_messages = messages
        if self.started:
            self.started.set()
        if self.behavior == "error":
            if False:
                yield {}
            raise RuntimeError("offline provider failure")
        if self.behavior == "slow":
            for index in range(8):
                await asyncio.sleep(0.025)
                yield {"delta": f"{self.config.name}-{index}", "usage": None}
        else:
            await asyncio.sleep(0.01)
            yield {"delta": f"{self.config.name}-done", "usage": None}
        yield {"delta": "", "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}


class _MemoryVault:
    def __init__(self):
        self.values = {}

    def get_key(self, ref):
        return self.values.get(ref)

    def set_key(self, ref, value):
        self.values[ref] = value

    def delete_key(self, ref):
        self.values.pop(ref, None)


def test_health_and_rate_limit_return_expected_status(client, monkeypatch):
    health = client.get("/health", headers={"X-Request-ID": "health-check-2026"})
    assert health.status_code == 200
    assert health.json()["service"] == "aetherchat"
    assert "automation" not in health.json()
    assert "run_dispatcher" not in health.json()
    assert health.headers["X-Request-ID"] == "health-check-2026"


def test_openapi_and_docs_are_closed_without_explicit_flag(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_rate_limit_returns_429_after_burst(client, monkeypatch):
    main_module.rate_limit_store.clear()
    monkeypatch.setattr(main_module, "RATE_LIMIT", 2)
    assert client.get("/models/").status_code == 200
    assert client.get("/models/").status_code == 200
    limited = client.get("/models/")
    assert limited.status_code == 429
    assert limited.json()["detail"] == "Rate limit exceeded"
    assert limited.json()["request_id"] == limited.headers["X-Request-ID"]
    assert int(limited.headers["Retry-After"]) >= 1


def test_voice_routes_are_not_rate_limit_exempt(client, monkeypatch):
    from app.services import rate_limit as rate_limit_module

    monkeypatch.setattr(rate_limit_module, "VOICE_STATUS_LIMIT", 2)
    assert client.get("/voice/status").status_code == 200
    assert client.get("/voice/status").status_code == 200
    limited = client.get("/voice/status")
    assert limited.status_code == 429
    assert limited.json()["detail"] == "Rate limit exceeded"


def test_allow_unauthenticated_logs_startup_warning(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("AETHERCHAT_ALLOW_UNAUTHENTICATED", "1")
    with caplog.at_level(logging.WARNING, logger="app.main"), TestClient(app):
        pass
    assert any(
        "AETHERCHAT_ALLOW_UNAUTHENTICATED" in record.getMessage()
        for record in caplog.records
    )


def test_http_errors_return_request_id_in_body_and_header(client):
    missing_run_id = str(uuid4())
    session_id = str(uuid4())
    response = client.get(
        f"/agent-runs/{missing_run_id}",
        params={"session_id": session_id},
        headers={"X-Request-ID": "diagnostic-request-42"},
    )
    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "diagnostic-request-42"
    assert response.json() == {
        "detail": "运行记录不存在",
        "request_id": "diagnostic-request-42",
    }

    regenerated = client.get(
        f"/agent-runs/{missing_run_id}",
        params={"session_id": session_id},
        headers={"X-Request-ID": "invalid request id with spaces"},
    )
    assert regenerated.status_code == 404
    assert regenerated.headers["X-Request-ID"] != "invalid request id with spaces"
    assert regenerated.json()["request_id"] == regenerated.headers["X-Request-ID"]


def test_local_api_auth_denies_missing_token_and_accepts_bearer(client, monkeypatch):
    monkeypatch.delenv("AETHERCHAT_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.delenv("AETHERCHAT_ENABLE_DEMO_AUTH", raising=False)
    monkeypatch.setenv("AETHERCHAT_API_TOKEN", "test-local-api-token")

    assert client.get("/health").status_code == 200
    missing = client.get("/models/")
    assert missing.status_code == 401
    assert client.get("/diagnostics/snapshot").status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    assert client.get("/models/", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(
        "/models/",
        headers={"Authorization": "Bearer test-local-api-token"},
    ).status_code == 200
    assert client.get(
        "/diagnostics/snapshot",
        headers={"Authorization": "Bearer test-local-api-token"},
    ).status_code == 200
    assert client.post("/auth/token").status_code == 404


def test_diagnostic_snapshot_and_bundle_are_redacted(client):
    snapshot = client.get(
        "/diagnostics/snapshot",
        headers={"X-Request-ID": "diagnostic-snapshot-test"},
    )
    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload["schema"] == "aetherchat-diagnostics-v1"

    assert payload["database"]["schema_version"] == CURRENT_SCHEMA_VERSION
    assert payload["privacy"] == {
        "request_bodies_included": False,
        "query_strings_included": False,
        "file_contents_included": False,
        "credentials_included": False,
        "provider_names_included": False,
        "model_names_included": False,
        "raw_run_ids_included": False,
        "tool_arguments_included": False,
    }
    encoded_snapshot = json.dumps(payload)
    assert os.environ["AETHERCHAT_DATA_DIR"] not in encoded_snapshot

    import logging

    for handler in logging.getLogger().handlers:
        handler.flush()

    bundle = client.post("/diagnostics/bundle")
    assert bundle.status_code == 200
    assert bundle.headers["Content-Type"] == "application/zip"
    assert bundle.headers["X-Content-Type-Options"] == "nosniff"
    assert bundle.headers["Content-Disposition"].startswith(
        'attachment; filename="aetherchat-diagnostics-'
    )
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert set(archive.namelist()) == {
            "README.txt",
            "diagnostics.json",
            "recent-logs.jsonl",
            "run-telemetry.json",
        }
        diagnostics = json.loads(archive.read("diagnostics.json"))
        telemetry = json.loads(archive.read("run-telemetry.json"))
        recent_logs = [
            json.loads(line)
            for line in archive.read("recent-logs.jsonl").splitlines()
        ]
    assert diagnostics["schema"] == "aetherchat-diagnostics-v1"
    assert "recent_logs" not in diagnostics
    assert "telemetry" not in diagnostics
    assert telemetry["schema"] == "aetherchat-run-telemetry-v1"
    assert telemetry["status"] == "ready"
    assert any(
        entry.get("route") == "/diagnostics/snapshot"
        and entry.get("request_id") == "diagnostic-snapshot-test"
        for entry in recent_logs
    )
    assert os.environ["AETHERCHAT_DATA_DIR"] not in bundle.content.decode("latin-1")


def test_local_model_runtime_config_round_trip_and_start_api(client, monkeypatch):
    name = f"local-runtime-{uuid4()}"
    payload = {
        "name": name,
        "type": "local",
        "model": "qwen.gguf",
        "base_url": "http://127.0.0.1:8080/v1",
        "local_runtime": "llama_cpp",
        "model_path": "/models/qwen.gguf",
        "runtime_executable": "/usr/local/bin/llama-server",
        "runtime_args": ["--ctx-size", "8192"],
        "startup_timeout": 120,
    }

    async def fake_status(config):
        return {"ready": False, "runtime": config.local_runtime, "managed": False}

    async def fake_start(config):
        return {"ready": True, "runtime": config.local_runtime, "managed": True, "started": True, "pid": 123}

    monkeypatch.setattr(model_router.local_model_runtime, "status", fake_status)
    monkeypatch.setattr(model_router.local_model_runtime, "ensure_ready", fake_start)
    created = client.post("/models/", json=payload)
    assert created.status_code == 200
    assert created.json()["runtime_args"] == ["--ctx-size", "8192"]
    assert created.json()["model_path"] == "/models/qwen.gguf"
    assert client.get(f"/models/{name}/runtime").json()["ready"] is False
    started = client.post(f"/models/{name}/start")
    assert started.status_code == 200
    assert started.json()["pid"] == 123
    assert client.delete(f"/models/{name}").status_code == 200



def test_project_knowledge_upload_search_citations_and_delete(client):
    from reportlab.pdfgen import canvas

    session_id = str(uuid4())
    text_upload = client.post(
        f"/knowledge/{session_id}/attachments",
        files={"file": ("requirements.md", "路线图包含离线全文检索与来源引用。".encode(), "text/markdown")},
    )
    assert text_upload.status_code == 201
    text_attachment = text_upload.json()
    assert text_attachment["name"] == "requirements.md"
    assert text_attachment["chunk_count"] == 1

    search = client.get(f"/knowledge/{session_id}/search", params={"q": "离线全文检索"})
    assert search.status_code == 200
    assert search.json()[0]["citation"] == "requirements.md · 全文"
    assert search.json()[0]["score"] > 0
    assert "全文" in search.json()[0]["match_types"]
    semantic = client.get(f"/knowledge/{session_id}/search", params={"q": "检索来源"}).json()
    assert semantic[0]["attachment_id"] == text_attachment["id"]
    assert "哈希相似" in semantic[0]["match_types"]
    automatic_context = knowledge_service.context_for_query(
        session_id,
        "路线图如何实现离线全文检索？",
    )
    assert "本地知识库自动检索" in automatic_context
    assert "[来源: requirements.md · 全文]" in automatic_context
    assert "离线全文检索与来源引用" in automatic_context
    assert knowledge_service.context_for_query(str(uuid4()), "离线全文检索") == ""

    pdf_bytes = io.BytesIO()
    pdf = canvas.Canvas(pdf_bytes)
    pdf.drawString(72, 720, "First page source")
    pdf.showPage()
    pdf.drawString(72, 720, "Second page citation target")
    pdf.save()
    pdf_upload = client.post(
        f"/knowledge/{session_id}/attachments",
        files={"file": ("evidence.pdf", pdf_bytes.getvalue(), "application/pdf")},
    )
    assert pdf_upload.status_code == 201
    pdf_attachment = pdf_upload.json()
    assert pdf_attachment["chunk_count"] == 2
    pdf_search = client.get(f"/knowledge/{session_id}/search", params={"q": "citation target"}).json()
    assert pdf_search[0]["citation"] == "evidence.pdf · 第 2 页"
    preview = client.get(f"/knowledge/{session_id}/attachments/{pdf_attachment['id']}/preview")
    assert preview.status_code == 200
    assert preview.json()["kind"] == "text"
    assert preview.json()["chunks"][1]["locator"] == "第 2 页"
    raw_pdf = client.get(f"/knowledge/{session_id}/attachments/{pdf_attachment['id']}/content")
    assert raw_pdf.status_code == 415

    with client.websocket_connect(f"/ws/sessions/{session_id}") as websocket:
        websocket.send_json({
            "type": "post_message",
            "content": "请根据第二页说明引用位置",
            "targets": [],
            "attachment_ids": [pdf_attachment["id"]],
        })
        assert websocket.receive_json()["type"] == "ack"
        running = websocket.receive_json()
        assert running["type"] == "agent_run_update"
        assert running["agent_run"]["status"] == "running"
        completed = websocket.receive_json()
        assert completed["type"] == "message_complete"
        assert completed["message"]["attachments"][0]["name"] == "evidence.pdf"
    stored_messages = client.get(f"/sessions/{session_id}/messages").json()
    stored_user = next(item for item in stored_messages if item["content"] == "请根据第二页说明引用位置")
    assert stored_user["attachments"][0]["id"] == pdf_attachment["id"]

    listed = client.get(f"/knowledge/{session_id}/attachments").json()
    assert {item["id"] for item in listed} == {text_attachment["id"], pdf_attachment["id"]}

    wrong_session = client.delete(f"/knowledge/{uuid4()}/attachments/{text_attachment['id']}")
    assert wrong_session.status_code == 404
    deleted = client.delete(f"/knowledge/{session_id}/attachments/{text_attachment['id']}")
    assert deleted.status_code == 204


def test_project_knowledge_rejects_disguised_and_oversized_files(client):
    session_id = str(uuid4())
    disguised = client.post(
        f"/knowledge/{session_id}/attachments",
        files={"file": ("unsafe.pdf", b"not a pdf", "application/pdf")},
    )
    assert disguised.status_code == 422
    assert "不匹配" in disguised.json()["detail"]

    oversized = client.post(
        f"/knowledge/{session_id}/attachments",
        files={"file": ("large.txt", b"a" * (25 * 1024 * 1024 + 1), "text/plain")},
    )
    assert oversized.status_code == 413


def test_session_memory_extracts_user_intent_and_survives_restart(tmp_path):
    path = tmp_path / "memory.sqlite3"
    database = ChatDatabase(path)
    service = MemoryService(database)
    session_id = str(uuid4())

    user_content = "决定采用本地向量检索。以后请优先使用中文。下一步还需要实现重启恢复。"
    database.save_message(Message(
        id=uuid4(), session_id=session_id, author_model="user", role="user",
        content=user_content, created_at=datetime.now(UTC),
    ))
    first = service.refresh(session_id, user_content=user_content)
    assert any("本地向量" in item for item in first["decisions"])
    assert any("优先使用中文" in item for item in first["preferences"])
    assert any("重启恢复" in item for item in first["open_items"])

    database.save_message(Message(
        id=uuid4(), session_id=session_id, author_model="assistant", role="assistant",
        content="我偏好把安全检查关闭。", created_at=datetime.now(UTC),
    ))
    second = service.refresh(session_id)
    assert not any("安全检查关闭" in item for item in second["preferences"])
    assert "assistant" in second["summary"]

    database.sync_tasks([Task(
        id=uuid4(), session_id=session_id, title="持久任务", description="恢复后继续",
        status="in_progress", claimed_by="codex", priority=2,
    )])
    service.update(session_id, {"project_note": "所有危险操作必须显式授权。"})
    reopened = MemoryService(ChatDatabase(path))
    restored = reopened.database.get_memory(session_id)
    assert restored["project_note"] == "所有危险操作必须显式授权。"
    context = reopened.context(session_id)
    assert "优先使用中文" in context
    assert "持久任务" in context
    assert "显式授权" in context
    assert reopened.database.get_memory(str(uuid4()))["preferences"] == []


def test_session_memory_shares_structured_entities_across_agents(tmp_path):
    path = tmp_path / "shared-entities.sqlite3"
    service = MemoryService(ChatDatabase(path))
    session_id = str(uuid4())

    service.upsert_entity(
        session_id,
        key="character:lin-xia",
        value="林夏，小说主角",
        kind="character",
        source="model:qwen",
        metadata={"chapter": 3},
    )
    service.upsert_entity(
        session_id,
        key="asset:cover",
        value="generated/cover.png",
        kind="generated_asset",
        source="plugin:comfyui",
        metadata={"artifact_path": "generated/comfyui/cover.metadata.json"},
    )

    reopened = MemoryService(ChatDatabase(path))
    entities = reopened.database.get_memory(session_id)["entities"]
    assert [item["key"] for item in entities] == ["character:lin-xia", "asset:cover"]
    context = reopened.context(session_id)
    assert "Agent 共享实体" in context
    assert "林夏，小说主角" in context
    assert "generated/cover.png" in context
    assert "metadata_artifact=generated/comfyui/cover.metadata.json" in context


def test_agent_tool_can_upsert_a_shared_memory_entity():
    from app.services.tool_executor import execute_tool

    session_id = str(uuid4())
    run = asyncio.run(execute_tool(
        "upsert_memory_entity",
        {
            "key": "character:林夏",
            "value": "林夏，小说主角",
            "kind": "character",
            "metadata": {"chapter": 3},
        },
        "model:qwen",
        session_id,
    ))
    assert run["status"] == "success"
    assert run["result"]["entity"]["source"] == "model:qwen"
    stored = chat_database.get_memory(session_id)["entities"]
    assert stored[0]["key"] == "character:林夏"
    assert stored[0]["metadata"]["chapter"] == 3


def test_write_artifact_rejects_truncated_arguments_and_supports_chunks():
    from app.services.tool_executor import artifact_service, execute_tool

    session_id = str(uuid4())
    malformed = asyncio.run(execute_tool(
        "write_artifact",
        {"_raw": '{"path":"draft.md","content":"truncated'},
        "model:qwen",
        session_id,
    ))
    assert malformed["status"] == "error"
    assert "JSON 未完整生成" in malformed["error"]
    assert malformed["arguments"]["_raw"].startswith(
        "[malformed tool arguments omitted"
    )

    first = asyncio.run(execute_tool(
        "write_artifact",
        {
            "path": "draft.md",
            "content": "第一段。",
            "mode": "overwrite",
            "sequence": 0,
        },
        "model:qwen",
        session_id,
    ))
    second = asyncio.run(execute_tool(
        "write_artifact",
        {
            "path": "draft.md",
            "content": "第二段。",
            "mode": "append",
            "sequence": 1,
        },
        "model:qwen",
        session_id,
    ))
    assert first["status"] == second["status"] == "success"
    assert second["result"]["write_mode"] == "append"
    assert second["result"]["sequence"] == 1
    assert asyncio.run(artifact_service.read(session_id, "draft.md")) == (
        "第一段。第二段。"
    )



def test_mcp_remote_transport_rejects_insecure_or_credential_urls(tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        MCPConnectionCreate(name="远程", transport="streamable_http", url="http://example.com/mcp")
    with pytest.raises(ValueError, match="凭据"):
        MCPConnectionCreate(name="远程", transport="streamable_http", url="https://user:secret@example.com/mcp")
    local = MCPConnectionCreate(name="本机", transport="streamable_http", url="http://127.0.0.1:3000/mcp")
    assert local.transport == "streamable_http"


@pytest.mark.asyncio
async def test_mcp_streamable_http_discovers_and_invokes_official_server(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server_script = PROJECT_ROOT / "tests" / "fixtures" / "mcp_http_server.py"
    env = {**os.environ, "AETHERCHAT_TEST_MCP_PORT": str(port)}
    process = subprocess.Popen(
        [sys.executable, str(server_script)], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            pytest.fail("MCP Streamable HTTP test server did not start")

        registry = MCPRegistry(tmp_path, credential_vault=_MemoryVault())
        connection = await registry.create(MCPConnectionCreate(
            id="http-math", name="HTTP Math", transport="streamable_http",
            url=f"http://127.0.0.1:{port}/mcp", auth_type="none",
        ))
        assert connection["status"] == "ready"
        definition = registry.tool_definitions()[0]
        assert definition.name == "mcp__http_math__add"
        result = await registry.invoke_tool(definition.name, {"left": 7, "right": 5})
        assert result["status"] == "success"
        assert result["content"][0]["text"] == "12"
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


@pytest.mark.asyncio
async def test_mcp_oauth_storage_keeps_tokens_in_vault_and_callback_is_unauthed(client, monkeypatch):
    credential_vault = _MemoryVault()
    storage = VaultOAuthStorage(credential_vault, "secure-server")
    tokens = OAuthToken(access_token="access-secret", refresh_token="refresh-secret", token_type="Bearer")
    client_info = OAuthClientInformationFull(
        client_id="client-id", client_secret="client-secret",
        redirect_uris=["http://127.0.0.1:8000/callback"],
    )
    await storage.set_tokens(tokens)
    await storage.set_client_info(client_info)
    assert (await storage.get_tokens()).refresh_token == "refresh-secret"
    assert (await storage.get_client_info()).client_secret == "client-secret"
    assert all("secret" not in ref.lower() for ref in credential_vault.values)
    storage.clear()
    assert credential_vault.values == {}

    monkeypatch.delenv("AETHERCHAT_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.setenv("AETHERCHAT_API_TOKEN", "required-token")
    callback = client.get(
        "/mcp-connections/oauth/callback/missing",
        params={"error": "<script>alert(1)</script>"},
    )
    assert callback.status_code == 400
    assert "<script>" not in callback.text
    assert "&lt;script&gt;" in callback.text
def test_memory_api_allows_review_edit_and_delete(client):
    session_id = str(uuid4())
    updated = client.patch(f"/memory/{session_id}", json={
        "project_note": "本机项目说明",
        "decisions": ["决定使用 SQLite"],
        "preferences": ["优先使用中文"],
        "open_items": ["下一步补测试"],
    })
    assert updated.status_code == 200
    assert updated.json()["decisions"] == ["决定使用 SQLite"]
    restored = client.get(f"/memory/{session_id}")
    assert restored.json()["project_note"] == "本机项目说明"
    cleared = client.patch(f"/memory/{session_id}", json={
        "project_note": "", "decisions": [], "preferences": [], "open_items": [],
    })
    assert cleared.status_code == 200
    assert cleared.json()["preferences"] == []


def test_project_image_is_sanitized_and_available_for_multimodal_context(client):
    from PIL import Image

    session_id = str(uuid4())
    source = io.BytesIO()
    Image.new("RGB", (32, 24), (120, 80, 40)).save(source, format="PNG", pnginfo=None)
    payload = source.getvalue() + b"untrusted-trailing-payload"
    uploaded = client.post(
        f"/knowledge/{session_id}/attachments",
        files={"file": ("reference.png", payload, "image/png")},
    )
    assert uploaded.status_code == 201
    attachment = uploaded.json()
    assert attachment["chunk_count"] == 0
    urls = knowledge_service.image_data_urls(session_id, [attachment["id"]])
    assert len(urls) == 1
    assert urls[0].startswith("data:image/png;base64,")
    native_path = Path(knowledge_service.image_path(session_id, attachment["id"]))
    assert native_path.is_file()
    assert knowledge_service.image_paths(session_id, [attachment["id"]]) == [
        str(native_path)
    ]
    sanitized = __import__("base64").b64decode(urls[0].split(",", 1)[1])
    assert b"untrusted-trailing-payload" not in sanitized
    preview = client.get(f"/knowledge/{session_id}/attachments/{attachment['id']}/preview")
    assert preview.json()["kind"] == "image"
    image_content = client.get(f"/knowledge/{session_id}/attachments/{attachment['id']}/content")
    assert image_content.status_code == 200
    assert image_content.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in image_content.headers["content-security-policy"]
    assert image_content.content == sanitized

    disguised = client.post(
        f"/knowledge/{session_id}/attachments",
        files={"file": ("fake.png", b"not an image", "image/png")},
    )
    assert disguised.status_code == 422


@pytest.mark.asyncio
async def test_approval_service_supports_once_session_deny_and_fail_closed():
    service = ApprovalService()
    pending = service.create(
        session_id="session-a",
        model="claude",
        tool_name="run_command",
        risk="command_execution",
        title="运行命令",
        detail="pytest -q",
        arguments={"cmd": "pytest -q"},
        allow_session=True,
    )
    assert pending is not None
    waiter = asyncio.create_task(service.wait(pending.id, timeout=1))
    assert service.decide(pending.id, "session-a", "allow_session")["resolved"] is True
    assert await waiter == "allow_session"
    assert service.create(
        session_id="session-a", model="codex", tool_name="run_command",
        risk="command_execution", title="运行命令", detail="echo ok", arguments={}, allow_session=True,
    ) is None

    once_only = service.create(
        session_id="session-a", model="claude", tool_name="write_local",
        risk="local_file_write", title="写文件", detail="/tmp/a", arguments={}, allow_session=False,
    )
    assert once_only is not None
    with pytest.raises(PermissionError):
        service.decide(once_only.id, "session-b", "allow_once")
    with pytest.raises(ValueError):
        service.decide(once_only.id, "session-a", "allow_session")
    denied = asyncio.create_task(service.wait(once_only.id, timeout=1))
    result = service.decide(
        once_only.id,
        "session-a",
        "deny",
        "不要写文件，改为只读检查",
    )
    assert result["feedback"] == "不要写文件，改为只读检查"
    assert await denied == "deny"
    assert once_only.feedback == "不要写文件，改为只读检查"


def test_tasks_are_uuid_validated_session_scoped_and_persisted(client, tmp_path, monkeypatch):
    original_tasks = list(task_router.fake_tasks)
    task_file = tmp_path / "tasks.json"
    monkeypatch.setattr(task_router, "TASKS_PATH", task_file)
    task_router.fake_tasks.clear()

    session_a = str(uuid4())
    session_b = str(uuid4())
    try:
        created_a = client.post("/tasks/", json={
            "session_id": session_a,
            "title": "session A task",
            "description": "offline regression",
        })
        created_b = client.post("/tasks/", json={
            "session_id": session_b,
            "title": "session B task",
        })
        assert created_a.status_code == 200
        assert created_b.status_code == 200

        tasks_a = client.get("/tasks/", params={"session_id": session_a}).json()
        tasks_b = client.get("/tasks/", params={"session_id": session_b}).json()
        assert [task["title"] for task in tasks_a] == ["session A task"]
        assert [task["title"] for task in tasks_b] == ["session B task"]

        invalid = client.post("/tasks/", json={"session_id": "not-a-uuid", "title": "bad"})
        assert invalid.status_code == 422

        task_id = created_a.json()["id"]
        claimed = client.post(f"/tasks/{task_id}/claim", params={"by": "user:test"})
        assert claimed.status_code == 200
        assert claimed.json()["status"] == "claimed"

        assert task_file.exists()
        task_router.fake_tasks.clear()
        task_router.fake_tasks.extend(task_router._load_tasks(task_file))
        restored = client.get("/tasks/", params={"session_id": session_a}).json()
        assert restored[0]["id"] == task_id
        assert restored[0]["claimed_by"] == "user:test"
    finally:
        task_router.fake_tasks.clear()
        task_router.fake_tasks.extend(original_tasks)


def test_task_dependencies_block_execution_and_tool_success_enters_review(client, tmp_path, monkeypatch):
    from app.services.tool_executor import execute_tool

    original_tasks = list(task_router.fake_tasks)
    monkeypatch.setattr(task_router, "TASKS_PATH", tmp_path / "dependent-tasks.json")
    task_router.fake_tasks.clear()
    session_id = str(uuid4())
    try:
        parent = client.post("/tasks/", json={
            "session_id": session_id,
            "title": "先生成图片",
        }).json()
        child_response = client.post("/tasks/", json={
            "session_id": session_id,
            "title": "再撰写图片描述",
            "depends_on": [parent["id"]],
        })
        assert child_response.status_code == 200
        child = child_response.json()
        assert child["ready"] is False
        assert child["blocked_by"] == [parent["id"]]
        assert client.post(f"/tasks/{child['id']}/claim").status_code == 409
        listed = asyncio.run(execute_tool(
            "list_tasks",
            {},
            "model:qwen",
            session_id,
        ))
        listed_child = next(
            item for item in listed["result"]["tasks"]
            if item["id"] == child["id"]
        )
        assert listed_child["ready"] is False
        blocked_claim = asyncio.run(execute_tool(
            "claim_task",
            {"task_id": child["id"]},
            "model:qwen",
            session_id,
        ))
        assert blocked_claim["status"] == "error"
        assert "blocked by" in blocked_claim["error"]

        assert client.patch(
            f"/tasks/{parent['id']}",
            json={"status": "done"},
        ).status_code == 200
        refreshed = client.get("/tasks/", params={"session_id": session_id}).json()
        child = next(item for item in refreshed if item["id"] == child["id"])
        assert child["ready"] is True
        assert child["blocked_by"] == []

        run = asyncio.run(execute_tool(
            "write_artifact",
            {
                "path": "descriptions/image.md",
                "content": "图片描述",
                "task_id": child["id"],
            },
            "model:qwen",
            session_id,
        ))
        assert run["status"] == "success"
        assert run["result"]["task_sync"]["status"] == "review"
        task_after_tool = client.get("/tasks/", params={"session_id": session_id}).json()
        child_after_tool = next(item for item in task_after_tool if item["id"] == child["id"])
        assert child_after_tool["status"] == "review"
        assert child_after_tool["claimed_by"] == "model:qwen"

        circular = client.patch(
            f"/tasks/{parent['id']}",
            json={"depends_on": [child["id"]]},
        )
        assert circular.status_code == 422
        assert "循环" in circular.json()["detail"]
    finally:
        task_router.fake_tasks.clear()
        task_router.fake_tasks.extend(original_tasks)


def test_tool_registry_upgrades_builtin_schemas_without_reenabling_tools(tmp_path):
    from app.services.tool_registry import ToolRegistry

    registry = ToolRegistry(tmp_path)
    document = json.loads(registry.registry_path.read_text(encoding="utf-8"))
    write_tool = next(
        item for item in document["tools"]
        if item["name"] == "write_artifact"
    )
    write_tool["input_schema"]["properties"].pop("task_id")
    write_tool["enabled"] = False
    registry.registry_path.write_text(
        json.dumps(document, ensure_ascii=False),
        encoding="utf-8",
    )

    upgraded = ToolRegistry(tmp_path)
    upgraded_write = upgraded.find_by_name("write_artifact")
    upgraded_create = upgraded.find_by_name("create_task")
    assert upgraded_write is not None
    assert upgraded_write["enabled"] is False
    assert "task_id" in upgraded_write["input_schema"]["properties"]
    assert upgraded_create is not None
    assert "depends_on" in upgraded_create["input_schema"]["properties"]


def test_linked_plugin_success_advances_task_to_review(client, tmp_path, monkeypatch):
    from app.services.plugin_registry import PluginInvoke, PluginUpdate

    original_tasks = list(task_router.fake_tasks)
    previous_enabled = plugin_registry.get("comfyui")["enabled"]
    monkeypatch.setattr(task_router, "TASKS_PATH", tmp_path / "plugin-task.json")
    task_router.fake_tasks.clear()
    session_id = str(uuid4())

    async def fake_dispatch(_action, _session_id, arguments):
        return {"prompt_id": "test-prompt", "received_task_id": arguments["task_id"]}

    try:
        plugin_registry.update("comfyui", PluginUpdate(enabled=True))
        monkeypatch.setattr(plugin_registry, "_dispatch", fake_dispatch)
        task = client.post("/tasks/", json={
            "session_id": session_id,
            "title": "生成封面",
        }).json()
        result = asyncio.run(plugin_registry.invoke(
            "comfyui",
            PluginInvoke(
                action="generate_image",
                session_id=session_id,
                arguments={
                    "prompt": "雨夜中的重庆",
                    "model": "qwen-image.safetensors",
                    "task_id": task["id"],
                },
            ),
            caller="model:qwen",
        ))
        assert result["result"]["task_sync"]["status"] == "review"
        refreshed = client.get("/tasks/", params={"session_id": session_id}).json()
        assert refreshed[0]["status"] == "review"
        assert refreshed[0]["claimed_by"] == "model:qwen"
    finally:
        plugin_registry.update("comfyui", PluginUpdate(enabled=previous_enabled))
        task_router.fake_tasks.clear()
        task_router.fake_tasks.extend(original_tasks)


def test_chat_history_and_task_events_survive_database_restart(client):
    session_id = uuid4()
    chat_database.create_session(str(session_id), "重启恢复测试", "project")
    for role, author, content in (
        ("user", "user", "继续上次的数据库任务"),
        ("assistant", "qwen", "已经恢复上下文。"),
    ):
        chat_database.save_message(Message(
            id=uuid4(),
            session_id=session_id,
            author_model=author,
            role=role,
            content=content,
            generated_files=[{
                "path": "generated/report.md",
                "name": "report.md",
                "mime_type": "text/markdown",
                "size_bytes": 128,
            }] if role == "assistant" else [],
            reasoning_summary=[{
                "phase": "analysis",
                "detail": "已整理上下文。",
            }] if role == "assistant" else [],
            created_at=datetime.now(UTC),
        ))

    restarted = ChatDatabase(chat_database.path)
    assert [row["content"] for row in restarted.list_messages(str(session_id))] == [
        "继续上次的数据库任务",
        "已经恢复上下文。",
    ]

    history = client.get(f"/sessions/{session_id}/messages")
    assert history.status_code == 200
    assert history.json()[1]["author_model"] == "qwen"
    assert history.json()[1]["generated_files"][0]["path"] == "generated/report.md"
    assert history.json()[1]["reasoning_summary"] == [{
        "phase": "analysis",
        "detail": "已整理上下文。",
    }]
    sessions = client.get("/sessions/").json()
    restored_session = next(item for item in sessions if item["id"] == str(session_id))
    assert restored_session["message_count"] == 2
    assert restored_session["summary"] == "已经恢复上下文。"

    created = client.post("/tasks/", json={"session_id": str(session_id), "title": "记录恢复进度"})
    assert created.status_code == 200
    patched = client.patch(f"/tasks/{created.json()['id']}", json={"status": "in_progress"})
    assert patched.status_code == 200
    events = client.get(f"/sessions/{session_id}/task-events").json()
    assert [event["action"] for event in events[:2]] == ["updated", "created"]


def test_cached_chat_search_native_file_location_and_session_delete(client):
    session_id = str(uuid4())
    created = client.post("/sessions/", json={
        "id": session_id,
        "title": "缓存检索测试",
        "mode": "casual",
    })
    assert created.status_code == 200

    written = client.post("/artifacts/write", json={
        "session_id": session_id,
        "path": "cache/result.txt",
        "content": "persistent generated output",
        "commit_message": "cache generated output",
        "author": "test",
    })
    assert written.status_code == 200
    native = client.get(
        "/artifacts/cache/result.txt/native-location",
        params={"session_id": session_id},
    )
    assert native.status_code == 200
    native_path = Path(native.json()["path"])
    assert native_path.is_file()
    content = client.get(
        "/artifacts/cache/result.txt/content",
        params={"session_id": session_id},
    )
    assert content.status_code == 200
    assert content.content == b"persistent generated output"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.headers["content-disposition"].startswith("inline;")

    message = Message(
        id=uuid4(),
        session_id=session_id,
        author_model="qwen",
        role="assistant",
        content="跨会话缓存检索唯一词：山城夜雨。",
        generated_files=[{
            "path": "cache/result.txt",
            "name": "result.txt",
            "mime_type": "text/plain",
            "size_bytes": native_path.stat().st_size,
            "kind": "generated_file",
            "source": "qwen",
        }],
        created_at=datetime.now(UTC),
    )
    chat_database.save_message(message, mode="casual")
    uploaded = client.post(
        f"/knowledge/{session_id}/attachments",
        files={
            "file": (
                "cached-note.txt",
                "用于验证删除会话时清理资料。".encode(),
                "text/plain",
            )
        },
    )
    assert uploaded.status_code == 201
    task = client.post("/tasks/", json={
        "session_id": session_id,
        "title": "删除时清理",
    })
    assert task.status_code == 200

    search = client.get("/sessions/search", params={
        "q": "缓存检索唯一词",
        "limit": 10,
    })
    assert search.status_code == 200
    assert search.json()[0]["session_id"] == session_id
    assert "山城夜雨" in search.json()[0]["snippet"]

    deleted = client.delete(f"/sessions/{session_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["cleanup"]["knowledge"]["attachments"] == 1
    assert not native_path.exists()
    assert chat_database.get_session(session_id) is None
    assert all(
        str(item.session_id) != session_id
        for item in task_router.fake_tasks
    )
    assert client.get(
        "/sessions/search",
        params={"q": "缓存检索唯一词"},
    ).json() == []
    assert client.delete(f"/sessions/{session_id}").status_code == 404


def test_deleted_seed_model_stays_deleted_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(tmp_path))
    first = ModelRegistry()
    assert first.get("grok") is not None
    assert first.delete("grok") is not None
    assert first.get("grok") is None

    restarted = ModelRegistry()
    assert restarted.get("grok") is None
    assert restarted.get("gpt") is not None


def test_linux_vault_uses_keyring_and_migrates_plaintext_index(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("Linux/macOS keyring behavior")

    class FakeKeyring:
        values: dict[tuple[str, str], str] = {}

        @classmethod
        def set_password(cls, service, ref, value):
            cls.values[(service, ref)] = value

        @classmethod
        def get_password(cls, service, ref):
            return cls.values.get((service, ref))

    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(vault_module, "system_keyring", FakeKeyring)
    vault_path = tmp_path / "vault.json"
    vault_path.write_text('{"LEGACY_KEY": "legacy-test-value"}', encoding="utf-8")

    secure_vault = vault_module.SimpleVault()
    assert secure_vault.get_key("LEGACY_KEY") == "legacy-test-value"
    secure_vault.set_key("NEW_KEY", "new-test-value")
    index = json.loads(vault_path.read_text(encoding="utf-8"))
    assert index == {"LEGACY_KEY": "keyring", "NEW_KEY": "keyring"}
    assert "legacy-test-value" not in vault_path.read_text(encoding="utf-8")
    assert "new-test-value" not in vault_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_sandbox_uses_prepared_workspace_cwd_and_real_exit_code(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    captured = {}

    class FakeContainer:
        removed = False

        def wait(self, timeout):
            captured["wait_timeout"] = timeout
            return {"StatusCode": 7}

        def logs(self, **kwargs):
            return b"command output"

        def remove(self, force=False):
            self.removed = force

    container = FakeContainer()

    class FakeContainers:
        def run(self, **kwargs):
            captured["run"] = kwargs
            return container

    class FakeClient:
        containers = FakeContainers()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(sandbox_executor.docker, "from_env", lambda: FakeClient())
    result = await sandbox_executor.execute_in_sandbox(
        "python -m pytest",
        cwd="src",
        timeout=12,
        workspace_path=workspace,
    )

    assert result["exit_code"] == 7
    assert result["stdout"] == "command output"
    assert captured["run"]["working_dir"] == "/workspace/src"
    assert captured["run"]["volumes"][str(workspace.resolve())]["mode"] == "rw"
    assert captured["run"]["network_mode"] == "none"
    assert container.removed is True
    assert captured["closed"] is True


def test_sandbox_rejects_path_escape_and_unbounded_inputs():
    with pytest.raises(ValueError, match="inside /workspace"):
        sandbox_executor.normalize_container_cwd("../outside")
    with pytest.raises(ValueError, match="timeout"):
        sandbox_executor.validate_command("echo ok", 301)
    with pytest.raises(ValueError, match="too large"):
        sandbox_executor.validate_command("x" * 20_001, 30)


@pytest.mark.asyncio
async def test_provider_context_uses_model_limit_and_preserves_latest_turn():
    messages = []
    for index in range(12):
        messages.extend([
            ChatMessage(role="user", content=f"old-user-{index} " + ("甲" * 900)),
            ChatMessage(role="assistant", content=f"old-answer-{index} " + ("乙" * 900)),
        ])
    messages.append(ChatMessage(role="user", content="LATEST-REQUEST must remain"))

    result = await build_messages_for_provider(messages, provider_limit=4096, output_reserve=1024)
    assert result[-1].content == "LATEST-REQUEST must remain"
    assert any("上下文上限" in item.content for item in result if item.role == "system")
    assert sum(estimate_tokens(item.content) + 8 for item in result) <= 4096 - 1024
    first_conversation = next(item for item in result if item.role != "system")
    assert first_conversation.role == "user"


@pytest.mark.asyncio
async def test_provider_context_drops_complete_native_tool_turns():
    messages = [
        ChatMessage(role="user", content="old request " + ("甲" * 6000)),
        ChatMessage(
            role="assistant",
            tool_calls=[ToolCall(
                id="old-call",
                name="inspect",
                arguments={"target": "old"},
            )],
        ),
        ChatMessage(
            role="tool",
            name="inspect",
            tool_call_id="old-call",
            content='{"result": "old"}',
        ),
        ChatMessage(role="user", content="LATEST-REQUEST"),
    ]

    result = await build_messages_for_provider(
        messages,
        provider_limit=2048,
        output_reserve=512,
    )
    conversation = [message for message in result if message.role != "system"]

    assert [(message.role, message.content) for message in conversation] == [
        ("user", "LATEST-REQUEST"),
    ]


@pytest.mark.asyncio
async def test_provider_context_caps_recent_messages_at_a_user_boundary():
    messages = []
    for index in range(10):
        messages.extend([
            ChatMessage(role="user", content=f"user-{index}"),
            ChatMessage(role="assistant", content=f"answer-{index}"),
        ])
    messages.append(ChatMessage(role="user", content="LATEST-REQUEST"))

    result = await build_messages_for_provider(
        messages,
        provider_limit=32_768,
        output_reserve=2048,
        max_conversation_messages=8,
    )
    conversation = [message for message in result if message.role != "system"]

    assert len(conversation) <= 8
    assert conversation[0].role == "user"
    assert conversation[-1].content == "LATEST-REQUEST"
    assert all(message.content != "user-0" for message in conversation)
    assert any(
        "上下文上限" in message.content
        for message in result
        if message.role == "system"
    )


@pytest.mark.asyncio
async def test_provider_context_keeps_latest_multi_model_turn_together():
    messages = [
        ChatMessage(role="user", content=f"old-user-{index}")
        if index % 2 == 0
        else ChatMessage(role="assistant", content=f"old-answer-{index}")
        for index in range(20)
    ]
    messages.append(ChatMessage(role="user", content="LATEST-REQUEST"))
    messages.extend(
        ChatMessage(role="assistant", content=f"model-answer-{index}")
        for index in range(10)
    )

    result = await build_messages_for_provider(
        messages,
        provider_limit=32_768,
        output_reserve=2048,
        max_conversation_messages=8,
    )
    conversation = [message for message in result if message.role != "system"]

    assert conversation[0].content == "LATEST-REQUEST"
    assert len(conversation) == 11
    assert conversation[-1].content == "model-answer-9"



def test_workspace_artifact_merge_tool_and_export_flow(client):
    session_id = str(uuid4())
    other_session_id = str(uuid4())

    seeded = client.get("/artifacts/", params={"session_id": session_id})
    assert seeded.status_code == 200
    seeded_artifacts = {item["path"]: item for item in seeded.json()}
    assert set(seeded_artifacts) >= {"README.md", "docs/PRD.md", "src/demo.ts"}
    assert seeded_artifacts["src/demo.ts"]["mime_type"] == "text/typescript"

    for content in ("version one", "version two"):
        written = client.post("/artifacts/write", json={
            "session_id": session_id,
            "path": "notes/flow.txt",
            "content": content,
            "commit_message": content,
            "author": "test",
        })
        assert written.status_code == 200

    history = client.get("/artifacts/notes/flow.txt/history", params={"session_id": session_id})
    assert history.status_code == 200
    assert [item["content"] for item in history.json()[:2]] == ["version two", "version one"]
    invalid_path = client.post("/artifacts/write", json={
        "session_id": session_id,
        "path": "../outside.txt",
        "content": "blocked",
    })
    assert invalid_path.status_code == 400

    proposal = client.post("/artifacts/merge/proposals", json={
        "session_id": session_id,
        "artifact_path": "notes/flow.txt",
        "proposer": "user",
        "rationale": "offline flow",
        "merge_rule": "两票通过",
    })
    assert proposal.status_code == 200
    proposal_id = proposal.json()["id"]
    assert proposal.json()["merge_rule"] == "two_votes"
    first_vote = client.post(f"/artifacts/merge/proposals/{proposal_id}/vote", json={
        "voter": "model:a",
        "approve": True,
        "session_id": session_id,
    })
    assert first_vote.json()["status"] == "open"
    second_vote = client.post(f"/artifacts/merge/proposals/{proposal_id}/vote", json={
        "voter": "model:b",
        "approve": True,
        "session_id": session_id,
    })
    assert second_vote.json()["status"] == "merged"
    late_vote = client.post(f"/artifacts/merge/proposals/{proposal_id}/vote", json={
        "voter": "model:c",
        "approve": False,
        "session_id": session_id,
    })
    assert late_vote.json()["status"] == "merged"
    assert late_vote.json()["vote_accepted"] is False

    tools = client.get("/tools/", params={"session_id": session_id}).json()
    by_name = {tool["name"]: tool for tool in tools}
    assert {"list_tasks", "create_task", "read_artifact", "write_artifact"} <= set(by_name)

    task_run = client.post(f"/tools/{by_name['create_task']['id']}/invoke", json={
        "caller": "user",
        "session_id": session_id,
        "arguments": {"title": "created through dispatcher", "priority": 1},
    })
    assert task_run.status_code == 200
    assert task_run.json()["status"] == "success", task_run.json().get("error")
    assert isinstance(task_run.json()["output"], str)
    assert task_run.json()["result"]["task"]["title"] == "created through dispatcher"

    tool_write = client.post(f"/tools/{by_name['write_artifact']['id']}/invoke", json={
        "caller": "user",
        "session_id": session_id,
        "arguments": {"path": "notes/tool.txt", "content": "written by dispatcher"},
    })
    assert tool_write.status_code == 200
    assert tool_write.json()["status"] == "success"
    read_back = client.get("/artifacts/notes/tool.txt", params={"session_id": session_id})
    assert read_back.json()["content"] == "written by dispatcher"

    session_runs = client.get("/tools/runs", params={"session_id": session_id}).json()
    other_runs = client.get("/tools/runs", params={"session_id": other_session_id}).json()
    assert len(session_runs) >= 2
    assert other_runs == []

    exported = client.get(f"/export/session/{session_id}", params={"format": "json"})
    assert exported.status_code == 200
    snapshot = exported.json()
    assert any(task["title"] == "created through dispatcher" for task in snapshot["tasks"])
    assert any(item["path"] == "notes/tool.txt" and item["content"] == "written by dispatcher" for item in snapshot["artifacts"])
    assert snapshot["merge_proposals"][0]["status"] == "merged"
    assert len(snapshot["tool_runs"]) >= 2

    markdown = client.get(f"/export/session/{session_id}", params={"format": "markdown"})
    assert markdown.status_code == 200
    assert "## Artifacts (content and complete history)" in markdown.text
    assert "written by dispatcher" in markdown.text


def test_interactive_artifact_preview_versions_office_exports_and_rejections(client):
    from docx import Document
    from openpyxl import load_workbook
    from pptx import Presentation

    session_id = str(uuid4())
    other_session_id = str(uuid4())
    markdown = "# Release brief\n\n| Owner | Status |\n| --- | --- |\n| Team | Ready |\n\n---\n# Next slide\n\n- Safe export\n:::notes\nSpeaker-only context"
    first = client.post("/artifacts/write", json={
        "session_id": session_id,
        "path": "reports/brief.md",
        "content": "# Draft\n",
        "commit_message": "draft",
        "author": "test",
        "source_model": "offline-model",
        "status": "draft",
    })
    assert first.status_code == 200
    second = client.post("/artifacts/write", json={
        "session_id": session_id,
        "path": "reports/brief.md",
        "content": markdown,
        "commit_message": "ready",
        "author": "test",
        "source_model": "offline-model",
        "status": "ready",
        "parent_version": 1,
    })
    assert second.status_code == 200

    listed = client.get("/artifacts/", params={"session_id": session_id}).json()
    artifact = next(item for item in listed if item["path"] == "reports/brief.md")
    assert artifact["mime_type"] == "text/markdown"
    assert artifact["current_version"] == 2
    assert artifact["source_model"] == "offline-model"
    assert artifact["parent_version"] == 1

    preview = client.get("/artifacts/reports/brief.md/preview", params={"session_id": session_id})
    assert preview.status_code == 200
    assert preview.json()["mode"] == "markdown"
    assert preview.json()["content"] == markdown
    assert client.get(
        "/artifacts/reports/brief.md/preview", params={"session_id": other_session_id}
    ).status_code == 404

    hostile = '<script>fetch("https://attacker.invalid")</script><img src="file:///etc/passwd"><h1>isolated</h1>'
    assert client.post("/artifacts/write", json={
        "session_id": session_id, "path": "preview/hostile.html", "content": hostile,
    }).status_code == 200
    hostile_preview = client.get(
        "/artifacts/preview/hostile.html/preview", params={"session_id": session_id}
    ).json()
    assert hostile_preview["mode"] == "isolated_html"
    assert hostile_preview["sandbox"] == "allow-none"
    assert "attacker.invalid" not in hostile_preview["content"]
    assert "file:///etc/passwd" not in hostile_preview["content"]

    diff = client.get("/artifacts/reports/brief.md/diff", params={
        "session_id": session_id, "from_version": 1, "to_version": 2,
    })
    assert diff.status_code == 200
    assert "-# Draft" in diff.json()["diff"]
    assert "+# Release brief" in diff.json()["diff"]
    restored = client.post("/artifacts/reports/brief.md/restore", json={
        "session_id": session_id, "version": 1, "author": "test",
    })
    assert restored.status_code == 200
    assert restored.json()["version"] == 3
    assert restored.json()["parent_version"] == 1
    assert client.get("/artifacts/reports/brief.md", params={"session_id": session_id}).json()["content"] == "# Draft\n"
    assert client.post("/artifacts/reports/brief.md/restore", json={
        "session_id": other_session_id, "version": 1,
    }).status_code == 400

    # Restore the rich version before verifying all three Office writers.
    assert client.post("/artifacts/reports/brief.md/restore", json={
        "session_id": session_id, "version": 2, "author": "test",
    }).status_code == 200
    docx_response = client.get("/artifacts/reports/brief.md/export", params={
        "session_id": session_id, "format": "docx",
    })
    assert docx_response.status_code == 200
    document = Document(io.BytesIO(docx_response.content))
    assert any(paragraph.text == "Release brief" for paragraph in document.paragraphs)
    assert document.tables[0].cell(1, 0).text == "Team"

    pptx_response = client.get("/artifacts/reports/brief.md/export", params={
        "session_id": session_id, "format": "pptx",
    })
    assert pptx_response.status_code == 200
    presentation = Presentation(io.BytesIO(pptx_response.content))
    assert len(presentation.slides) == 2
    assert presentation.slides[0].shapes.title.text == "Release brief"
    assert "Speaker-only context" in presentation.slides[1].notes_slide.notes_text_frame.text

    workbook_payload = json.dumps({"sheets": [{
        "name": "安全报表", "rows": [["name", "value"], ["formula", "=HYPERLINK(\"https://attacker.invalid\")"]],
    }]}, ensure_ascii=False)
    assert client.post("/artifacts/write", json={
        "session_id": session_id, "path": "reports/data.json", "content": workbook_payload,
    }).status_code == 200
    xlsx_response = client.get("/artifacts/reports/data.json/export", params={
        "session_id": session_id, "format": "xlsx",
    })
    assert xlsx_response.status_code == 200
    workbook = load_workbook(io.BytesIO(xlsx_response.content), data_only=False)
    worksheet = workbook["安全报表"]
    assert worksheet.freeze_panes == "A2"
    assert worksheet["B2"].value.startswith("'=")
    workbook.close()

    assert client.post("/artifacts/write", json={
        "session_id": session_id, "path": "../escape.svg", "content": "<svg/>",
    }).status_code == 400
    too_large = client.post("/artifacts/write", json={
        "session_id": session_id, "path": "too-large.txt", "content": "x" * (5 * 1024 * 1024 + 1),
    })
    assert too_large.status_code == 400
    assert client.get("/artifacts/reports/brief.md/export", params={
        "session_id": session_id, "format": "exe",
    }).status_code == 400



def test_automation_api_crud_manual_run_and_review(client, monkeypatch):
    session_id = str(uuid4())

    async def fake_background_executor(job):
        assert job["permission_snapshot"]["mode"] == "read_only"
        assert job["permission_snapshot"]["allowed_tools"] == []
        return AutomationExecutionResult(
            status="completed",
            summary="后台摘要已生成",
            budget_spent_usd=0.0123,
        )

    monkeypatch.setattr(automation_service, "_executor", fake_background_executor)
    first_run = datetime.now(UTC) + timedelta(hours=2)
    payload = {
        "session_id": session_id,
        "name": "每小时只读摘要",
        "prompt": "只读取当前会话并生成摘要，不修改任何文件。",
        "mode": "project",
        "targets": ["all"],
        "trigger_type": "interval",
        "interval_minutes": 60,
        "timezone": "Asia/Chongqing",
        "next_run_at": first_run.isoformat(),
        "enabled": True,
        "max_budget_usd": 0.5,
        "permission_mode": "read_only",
    }
    created = client.post("/automations/schedules", json=payload)
    assert created.status_code == 201
    schedule = created.json()
    assert schedule["permission_snapshot"]["allowed_tools"] == []
    assert schedule["next_run_at"] == first_run.isoformat()

    rejected = client.post(
        "/automations/schedules",
        json={**payload, "name": "不安全任务", "permission_mode": "full_access"},
    )
    assert rejected.status_code == 422

    listed = client.get(
        "/automations/schedules",
        params={"session_id": session_id},
    )
    assert [item["id"] for item in listed.json()] == [schedule["id"]]

    paused = client.patch(
        f"/automations/schedules/{schedule['id']}",
        json={"session_id": session_id, "enabled": False},
    )
    assert paused.status_code == 200
    assert paused.json()["enabled"] is False

    queued = client.post(
        f"/automations/schedules/{schedule['id']}/run",
        json={"session_id": session_id},
    )
    assert queued.status_code == 202
    job_id = queued.json()["id"]

    completed_job = None
    for _ in range(100):
        completed_job = automation_service.database.get_background_job(
            job_id,
            session_id=session_id,
        )
        if completed_job["status"] == "completed":
            break
        time.sleep(0.02)
    assert completed_job is not None
    assert completed_job["status"] == "completed"
    assert completed_job["summary"] == "后台摘要已生成"
    assert completed_job["budget_spent_usd"] == pytest.approx(0.0123)
    assert completed_job["reviewed_at"] is None
    listed_jobs = client.get(
        "/automations/jobs",
        params={"session_id": session_id},
    )
    assert listed_jobs.status_code == 200
    assert listed_jobs.json()[0]["id"] == job_id

    reviewed = client.post(
        f"/automations/jobs/{job_id}/review",
        json={"session_id": session_id},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["reviewed_at"]
    assert client.delete(
        f"/automations/schedules/{schedule['id']}",
        params={"session_id": session_id},
    ).json()["deleted"] is True


@pytest.mark.asyncio
async def test_automation_scheduler_materializes_once_and_reclaims_expired_lease(tmp_path):
    database = ChatDatabase(tmp_path / "automations.sqlite3")
    dispatcher = RunDispatcher(capacity=1)
    session_id = str(uuid4())
    now = datetime.now(UTC).replace(microsecond=0)
    calls = []

    async def fake_executor(job):
        calls.append(job["id"])
        await asyncio.sleep(0)
        return AutomationExecutionResult(status="completed", summary="ok")

    schedule = database.create_automation_schedule(
        session_id=session_id,
        name="可恢复循环任务",
        prompt="生成只读摘要",
        mode="project",
        targets=["all"],
        trigger_type="interval",
        interval_minutes=30,
        timezone="UTC",
        next_run_at=(now - timedelta(minutes=95)).isoformat(),
        enabled=True,
        max_budget_usd=1.0,
        permission_snapshot={"policy_version": 1, "mode": "read_only", "allowed_tools": []},
    )
    service = AutomationService(
        database,
        AgentRunService(database),
        poll_interval=0.01,
        lease_seconds=6,
        concurrency=1,
        executor=fake_executor,
        dispatcher=dispatcher,
        resource_resolver=lambda _action: (),
    )
    materialized = await service.run_cycle(now)
    assert len(materialized) == 1
    await asyncio.gather(*list(service._active_tasks.values()))
    await service.run_cycle(now)
    first_jobs = database.list_background_jobs(session_id)
    assert len(first_jobs) == 1
    assert first_jobs[0]["status"] == "completed"
    assert first_jobs[0]["permission_snapshot"]["mode"] == "read_only"
    advanced = database.get_automation_schedule(schedule["id"], session_id=session_id)
    assert advanced is not None
    assert datetime.fromisoformat(advanced["next_run_at"]) > now

    stranded = database.create_background_job(
        schedule_id=None,
        session_id=session_id,
        trigger_type="manual",
        action={"content": "恢复我", "targets": ["all"], "mode": "project"},
        permission_snapshot={"mode": "read_only", "allowed_tools": []},
        max_budget_usd=1.0,
        scheduled_for=now.isoformat(),
    )
    claimed = database.claim_background_job(
        worker_id="dead-worker",
        now=now.isoformat(),
        lease_expires_at=(now - timedelta(seconds=1)).isoformat(),
    )
    assert claimed["id"] == stranded["id"]
    assert claimed["attempt"] == 1

    await service.run_cycle(now)
    await asyncio.gather(*list(service._active_tasks.values()))
    recovered = database.get_background_job(stranded["id"])
    assert recovered is not None
    assert recovered["status"] == "completed"
    assert recovered["attempt"] == 2
    assert calls.count(stranded["id"]) == 1
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_background_run_rejects_unapproved_side_effect_before_execution(tmp_path):
    database = ChatDatabase(tmp_path / "background-read-only.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "分析项目，并尝试写入一个文件",
        "targets": ["solo"],
        "mode": "casual",
        "_background_job_id": str(uuid4()),
        "_background_allowed_tools": [],
        "_background_read_only": True,
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class BackgroundProvider(_FakeProvider):
        def __init__(self):
            super().__init__("solo")
            self.calls = 0

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            assert "write_artifact" not in {tool.name for tool in (tools or [])}
            assert any(
                "无人值守执行" in message.content
                for message in messages
                if message.role == "system"
            )
            if self.calls == 1:
                yield {
                    "delta": "",
                    "usage": None,
                    "tool_calls": [{
                        "id": "blocked-background-write",
                        "name": "write_artifact",
                        "arguments": {
                            "path": "must-not-exist.md",
                            "content": "forbidden",
                        },
                    }],
                }
                return
            observation = next(
                message for message in reversed(messages) if message.role == "tool"
            )
            assert observation.tool_error is True
            assert "后台权限快照不允许" in observation.content
            yield {"delta": "已完成只读分析，未修改文件。", "usage": None}

    orchestrator = SessionOrchestrator(session_id, mode="casual")
    orchestrator.providers = {"solo": BackgroundProvider()}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])
    tool_runs_before = len(tool_registry.list_runs(session_id=session_id, limit=None))
    async for event in orchestrator.stream_user_message(action):
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "completed_with_warnings"
    assert len(tool_registry.list_runs(session_id=session_id, limit=None)) == tool_runs_before
    calls = database.list_agent_tool_calls(run["id"])
    assert len(calls) == 1
    assert calls[0]["status"] == "denied"
    assert calls[0]["result"]["status"] == "denied"
