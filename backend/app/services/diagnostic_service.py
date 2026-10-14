from __future__ import annotations

import io
import json
import os
import platform
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .chat_database import CURRENT_SCHEMA_VERSION, chat_database
from .run_telemetry import RunTelemetryService
from .structured_logging import read_recent_log_entries, structured_log_path


class DiagnosticDatabase(Protocol):
    def readiness_probe(self) -> dict[str, Any]: ...


class DiagnosticTelemetry(Protocol):
    def snapshot(self, *, run_limit: int = 50) -> dict[str, Any]: ...


_DISPATCH_COUNTERS = (
    "capacity",
    "queue_capacity",
    "running",
    "queued",
    "submitted",
    "started",
    "completed",
    "cancelled",
    "rejected",
    "queue_high_water",
    "interactive_running",
    "background_running",
    "interactive_queued",
    "background_queued",
    "interactive_weight",
    "provider_capacity",
    "gpu_capacity",
)
_AUTOMATION_COUNTERS = (
    "active_workers",
    "concurrency",
    "dispatch_queued",
    "dispatch_running",
    "enabled_schedules",
    "queued_jobs",
    "running_jobs",
    "review_items",
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _bounded_number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    return None


class DiagnosticService:
    """Build support evidence from an explicit allowlist, never raw application state."""

    def __init__(
        self,
        database: DiagnosticDatabase | None = None,
        *,
        log_path: str | os.PathLike[str] | None = None,
        telemetry_service: DiagnosticTelemetry | None = None,
        now: Any = _utc_now,
    ) -> None:
        self.database = database if database is not None else chat_database
        # Pin the log file at construction so later env mutations (tests or
        # process wrappers) cannot silently read a different diagnostics path.
        resolved_log = log_path if log_path is not None else structured_log_path()
        self.log_path = Path(resolved_log).expanduser().absolute()
        self.telemetry_service = telemetry_service
        telemetry_path = getattr(self.database, "path", None)
        if telemetry_service is None and telemetry_path is not None:
            self.telemetry_service = RunTelemetryService(telemetry_path)
        self._now = now


    def _audit_chain_snapshot(self) -> dict[str, Any]:
        try:
            from .platform_governance import platform_governance

            result = platform_governance.verify_audit_chain()
            return {
                "ok": bool(result.get("ok")),
                "checked": int(result.get("checked") or 0),
                "tip_hash_prefix": str(result.get("tip_hash") or "")[:16],
                "error_count": len(result.get("errors") or []),
            }
        except Exception:
            return {"ok": False, "checked": 0, "tip_hash_prefix": "", "error_count": -1}

    def _database_snapshot(self) -> dict[str, Any]:
        try:
            raw = self.database.readiness_probe()
        except Exception:
            return {
                "status": "unavailable",
                "expected_schema_version": CURRENT_SCHEMA_VERSION,
            }
        version = raw.get("schema_version")
        return {
            "status": str(raw.get("status") or "unavailable")[:40],
            "schema_version": version if isinstance(version, int) else None,
            "expected_schema_version": CURRENT_SCHEMA_VERSION,
        }

    @staticmethod
    def _automation_snapshot(raw: Mapping[str, Any] | None) -> dict[str, Any]:
        supplied = raw or {}
        result: dict[str, Any] = {
            "scheduler_running": supplied.get("scheduler_running") is True,
        }
        for key in _AUTOMATION_COUNTERS:
            value = _bounded_number(supplied.get(key))
            if value is not None:
                result[key] = value
        return result

    @staticmethod
    def _dispatcher_snapshot(raw: Mapping[str, Any] | None) -> dict[str, Any]:
        supplied = raw or {}
        result: dict[str, Any] = {"accepting": supplied.get("accepting") is True}
        for key in _DISPATCH_COUNTERS:
            value = _bounded_number(supplied.get(key))
            if value is not None:
                result[key] = value

        resources = supplied.get("resources")
        if isinstance(resources, Mapping):
            provider_slots = [
                value
                for key, value in resources.items()
                if str(key).startswith("provider:") and isinstance(value, Mapping)
            ]
            gpu = resources.get("gpu")
            result["resource_summary"] = {
                "provider_slots": len(provider_slots),
                "provider_running": sum(int(item.get("running") or 0) for item in provider_slots),
                "provider_queued": sum(int(item.get("queued") or 0) for item in provider_slots),
                "gpu_running": int(gpu.get("running") or 0) if isinstance(gpu, Mapping) else 0,
                "gpu_queued": int(gpu.get("queued") or 0) if isinstance(gpu, Mapping) else 0,
            }
        return result

    def snapshot(
        self,
        *,
        automation: Mapping[str, Any] | None = None,
        run_dispatcher: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        version = str(os.environ.get("AETHERCHAT_APP_VERSION") or "unknown")[:80]
        channel = str(os.environ.get("AETHERCHAT_BUILD_CHANNEL") or "unknown")[:40]
        commit = str(os.environ.get("AETHERCHAT_BUILD_COMMIT") or "unknown")[:64]
        build_timestamp = str(
            os.environ.get("AETHERCHAT_BUILD_TIMESTAMP") or "unknown"
        )[:64]
        package_hash = str(os.environ.get("AETHERCHAT_PACKAGE_HASH") or "")[:64]
        try:
            telemetry = (
                self.telemetry_service.snapshot(run_limit=50)
                if self.telemetry_service is not None
                else {
                    "schema": "aetherchat-run-telemetry-v1",
                    "status": "unavailable",
                    "error_code": "telemetry_not_configured",
                }
            )
        except Exception:
            telemetry = {
                "schema": "aetherchat-run-telemetry-v1",
                "status": "unavailable",
                "error_code": "telemetry_snapshot_failed",
            }
        return {
            "schema": "aetherchat-diagnostics-v1",
            "generated_at": self._now().astimezone(UTC).isoformat(),
            "application": {
                "version": version,
                "channel": channel,
                "commit": commit,
                "build_timestamp": build_timestamp,
                "package_hash": package_hash,
                "platform": platform.system(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
            },
            "database": self._database_snapshot(),
            "audit_chain": self._audit_chain_snapshot(),
            "runtime": {
                "automation": self._automation_snapshot(automation),
                "run_dispatcher": self._dispatcher_snapshot(run_dispatcher),
            },
            "telemetry": telemetry,
            "recent_logs": read_recent_log_entries(self.log_path, limit=200),
            "privacy": {
                "request_bodies_included": False,
                "query_strings_included": False,
                "file_contents_included": False,
                "credentials_included": False,
                "provider_names_included": False,
                "model_names_included": False,
                "raw_run_ids_included": False,
                "tool_arguments_included": False,
            },
        }

    def bundle(
        self,
        *,
        automation: Mapping[str, Any] | None = None,
        run_dispatcher: Mapping[str, Any] | None = None,
    ) -> bytes:
        snapshot = self.snapshot(
            automation=automation,
            run_dispatcher=run_dispatcher,
        )
        logs = snapshot.pop("recent_logs")
        telemetry = snapshot.pop("telemetry")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "diagnostics.json",
                json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            archive.writestr(
                "recent-logs.jsonl",
                "".join(
                    json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
                    for entry in logs
                ),
            )
            archive.writestr(
                "run-telemetry.json",
                json.dumps(telemetry, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
            archive.writestr(
                "README.txt",
                "AetherChat 脱敏诊断包\n"
                "仅包含 allowlist 运行指标、哈希 trace 引用、数据库 schema 状态"
                "和二次脱敏日志。\n"
                "不包含请求体、查询串、文件正文、完整提示、密钥、模型/provider 名称、"
                "原始 Run ID 或工具参数。\n",
            )
        return output.getvalue()

    def bundle_filename(self) -> str:
        timestamp = self._now().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"aetherchat-diagnostics-{timestamp}.zip"


diagnostic_service = DiagnosticService()
