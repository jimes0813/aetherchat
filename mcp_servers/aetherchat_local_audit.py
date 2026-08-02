from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from contextlib import closing, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from io import TextIOWrapper
from pathlib import Path
from typing import Any, Iterator
from urllib.request import ProxyHandler, Request, build_opener

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server


@dataclass(frozen=True)
class AuditSettings:
    data_dir: Path
    backend_url: str
    model_url: str


def _default_data_dir() -> Path:
    candidate = Path.home() / ".config" / "aetherchat-frontend" / "backend-data"
    return candidate.resolve()


_settings = AuditSettings(
    data_dir=_default_data_dir(),
    backend_url="http://127.0.0.1:8000",
    model_url="http://127.0.0.1:8080",
)

server = FastMCP("AetherChat Local Audit", json_response=True)
_RUN_ID = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
_SAFE_ARGUMENT_KEYS = {
    "action",
    "artifact_path",
    "browser",
    "destination_path",
    "format",
    "name",
    "path",
    "session_id",
    "task_id",
    "url",
}


def configure(
    data_dir: str | Path,
    *,
    backend_url: str = "http://127.0.0.1:8000",
    model_url: str = "http://127.0.0.1:8080",
) -> None:
    """Configure filesystem and loopback endpoints without creating files."""
    global _settings
    _settings = AuditSettings(
        data_dir=Path(data_dir).expanduser().resolve(),
        backend_url=backend_url.rstrip("/"),
        model_url=model_url.rstrip("/"),
    )


def _database_path() -> Path:
    return _settings.data_dir / "aetherchat.sqlite3"


def _connect_read_only() -> sqlite3.Connection:
    database = _database_path()
    if not database.is_file():
        raise RuntimeError(f"AetherChat 数据库不存在：{database}")
    connection = sqlite3.connect(
        f"{database.as_uri()}?mode=ro",
        uri=True,
        timeout=5,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _json_value(value: Any, fallback: Any) -> Any:
    if not isinstance(value, str) or not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _walk_mappings(value: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    if depth > 8:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_mappings(child, depth + 1)
    elif isinstance(value, list):
        for child in value[:100]:
            yield from _walk_mappings(child, depth + 1)


def _safe_arguments(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"provided_keys": []}
    summary: dict[str, Any] = {"provided_keys": sorted(str(key) for key in value)[:100]}
    for key in _SAFE_ARGUMENT_KEYS:
        if key not in value:
            continue
        item = value[key]
        if isinstance(item, (str, int, float, bool)) or item is None:
            summary[key] = item[:500] if isinstance(item, str) else item
    return summary


def _effect_evidence(result: Any) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for item in _walk_mappings(result):
        if (
            item.get("success") is True
            and item.get("verification") == "launcher_accepted"
        ):
            evidence.append({
                "kind": "external_navigation",
                "verification": "launcher_accepted",
                "launcher": item.get("launcher"),
                "browser": item.get("browser"),
                "url": str(item.get("url") or "")[:1000],
                "accepted_at": item.get("accepted_at"),
                "scope": "启动器已接受请求；不证明网页已加载或发生页面交互",
            })
        if item.get("saved") is True:
            evidence.append({
                "kind": "local_file_saved",
                "name": str(item.get("name") or "")[:300] or None,
                "path": str(
                    item.get("destination_path")
                    or item.get("path")
                    or ""
                )[:2000] or None,
                "size_bytes": item.get("size_bytes"),
                "saved_at": item.get("saved_at"),
            })
        artifact = item.get("artifact")
        if isinstance(artifact, dict) and artifact.get("path"):
            evidence.append({
                "kind": "artifact_recorded",
                "path": str(artifact.get("path"))[:2000],
                "size_bytes": artifact.get("size_bytes"),
                "hash": str(artifact.get("hash") or "")[:128] or None,
            })
        generated = item.get("generated_files")
        if isinstance(generated, list):
            for generated_file in generated[:20]:
                if not isinstance(generated_file, dict):
                    continue
                location = generated_file.get("path") or generated_file.get("url")
                if location:
                    evidence.append({
                        "kind": "generated_file",
                        "path": str(location)[:2000],
                        "name": str(generated_file.get("name") or "")[:300] or None,
                        "size_bytes": generated_file.get("size_bytes"),
                    })
    unique: dict[str, dict[str, Any]] = {}
    for item in evidence:
        unique[json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)] = item
    return list(unique.values())


def _endpoint_health(base_url: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/health"
    request = Request(url, headers={"Accept": "application/json"})
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(request, timeout=3) as response:
            body = response.read(65_536).decode("utf-8", errors="replace")
            payload = _json_value(body, {"text": body[:500]})
            return {
                "ok": 200 <= response.status < 300,
                "status_code": response.status,
                "payload": payload,
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
        }


def _database_summary() -> dict[str, Any]:
    database = _database_path()
    if not database.is_file():
        return {"ok": False, "path": str(database), "error": "数据库不存在"}
    try:
        with closing(_connect_read_only()) as connection:
            runs = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status IN ('queued', 'running', 'waiting_approval') THEN 1 ELSE 0 END) AS active,
                       MAX(created_at) AS latest
                  FROM agent_runs
                """
            ).fetchone()
            tool_calls = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                       MAX(created_at) AS latest
                  FROM agent_tool_calls
                """
            ).fetchone()
        return {
            "ok": True,
            "path": str(database),
            "size_bytes": database.stat().st_size,
            "agent_runs": {
                "total": int(runs["total"] or 0),
                "active": int(runs["active"] or 0),
                "latest": runs["latest"],
            },
            "tool_calls": {
                "total": int(tool_calls["total"] or 0),
                "completed": int(tool_calls["completed"] or 0),
                "latest": tool_calls["latest"],
            },
        }
    except (OSError, sqlite3.Error) as exc:
        return {
            "ok": False,
            "path": str(database),
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
        }


