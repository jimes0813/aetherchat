from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

CURRENT_SCHEMA_VERSION = 4
EVENT_STREAM_RETENTION_LIMIT = 4096
READINESS_TABLES = (
    "agent_runs",
    "event_journal",
    "event_streams",
    "messages",
    "sessions",
    "tasks",
)
AGENT_RUN_COMPLETION_STATUSES = {
    "completed",
    "completed_verified",
    "completed_with_warnings",
    "uncertain",
}
AGENT_RUN_TERMINAL_STATUSES = {
    *AGENT_RUN_COMPLETION_STATUSES,
    "failed",
    "interrupted",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _database_path() -> Path:
    configured = os.environ.get("AETHERCHAT_DATA_DIR")
    if configured:
        root = Path(configured).expanduser().resolve()
    elif os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AetherChat"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "aetherchat"
    root.mkdir(parents=True, exist_ok=True)
    return root / "aetherchat.sqlite3"
