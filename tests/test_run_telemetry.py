from __future__ import annotations

import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.chat_database import ChatDatabase  # noqa: E402
from app.services.diagnostic_service import DiagnosticService  # noqa: E402
from app.services.run_telemetry import RunTelemetryService  # noqa: E402
from app.services.structured_logging import diagnostic_ref  # noqa: E402

FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "run_telemetry_cases.json"


def _seed_telemetry_database(path: Path) -> dict:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    ChatDatabase(path)
    session_id = fixture["session_id"]
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO sessions
               (id, title, summary, mode, created_at, updated_at)
               VALUES (?, 'private session title', '', 'project', ?, ?)""",
            (session_id, "2026-08-12T00:00:00Z", "2026-08-12T02:00:05Z"),
        )
        for run in fixture["runs"]:
            connection.execute(
                """INSERT INTO agent_runs
                   (id, session_id, status, mode, input_json, targets_json,
                    retry_count, error, created_at, updated_at, started_at,
                    completed_at)
                   VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?)""",
                (
                    run["id"],
                    session_id,
                    run["status"],
                    run["mode"],
                    json.dumps({"content": "private run prompt"}),
                    run["retry_count"],
                    "private run error" if run["status"] == "failed" else None,
                    run["created_at"],
                    run["completed_at"],
                    run["started_at"],
                    run["completed_at"],
                ),
            )
            for step in run["steps"]:
                connection.execute(
                    """INSERT INTO agent_steps
                       (id, run_id, session_id, model, status, attempt, error,
                        created_at, updated_at, started_at, completed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        step["id"],
                        run["id"],
                        session_id,
                        step["model"],
                        step["status"],
                        step["attempt"],
                        "private step error" if step["status"] == "failed" else None,
                        step["started_at"],
                        step["completed_at"],
                        step["started_at"],
                        step["completed_at"],
                    ),
                )
            for index, step in enumerate(run["plan_steps"]):
                connection.execute(
                    """INSERT INTO agent_plan_steps
                       (id, run_id, session_id, step_key, sequence, kind, title,
                        status, attempt, created_at, updated_at, started_at,
                        completed_at)
                       VALUES (?, ?, ?, ?, ?, 'worker', ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        step["id"],
                        run["id"],
                        session_id,
                        step["step_key"],
                        index,
                        "private plan title",
                        step["status"],
                        step["attempt"],
                        step["started_at"],
                        step["completed_at"],
                        step["started_at"],
                        step["completed_at"],
                    ),
                )
            for tool in run["tools"]:
                connection.execute(
                    """INSERT INTO agent_tool_calls
                       (id, run_id, session_id, model, provider_type,
                        provider_model, provider_call_id, tool_name,
                        arguments_json, arguments_sha256, idempotency_key,
                        side_effect, status, verification_status, error,
                        created_at, updated_at, completed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'private-args-hash',
                               'private-idempotency-key', ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        tool["id"],
                        run["id"],
                        session_id,
                        tool["model"],
                        tool["provider_type"],
                        tool["provider_model"],
                        "private-provider-call-id",
                        tool["tool_name"],
                        json.dumps({"password": "private-tool-password"}),
                        tool["side_effect"],
                        tool["status"],
                        tool["verification_status"],
                        "private tool error" if tool["status"] == "failed" else None,
                        tool["created_at"],
                        tool["completed_at"],
                        tool["completed_at"],
                    ),
                )
            for event in run["events"]:
                connection.execute(
                    """INSERT INTO agent_events
                       (id, run_id, event_type, payload_json, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        event["id"],
                        run["id"],
                        event["event_type"],
                        json.dumps(event["payload"]),
                        event["created_at"],
                    ),
                )
        for message in fixture["messages"]:
            connection.execute(
                """INSERT INTO messages
                   (id, session_id, author_model, role, content, tokens_in,
                    tokens_out, cost_usd, created_at)
                   VALUES (?, ?, ?, 'assistant', 'private message content', ?, ?, ?, ?)""",
                (
                    message["id"],
                    session_id,
                    message["model"],
                    message["tokens_in"],
                    message["tokens_out"],
                    message["cost_usd"],
                    message["created_at"],
                ),
            )
    return fixture


def test_run_telemetry_aggregates_fixed_evidence_without_private_values(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "telemetry.sqlite3"
    fixture = _seed_telemetry_database(database_path)

    snapshot = RunTelemetryService(database_path).snapshot()

    assert snapshot["schema"] == "aetherchat-run-telemetry-v1"
    assert snapshot["status"] == "ready"
    assert snapshot["sample"]["runs"] == 3
    assert snapshot["runs"] == {
        "status_counts": {"completed": 1, "failed": 1, "interrupted": 1},
        "duration_ms": {"count": 3, "p50": 10000.0, "p95": 20000.0, "max": 20000.0},
        "retry_count": 1,
    }
    assert snapshot["recovery"] == {
        "target_success_rate": 0.95,
        "eligible_runs": 1,
        "attempted_runs": 1,
        "successful_runs": 0,
        "verified_successful_runs": 0,
        "unsuccessful_runs": 1,
        "pending_runs": 0,
        "success_rate": 0.0,
        "meets_target": False,
        "sample_sufficient": False,
    }
    assert snapshot["providers"] == {
        "turns": 2,
        "type_counts": {"local": 1, "openai_compat": 1},
        "first_token_ms": {"count": 1, "p50": 100.0, "p95": 100.0, "max": 100.0},
        "elapsed_ms": {"count": 1, "p50": 8000.0, "p95": 8000.0, "max": 8000.0},
    }
    assert snapshot["tools"] == {
        "calls": 2,
        "family_counts": {"mcp": 1, "plugin": 1},
        "status_counts": {"completed": 1, "failed": 1},
        "verification_counts": {"failed": 1, "verified": 1},
        "side_effect_calls": 2,
        "duration_ms": {"count": 2, "p50": 2000.0, "p95": 4000.0, "max": 4000.0},
    }
    assert snapshot["usage"] == {
        "persisted_assistant_messages": {
            "tokens_in": 150,
            "tokens_out": 25,
            "cost_usd": 0.13,
        },
        "run_attributed_events": {
            "tokens_in": 100,
            "tokens_out": 20,
            "cost_usd": 0.12,
        },
    }
    assert snapshot["failures"] == {
        "run_failed": 1,
        "step_failed": 1,
        "plan_step_failed": 0,
        "tool_failed": 1,
        "code_counts": {"provider_error": 1},
    }
    traces = {item["status"]: item for item in snapshot["traces"]}
    assert traces["completed"]["request_refs"] == [
        diagnostic_ref("request", "private-request-alpha")
    ]
    assert traces["failed"]["request_refs"] == ["request:7d34cb3b6074"]
    assert traces["completed"]["usage"] == {
        "tokens_in": 100,
        "tokens_out": 20,
        "cost_usd": 0.12,
    }
    assert traces["completed"]["spans"]["steps"][0]["status"] == "completed"
    assert traces["completed"]["spans"]["plan_steps"][0]["duration_ms"] == 6000.0
    assert [
        span["phase"] for span in traces["completed"]["spans"]["providers"]
    ] == ["requesting", "streaming", "finalizing"]
    assert all(
        span["span_ref"].startswith("provider:")
        for span in traces["completed"]["spans"]["providers"]
    )
    assert traces["completed"]["spans"]["tools"][0] == {
        "span_ref": diagnostic_ref("tool", "tool-success"),
        "provider_type": "openai_compat",
        "family": "plugin",
        "status": "completed",
        "verification_status": "verified",
        "side_effect": True,
        "started_at": "2026-08-12T00:00:02Z",
        "completed_at": "2026-08-12T00:00:04Z",
        "duration_ms": 2000.0,
    }
    assert traces["failed"]["failure_code_counts"] == {"provider_error": 1}

    encoded = json.dumps(snapshot, ensure_ascii=False)
    for forbidden in fixture["forbidden"]:
        assert forbidden not in encoded
    assert "private run prompt" not in encoded
    assert "private-tool-password" not in encoded
    assert "private run error" not in encoded


def test_run_telemetry_returns_stable_unavailable_contract(tmp_path: Path) -> None:
    snapshot = RunTelemetryService(tmp_path / "missing.sqlite3").snapshot()

    assert snapshot == {
        "schema": "aetherchat-run-telemetry-v1",
        "status": "unavailable",
        "error_code": "telemetry_database_unavailable",
    }


def test_diagnostic_bundle_exports_fixed_telemetry_without_raw_evidence(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "bundle.sqlite3"
    fixture = _seed_telemetry_database(database_path)
    service = DiagnosticService(
        ChatDatabase(database_path),
        log_path=tmp_path / "missing.jsonl",
    )

    with zipfile.ZipFile(io.BytesIO(service.bundle())) as archive:
        assert set(archive.namelist()) == {
            "README.txt",
            "diagnostics.json",
            "recent-logs.jsonl",
            "run-telemetry.json",
        }
        telemetry = json.loads(archive.read("run-telemetry.json"))
        contents = b"\n".join(archive.read(name) for name in archive.namelist())

    assert telemetry["status"] == "ready"
    assert telemetry["sample"]["runs"] == 3
    encoded = contents.decode("utf-8")
    for forbidden in fixture["forbidden"]:
        assert forbidden not in encoded
    assert "private-tool-password" not in encoded
