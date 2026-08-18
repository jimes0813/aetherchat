from __future__ import annotations

import os
from pathlib import Path
from threading import RLock

from .db._shared import (
    AGENT_RUN_COMPLETION_STATUSES,
    AGENT_RUN_TERMINAL_STATUSES,
    CURRENT_SCHEMA_VERSION,
    EVENT_STREAM_RETENTION_LIMIT,
    READINESS_TABLES,
)
from .db._shared import (
    _database_path as _database_path,
)
from .db._shared import (
    _utc_now as _utc_now,
)
from .db.agent_runs import AgentRunsMixin
from .db.automation import AutomationMixin
from .db.connection import ConnectionMixin
from .db.event_journal import EventJournalMixin
from .db.messages import MessagesMixin
from .db.schema import SchemaMixin
from .db.sessions import SessionsMixin
from .db.tasks import TasksMixin
from .db.workspaces import WorkspacesMixin

__all__ = [
    "AGENT_RUN_COMPLETION_STATUSES",
    "AGENT_RUN_TERMINAL_STATUSES",
    "CURRENT_SCHEMA_VERSION",
    "ChatDatabase",
    "EVENT_STREAM_RETENTION_LIMIT",
    "READINESS_TABLES",
    "chat_database",
]


class ChatDatabase(
    SchemaMixin,
    SessionsMixin,
    MessagesMixin,
    TasksMixin,
    AgentRunsMixin,
    EventJournalMixin,
    WorkspacesMixin,
    AutomationMixin,
    ConnectionMixin,
):
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path).expanduser().resolve() if path else _database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.last_migration_backup: Path | None = None
        existing_version = self._existing_schema_version()
        if existing_version is not None and existing_version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                "数据库版本高于当前应用支持的版本: "
                f"{existing_version} > {CURRENT_SCHEMA_VERSION}"
            )
        if existing_version is not None and existing_version < CURRENT_SCHEMA_VERSION:
            self.last_migration_backup = self._create_migration_backup(
                existing_version,
                CURRENT_SCHEMA_VERSION,
            )
        try:
            self._initialize()
            # Schema 4 already-migrated DBs still need a one-shot bind of
            # historical NULL project_id rows. Idempotent.
            self.backfill_unbound_projects()
        except Exception:
            if self.last_migration_backup is not None:
                self._restore_migration_backup(self.last_migration_backup)
            raise



chat_database = ChatDatabase()