def _runtime_health() -> dict[str, Any]:
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "backend": _endpoint_health(_settings.backend_url),
        "local_model": _endpoint_health(_settings.model_url),
        "database": _database_summary(),
    }


def _list_recent_runs(limit: int = 10, session_id: str | None = None) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit), 50))
    where = "WHERE r.session_id = ?" if session_id else ""
    parameters: list[Any] = [session_id] if session_id else []
    parameters.append(bounded_limit)
    with closing(_connect_read_only()) as connection:
        rows = connection.execute(
            f"""
            SELECT r.id, r.session_id, r.status, r.mode, r.targets_json,
                   r.error, r.created_at, r.updated_at, r.completed_at,
                   COUNT(c.id) AS tool_call_count,
                   SUM(CASE WHEN c.status = 'completed' THEN 1 ELSE 0 END) AS completed_tool_calls,
                   SUM(CASE WHEN c.status IN ('failed', 'denied') THEN 1 ELSE 0 END) AS unsuccessful_tool_calls
              FROM agent_runs AS r
              LEFT JOIN agent_tool_calls AS c ON c.run_id = r.id
              {where}
             GROUP BY r.id
             ORDER BY r.created_at DESC
             LIMIT ?
            """,
            parameters,
        ).fetchall()
    return {
        "read_only": True,
        "runs": [
            {
                "run_id": row["id"],
                "session_id": row["session_id"],
                "status": row["status"],
                "mode": row["mode"],
                "targets": _json_value(row["targets_json"], []),
                "tool_call_count": int(row["tool_call_count"] or 0),
                "completed_tool_calls": int(row["completed_tool_calls"] or 0),
                "unsuccessful_tool_calls": int(row["unsuccessful_tool_calls"] or 0),
                "has_execution_evidence": int(row["tool_call_count"] or 0) > 0,
                "error": str(row["error"] or "")[:1000] or None,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "completed_at": row["completed_at"],
            }
            for row in rows
        ],
        "interpretation": "tool_call_count=0 表示该 Run 没有系统工具执行记录；模型文字不能替代执行凭证。",
    }


