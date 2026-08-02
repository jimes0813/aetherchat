import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
os.environ.setdefault(
    "AETHERCHAT_DATA_DIR",
    tempfile.mkdtemp(prefix="aetherchat-audit-mcp-tests-"),
)

from app.models.schemas import ToolDefinition  # noqa: E402
from app.services.mcp_registry import MCPConnectionCreate, MCPRegistry  # noqa: E402
from app.services.orchestrator import SessionOrchestrator  # noqa: E402
from mcp_servers.aetherchat_local_audit import (  # noqa: E402
    _get_run_evidence,
    _list_recent_runs,
    configure,
)


class MemoryVault:
    def get_key(self, _ref):
        return None

    def set_key(self, _ref, _value):
        return None

    def delete_key(self, _ref):
        return None


def create_audit_database(root: Path) -> tuple[str, str]:
    root.mkdir(parents=True, exist_ok=True)
    run_with_receipt = "11111111-1111-4111-8111-111111111111"
    run_without_tools = "22222222-2222-4222-8222-222222222222"
    database = root / "aetherchat.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE agent_runs (
                id TEXT PRIMARY KEY, session_id TEXT, status TEXT, mode TEXT,
                input_json TEXT, targets_json TEXT, retry_count INTEGER,
                error TEXT, created_at TEXT, updated_at TEXT,
                started_at TEXT, completed_at TEXT
            );
            CREATE TABLE agent_tool_calls (
                id TEXT PRIMARY KEY, run_id TEXT, step_id TEXT,
                plan_step_id TEXT, session_id TEXT, model TEXT,
                provider_type TEXT, provider_model TEXT,
                provider_call_id TEXT, tool_name TEXT, arguments_json TEXT,
                arguments_sha256 TEXT, idempotency_key TEXT,
                side_effect INTEGER, status TEXT, attempt INTEGER,
                round_index INTEGER, call_index INTEGER, tool_run_id TEXT,
                result_json TEXT, result_sha256 TEXT, error TEXT,
                created_at TEXT, updated_at TEXT, completed_at TEXT
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO agent_runs (
                id, session_id, status, mode, input_json, targets_json,
                retry_count, error, created_at, updated_at, started_at, completed_at
            ) VALUES (?, 'session-1', 'completed', 'casual', '{}', '["Qwen"]',
                      0, NULL, ?, ?, ?, ?)
            """,
            [
                (run_with_receipt, "2026-08-02T01:00:00Z", "2026-08-02T01:00:02Z", "2026-08-02T01:00:00Z", "2026-08-02T01:00:02Z"),
                (run_without_tools, "2026-08-02T02:00:00Z", "2026-08-02T02:00:02Z", "2026-08-02T02:00:00Z", "2026-08-02T02:00:02Z"),
            ],
        )
        result = {
            "status": "success",
            "result": {
                "execution_receipt": {
                    "success": True,
                    "verification": "launcher_accepted",
                    "launcher": "system_default",
                    "browser": "default",
                    "url": "https://example.com",
                    "accepted_at": "2026-08-02T01:00:01Z",
                }
            },
        }
        connection.execute(
            """
            INSERT INTO agent_tool_calls (
                id, run_id, session_id, model, provider_type, provider_model,
                provider_call_id, tool_name, arguments_json, arguments_sha256,
                idempotency_key, side_effect, status, attempt, round_index,
                call_index, tool_run_id, result_json, result_sha256, error,
                created_at, updated_at, completed_at
            ) VALUES (
                'call-1', ?, 'session-1', 'Qwen', 'local', 'qwen.gguf',
                'provider-call-1', 'plugin__browser_links__open_url', ?, 'args-hash',
                'idem-1', 1, 'completed', 1, 0, 0, 'tool-run-1', ?, 'result-hash',
                NULL, '2026-08-02T01:00:01Z', '2026-08-02T01:00:02Z',
                '2026-08-02T01:00:02Z'
            )
            """,
            (
                run_with_receipt,
                json.dumps({"url": "https://example.com", "password": "hidden"}),
                json.dumps(result),
            ),
        )
    return run_with_receipt, run_without_tools


def test_audit_helpers_distinguish_receipts_from_model_only_runs(tmp_path):
    run_with_receipt, run_without_tools = create_audit_database(tmp_path)
    configure(tmp_path)

    recent = _list_recent_runs(limit=10)
    by_id = {item["run_id"]: item for item in recent["runs"]}
    assert by_id[run_with_receipt]["tool_call_count"] == 1
    assert by_id[run_without_tools]["has_execution_evidence"] is False

    evidenced = _get_run_evidence(run_with_receipt)
    assert evidenced["summary"]["verified_effect_count"] == 1
    assert evidenced["tool_calls"][0]["effect_evidence"][0]["verification"] == "launcher_accepted"
    assert "password" in evidenced["tool_calls"][0]["arguments"]["provided_keys"]
    assert "password" not in evidenced["tool_calls"][0]["arguments"]

    unsupported = _get_run_evidence(run_without_tools)
    assert unsupported["summary"]["tool_call_count"] == 0
    assert "不能视为真实执行" in unsupported["conclusion"]


@pytest.mark.asyncio
async def test_audit_server_handshakes_and_invokes_through_aetherchat_registry(tmp_path):
    run_with_receipt, _ = create_audit_database(tmp_path / "data")
    registry = MCPRegistry(tmp_path / "registry", credential_vault=MemoryVault())
    script = PROJECT_ROOT / "mcp_servers" / "aetherchat_local_audit.py"
    connection = await registry.create(MCPConnectionCreate(
        id="aetherchat-local-audit",
        name="AetherChat 本地审计",
        transport="stdio",
        command=sys.executable,
        args=[str(script), "--data-dir", str(tmp_path / "data")],
        auth_type="none",
        enabled=True,
        allowed_tools=["get_runtime_health", "list_recent_runs", "get_run_evidence"],
    ))
    assert connection["status"] == "ready"
    assert {item["name"] for item in connection["tools"]} == {
        "get_runtime_health", "list_recent_runs", "get_run_evidence",
    }

    result = await registry.invoke_tool(
        "mcp__aetherchat_local_audit__get_run_evidence",
        {"run_id": run_with_receipt},
    )
    assert result["status"] == "success"
    assert any(run_with_receipt in item.get("text", "") for item in result["content"])


def test_orchestrator_routes_local_audit_mcp_for_evidence_requests():
    audit_tool = ToolDefinition(
        name="mcp__aetherchat_local_audit__get_run_evidence",
        description="read-only evidence",
        parameters={"type": "object", "properties": {}},
    )
    unrelated_mcp = ToolDefinition(
        name="mcp__other__echo",
        description="echo",
        parameters={"type": "object", "properties": {}},
    )

    routed = SessionOrchestrator._route_tools("检查这次是否有真实操作", [audit_tool, unrelated_mcp])
    assert [tool.name for tool in routed] == [audit_tool.name]

    all_mcp = SessionOrchestrator._route_tools("列出 MCP 工具", [audit_tool, unrelated_mcp])
    assert {tool.name for tool in all_mcp} == {audit_tool.name, unrelated_mcp.name}
