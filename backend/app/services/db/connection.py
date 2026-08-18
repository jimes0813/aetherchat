from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._shared import (
    CURRENT_SCHEMA_VERSION,
    READINESS_TABLES,
)


class ConnectionMixin:
    def _existing_schema_version(self) -> int | None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        with sqlite3.connect(self.path, timeout=15) as connection:
            row = connection.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0

    def _create_migration_backup(self, from_version: int, to_version: int) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = self.path.with_name(
            f"{self.path.name}.pre-v{from_version}-to-v{to_version}-{timestamp}.bak"
        )
        with (
            sqlite3.connect(self.path, timeout=15) as source,
            sqlite3.connect(backup_path, timeout=15) as destination,
        ):
            source.backup(destination)
        return backup_path

    def _restore_migration_backup(self, backup_path: Path) -> None:
        with (
            sqlite3.connect(backup_path, timeout=15) as source,
            sqlite3.connect(self.path, timeout=15) as destination,
        ):
            source.backup(destination)

    @staticmethod
    def _table_columns(
        connection: sqlite3.Connection,
        table: str,
    ) -> set[str]:
        return {
            str(row["name"] if isinstance(row, sqlite3.Row) else row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def readiness_probe(self) -> dict[str, Any]:
        """Run a read-only, low-cost probe for the core chat database."""
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA query_only = ON")
            version_row = connection.execute("PRAGMA user_version").fetchone()
            schema_version = int(version_row[0]) if version_row else 0
            if schema_version != CURRENT_SCHEMA_VERSION:
                return {
                    "status": "schema_mismatch",
                    "schema_version": schema_version,
                    "expected_schema_version": CURRENT_SCHEMA_VERSION,
                }

            placeholders = ", ".join("?" for _ in READINESS_TABLES)
            rows = connection.execute(
                f"""SELECT name FROM sqlite_schema
                    WHERE type = 'table' AND name IN ({placeholders})""",
                READINESS_TABLES,
            ).fetchall()
            present = {str(row["name"]) for row in rows}
            missing = sorted(set(READINESS_TABLES) - present)
            if missing:
                return {
                    "status": "schema_mismatch",
                    "schema_version": schema_version,
                    "expected_schema_version": CURRENT_SCHEMA_VERSION,
                    "missing_tables": missing,
                }

            connection.execute("SELECT id FROM sessions LIMIT 1").fetchone()
        return {
            "status": "ready",
            "schema_version": schema_version,
            "expected_schema_version": CURRENT_SCHEMA_VERSION,
        }

    async def run_in_thread(self, func, /, *args, **kwargs):
        """Run a sync database callable off the event loop."""
        return await asyncio.to_thread(func, *args, **kwargs)
