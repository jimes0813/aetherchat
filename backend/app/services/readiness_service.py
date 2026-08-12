from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .chat_database import CURRENT_SCHEMA_VERSION, chat_database


class ReadinessDatabase(Protocol):
    def readiness_probe(self) -> dict[str, Any]: ...


OPTIONAL_DEPENDENCIES = ("redis", "automation", "comfyui", "local_models")


class ReadinessService:
    """Build a stable readiness contract without exposing diagnostic internals."""

    def __init__(self, database: ReadinessDatabase | None = None) -> None:
        self.database = database if database is not None else chat_database

    @staticmethod
    def _optional_checks(
        statuses: Mapping[str, str] | None,
    ) -> dict[str, dict[str, Any]]:
        supplied = statuses or {}
        return {
            name: {
                "status": str(supplied.get(name) or "not_probed"),
                "required": False,
            }
            for name in OPTIONAL_DEPENDENCIES
        }

    def evaluate(
        self,
        *,
        optional_statuses: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        optional_checks = self._optional_checks(optional_statuses)
        try:
            probe = self.database.readiness_probe()
        except Exception:
            return self._failure(
                code="database_unavailable",
                message="Core database is unavailable",
                database_check={
                    "status": "unavailable",
                    "expected_schema_version": CURRENT_SCHEMA_VERSION,
                },
                optional_checks=optional_checks,
            )

        raw_version = probe.get("schema_version")
        schema_version = raw_version if isinstance(raw_version, int) else None
        database_check: dict[str, Any] = {
            "status": str(probe.get("status") or "unavailable"),
            "schema_version": schema_version,
            "expected_schema_version": CURRENT_SCHEMA_VERSION,
        }
        if (
            database_check["status"] == "ready"
            and schema_version == CURRENT_SCHEMA_VERSION
        ):
            return {
                "status": "ready",
                "ready": True,
                "service": "aetherchat",
                "checks": {"database": database_check},
                "optional_dependencies": optional_checks,
            }

        if database_check["status"] == "schema_mismatch" or (
            schema_version is not None
            and schema_version != CURRENT_SCHEMA_VERSION
        ):
            database_check["status"] = "schema_mismatch"
            code = "database_schema_mismatch"
            message = "Core database schema is incompatible"
        else:
            database_check["status"] = "unavailable"
            code = "database_unavailable"
            message = "Core database is unavailable"
        return self._failure(
            code=code,
            message=message,
            database_check=database_check,
            optional_checks=optional_checks,
        )

    @staticmethod
    def _failure(
        *,
        code: str,
        message: str,
        database_check: dict[str, Any],
        optional_checks: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "status": "not_ready",
            "ready": False,
            "service": "aetherchat",
            "checks": {"database": database_check},
            "optional_dependencies": optional_checks,
            "error": {"code": code, "message": message},
        }


readiness_service = ReadinessService(chat_database)