def _get_run_evidence(run_id: str) -> dict[str, Any]:
    normalized = str(run_id or "").strip()
    if not _RUN_ID.fullmatch(normalized):
        raise ValueError("run_id 格式无效")
    with closing(_connect_read_only()) as connection:
        run = connection.execute(
            """
            SELECT id, session_id, status, mode, targets_json, error,
                   created_at, updated_at, completed_at
              FROM agent_runs
             WHERE id = ?
            """,
            (normalized,),
        ).fetchone()
        if run is None:
            raise ValueError("未找到 Agent Run")
        rows = connection.execute(
            """
            SELECT id, model, provider_type, provider_model, tool_name,
                   arguments_json, side_effect, status, attempt, round_index,
                   call_index, tool_run_id, result_json, error,
                   created_at, completed_at
              FROM agent_tool_calls
             WHERE run_id = ?
             ORDER BY created_at, round_index, call_index
            """,
            (normalized,),
        ).fetchall()

    calls = []
    verified_effects = 0
    for row in rows:
        result = _json_value(row["result_json"], None)
        effects = _effect_evidence(result)
        if effects:
            verified_effects += 1
        calls.append({
            "call_id": row["id"],
            "model": row["model"],
            "provider_type": row["provider_type"],
            "provider_model": row["provider_model"],
            "tool_name": row["tool_name"],
            "status": row["status"],
            "side_effect": bool(row["side_effect"]),
            "attempt": int(row["attempt"] or 0),
            "round_index": int(row["round_index"] or 0),
            "call_index": int(row["call_index"] or 0),
            "tool_run_id": row["tool_run_id"],
            "arguments": _safe_arguments(_json_value(row["arguments_json"], {})),
            "result_recorded": result is not None,
            "effect_evidence": effects,
            "error": str(row["error"] or "")[:1000] or None,
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        })

    completed = sum(1 for call in calls if call["status"] == "completed")
    unsuccessful = sum(1 for call in calls if call["status"] in {"failed", "denied"})
    if not calls:
        conclusion = "没有工具调用记录；该 Run 中的操作性描述不能视为真实执行。"
    elif verified_effects:
        conclusion = "存在系统记录的效果凭证；请按每条凭证的 scope 判断证明范围。"
    else:
        conclusion = "存在工具调用记录，但没有启动器、保存文件或生成文件等效果凭证。"
    return {
        "read_only": True,
        "run": {
            "run_id": run["id"],
            "session_id": run["session_id"],
            "status": run["status"],
            "mode": run["mode"],
            "targets": _json_value(run["targets_json"], []),
            "error": str(run["error"] or "")[:1000] or None,
            "created_at": run["created_at"],
            "updated_at": run["updated_at"],
            "completed_at": run["completed_at"],
        },
        "summary": {
            "tool_call_count": len(calls),
            "completed_tool_calls": completed,
            "unsuccessful_tool_calls": unsuccessful,
            "verified_effect_count": verified_effects,
        },
        "tool_calls": calls,
        "conclusion": conclusion,
    }


@server.tool()
def get_runtime_health() -> dict[str, Any]:
    """只读检查 AetherChat 后端、本地模型和审计数据库是否健康。"""
    return _runtime_health()


@server.tool()
def list_recent_runs(limit: int = 10, session_id: str | None = None) -> dict[str, Any]:
    """列出最近 Agent Run 及其工具调用数量；不返回用户消息正文。"""
    return _list_recent_runs(limit=limit, session_id=session_id)


@server.tool()
def get_run_evidence(run_id: str) -> dict[str, Any]:
    """读取指定 Run 的真实工具记录和效果凭证；模型文字本身不算凭证。"""
    return _get_run_evidence(run_id)


async def _serve_stdio() -> None:
    """Run stdio without letting temporary UTF-8 wrappers close OS stdio."""
    input_stream = TextIOWrapper(
        sys.stdin.buffer,
        encoding="utf-8",
        errors="replace",
    )
    output_stream = TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    try:
        async with stdio_server(
            stdin=anyio.wrap_file(input_stream),
            stdout=anyio.wrap_file(output_stream),
        ) as (read_stream, write_stream):
            await server._mcp_server.run(
                read_stream,
                write_stream,
                server._mcp_server.create_initialization_options(),
            )
    finally:
        for stream in (input_stream, output_stream):
            with suppress(ValueError):
                stream.detach()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="AetherChat read-only local audit MCP")
    parser.add_argument("--data-dir", default=str(_default_data_dir()))
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model-url", default="http://127.0.0.1:8080")
    args = parser.parse_args(argv)
    configure(
        args.data_dir,
        backend_url=args.backend_url,
        model_url=args.model_url,
    )
    anyio.run(_serve_stdio)


if __name__ == "__main__":
    main()
