from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from ..models.schemas import Message, Task


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


class ChatDatabase:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path).expanduser().resolve() if path else _database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'project',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_message_at TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    author_model TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tokens_in INTEGER NOT NULL DEFAULT 0,
                    tokens_out INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session_created
                    ON messages(session_id, created_at);

                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    claimed_by TEXT,
                    priority INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS task_events (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_events_session_created
                    ON task_events(session_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS session_memory (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                    summary TEXT NOT NULL DEFAULT '',
                    project_note TEXT NOT NULL DEFAULT '',
                    decisions_json TEXT NOT NULL DEFAULT '[]',
                    preferences_json TEXT NOT NULL DEFAULT '[]',
                    open_items_json TEXT NOT NULL DEFAULT '[]',
                    message_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'project',
                    input_json TEXT NOT NULL,
                    targets_json TEXT NOT NULL DEFAULT '[]',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    plan_status TEXT NOT NULL DEFAULT 'none',
                    plan_revision INTEGER NOT NULL DEFAULT 0,
                    automatic_revision_count INTEGER NOT NULL DEFAULT 0,
                    plan_approval_required INTEGER NOT NULL DEFAULT 0,
                    plan_approved_at TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_runs_session_updated
                    ON agent_runs(session_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS agent_steps (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    UNIQUE(run_id, model)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_steps_run ON agent_steps(run_id, created_at);

                CREATE TABLE IF NOT EXISTS agent_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    step_id TEXT REFERENCES agent_steps(id) ON DELETE SET NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_events_run_created
                    ON agent_events(run_id, created_at);

                CREATE TABLE IF NOT EXISTS agent_goals (
                    run_id TEXT PRIMARY KEY REFERENCES agent_runs(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    objective TEXT NOT NULL,
                    success_criteria_json TEXT NOT NULL DEFAULT '[]',
                    constraints_json TEXT NOT NULL DEFAULT '[]',
                    deliverable TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_plan_steps (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    step_key TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    phase INTEGER NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    assigned_model TEXT,
                    role TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    dependencies_json TEXT NOT NULL DEFAULT '[]',
                    allow_failed_dependencies INTEGER NOT NULL DEFAULT 0,
                    input_contract_json TEXT NOT NULL DEFAULT '{}',
                    output_contract_json TEXT NOT NULL DEFAULT '{}',
                    success_criteria_json TEXT NOT NULL DEFAULT '[]',
                    output_json TEXT,
                    output_sha256 TEXT,
                    error TEXT,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    UNIQUE(run_id, step_key)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_plan_steps_run
                    ON agent_plan_steps(run_id, phase, sequence);

                CREATE TABLE IF NOT EXISTS agent_plan_revisions (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    change_summary TEXT NOT NULL DEFAULT '',
                    goal_json TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    UNIQUE(run_id, revision)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_plan_revisions_run
                    ON agent_plan_revisions(run_id, revision);

                CREATE TABLE IF NOT EXISTS agent_reviews (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    plan_step_id TEXT REFERENCES agent_plan_steps(id) ON DELETE SET NULL,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    reviewer_model TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    issues_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_reviews_run
                    ON agent_reviews(run_id, created_at);

                CREATE TABLE IF NOT EXISTS agent_tool_calls (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    step_id TEXT REFERENCES agent_steps(id) ON DELETE SET NULL,
                    plan_step_id TEXT REFERENCES agent_plan_steps(id) ON DELETE SET NULL,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    model TEXT NOT NULL,
                    provider_type TEXT NOT NULL DEFAULT '',
                    provider_model TEXT NOT NULL DEFAULT '',
                    provider_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL DEFAULT '{}',
                    arguments_sha256 TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    side_effect INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    round_index INTEGER NOT NULL DEFAULT 0,
                    call_index INTEGER NOT NULL DEFAULT 0,
                    tool_run_id TEXT,
                    result_json TEXT,
                    result_sha256 TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(run_id, model, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_run
                    ON agent_tool_calls(run_id, model, round_index, call_index);

                CREATE TABLE IF NOT EXISTS agent_checkpoints (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    step_id TEXT REFERENCES agent_steps(id) ON DELETE SET NULL,
                    plan_step_id TEXT REFERENCES agent_plan_steps(id) ON DELETE SET NULL,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    model TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    state_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, model, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_checkpoints_run
                    ON agent_checkpoints(run_id, model, sequence DESC);

                CREATE TABLE IF NOT EXISTS agent_steers (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    targets_json TEXT NOT NULL DEFAULT '[]',
                    applied_models_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL,
                    applied_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_steers_run
                    ON agent_steers(run_id, created_at);

                CREATE TABLE IF NOT EXISTS workspace_repositories (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    root_path TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    head_commit TEXT NOT NULL,
                    branch TEXT NOT NULL DEFAULT '',
                    verification_commands_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'ready',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(session_id, root_path)
                );
                CREATE INDEX IF NOT EXISTS idx_workspace_repositories_session
                    ON workspace_repositories(session_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS agent_workspaces (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    plan_step_id TEXT NOT NULL REFERENCES agent_plan_steps(id) ON DELETE CASCADE,
                    repository_id TEXT NOT NULL REFERENCES workspace_repositories(id) ON DELETE RESTRICT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    attempt INTEGER NOT NULL,
                    root_path TEXT NOT NULL,
                    base_commit TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'prepared',
                    changed_files_json TEXT NOT NULL DEFAULT '[]',
                    diff_patch BLOB,
                    diff_sha256 TEXT,
                    diff_bytes INTEGER NOT NULL DEFAULT 0,
                    verification_status TEXT NOT NULL DEFAULT 'pending',
                    verification_json TEXT NOT NULL DEFAULT '[]',
                    apply_status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    verified_at TEXT,
                    applied_at TEXT,
                    UNIQUE(run_id, plan_step_id, attempt)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_workspaces_run
                    ON agent_workspaces(run_id, created_at);
                """
            )
            message_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(messages)").fetchall()
            }
            if "attachments_json" not in message_columns:
                connection.execute(
                    "ALTER TABLE messages ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]'"
                )
            memory_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(session_memory)").fetchall()
            }
            if "project_note" not in memory_columns:
                connection.execute(
                    "ALTER TABLE session_memory ADD COLUMN project_note TEXT NOT NULL DEFAULT ''"
                )
            tool_call_columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(agent_tool_calls)"
                ).fetchall()
            }
            if "plan_step_id" not in tool_call_columns:
                connection.execute(
                    "ALTER TABLE agent_tool_calls ADD COLUMN plan_step_id TEXT"
                )
            checkpoint_columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(agent_checkpoints)"
                ).fetchall()
            }
            if "plan_step_id" not in checkpoint_columns:
                connection.execute(
                    "ALTER TABLE agent_checkpoints ADD COLUMN plan_step_id TEXT"
                )
            run_columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(agent_runs)"
                ).fetchall()
            }
            for column, definition in (
                ("plan_status", "TEXT NOT NULL DEFAULT 'none'"),
                ("plan_revision", "INTEGER NOT NULL DEFAULT 0"),
                ("automatic_revision_count", "INTEGER NOT NULL DEFAULT 0"),
                ("plan_approval_required", "INTEGER NOT NULL DEFAULT 0"),
                ("plan_approved_at", "TEXT"),
            ):
                if column not in run_columns:
                    connection.execute(
                        f"ALTER TABLE agent_runs ADD COLUMN {column} {definition}"
                    )

    def ensure_session(
        self,
        session_id: str,
        *,
        title: str = "新会话",
        mode: str = "project",
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (id, title, summary, mode, created_at, updated_at)
                VALUES (?, ?, '', ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (str(session_id), title.strip()[:200] or "新会话", mode, now, now),
            )
            row = connection.execute("SELECT * FROM sessions WHERE id = ?", (str(session_id),)).fetchone()
        return dict(row) if row else {}

    def create_session(self, session_id: str, title: str, mode: str = "project") -> dict[str, Any]:
        return self.ensure_session(session_id, title=title, mode=mode)

    def save_message(self, message: Message, *, mode: str = "project") -> None:
        session_id = str(message.session_id)
        self.ensure_session(session_id, mode=mode)
        created_at = message.created_at.isoformat()
        summary = " ".join(message.content.strip().split())[:180]
        with self._lock, self._connect() as connection:
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO messages
                    (id, session_id, author_model, role, content, tokens_in, tokens_out, cost_usd, attachments_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(message.id), session_id, message.author_model, message.role, message.content,
                    message.tokens_in, message.tokens_out, message.cost_usd,
                    json.dumps(message.attachments, ensure_ascii=False), created_at,
                ),
            ).rowcount
            if not inserted:
                return
            row = connection.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
            title = row["title"] if row else "新会话"
            if message.role == "user" and title in {"新会话", "未命名会话", "新协作会话"}:
                title = summary[:48] or title
            connection.execute(
                """
                UPDATE sessions
                SET title = ?, summary = ?, mode = ?, updated_at = ?, last_message_at = ?
                WHERE id = ?
                """,
                (title, summary, mode, created_at, created_at, session_id),
            )

    def list_messages(self, session_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        self.ensure_session(session_id)
        bounded = max(1, min(5000, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT messages.*, rowid AS _rowid FROM messages WHERE session_id = ?
                    ORDER BY created_at DESC, rowid DESC LIMIT ?
                ) ORDER BY created_at ASC, _rowid ASC
                """,
                (str(session_id), bounded),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.pop("_rowid", None)
            try:
                item["attachments"] = json.loads(item.pop("attachments_json", "[]"))
            except (json.JSONDecodeError, TypeError):
                item["attachments"] = []
            result.append(item)
        return result

    def chat_history(self, session_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        return self.list_messages(session_id, limit)

    def get_memory(self, session_id: str) -> dict[str, Any]:
        self.ensure_session(session_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM session_memory WHERE session_id = ?", (str(session_id),)
            ).fetchone()
        if row is None:
            return {
                "session_id": str(session_id), "summary": "", "project_note": "", "decisions": [],
                "preferences": [], "open_items": [], "message_count": 0, "updated_at": None,
            }
        item = dict(row)
        for field in ("decisions", "preferences", "open_items"):
            try:
                item[field] = json.loads(item.pop(f"{field}_json"))
            except (json.JSONDecodeError, TypeError):
                item[field] = []
        return item

    def save_memory(
        self,
        session_id: str,
        *,
        summary: str,
        project_note: str,
        decisions: list[str],
        preferences: list[str],
        open_items: list[str],
        message_count: int,
    ) -> dict[str, Any]:
        self.ensure_session(session_id)
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO session_memory
                   (session_id, summary, project_note, decisions_json, preferences_json, open_items_json, message_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     summary=excluded.summary,
                     project_note=excluded.project_note,
                     decisions_json=excluded.decisions_json,
                     preferences_json=excluded.preferences_json,
                     open_items_json=excluded.open_items_json,
                     message_count=excluded.message_count,
                     updated_at=excluded.updated_at""",
                (
                    str(session_id), str(summary)[:4000], str(project_note)[:4000],
                    json.dumps(decisions[:30], ensure_ascii=False),
                    json.dumps(preferences[:30], ensure_ascii=False),
                    json.dumps(open_items[:30], ensure_ascii=False),
                    max(0, int(message_count)), now,
                ),
            )
        return self.get_memory(session_id)

    def list_tasks_for_session(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT id, title, description, status, claimed_by, priority
                   FROM tasks WHERE session_id = ? ORDER BY priority DESC, updated_at DESC""",
                (str(session_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(500, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*,
                       COUNT(DISTINCT m.id) AS message_count,
                       COUNT(DISTINCT t.id) AS task_count,
                       COUNT(DISTINCT CASE WHEN t.status NOT IN ('done', 'merged') THEN t.id END) AS open_task_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                LEFT JOIN tasks t ON t.session_id = s.id
                GROUP BY s.id
                ORDER BY COALESCE(s.last_message_at, s.updated_at) DESC
                LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _task_payload(task: Task) -> dict[str, Any]:
        return task.model_dump(mode="json")

    def sync_tasks(self, tasks: Iterable[Task], *, record_events: bool = True) -> None:
        now = _utc_now()
        task_list = list(tasks)
        with self._lock, self._connect() as connection:
            for task in task_list:
                session_id = str(task.session_id)
                self._ensure_session_in_connection(connection, session_id, now)
                previous = connection.execute("SELECT * FROM tasks WHERE id = ?", (str(task.id),)).fetchone()
                payload = self._task_payload(task)
                changed = previous is None or any(
                    previous[key] != payload.get(key)
                    for key in ("title", "description", "status", "claimed_by", "priority")
                )
                created_at = previous["created_at"] if previous else now
                connection.execute(
                    """
                    INSERT INTO tasks
                        (id, session_id, title, description, status, claimed_by, priority, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        session_id=excluded.session_id, title=excluded.title,
                        description=excluded.description, status=excluded.status,
                        claimed_by=excluded.claimed_by, priority=excluded.priority,
                        updated_at=excluded.updated_at
                    """,
                    (
                        str(task.id), session_id, task.title, task.description, task.status,
                        task.claimed_by, task.priority, created_at, now,
                    ),
                )
                if changed and record_events:
                    action = "created" if previous is None else "updated"
                    connection.execute(
                        """
                        INSERT INTO task_events (id, task_id, session_id, action, snapshot_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (str(uuid4()), str(task.id), session_id, action, json.dumps(payload, ensure_ascii=False), now),
                    )
            connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id IN (SELECT DISTINCT session_id FROM tasks)",
                (now,),
            )

    @staticmethod
    def _ensure_session_in_connection(connection: sqlite3.Connection, session_id: str, now: str) -> None:
        connection.execute(
            """
            INSERT INTO sessions (id, title, summary, mode, created_at, updated_at)
            VALUES (?, '新会话', '', 'project', ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (session_id, now, now),
        )

    def load_all_tasks(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT id, session_id, title, description, status, claimed_by, priority FROM tasks ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_task_events(self, session_id: str, limit: int = 300) -> list[dict[str, Any]]:
        bounded = max(1, min(2000, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, task_id, session_id, action, snapshot_json, created_at
                FROM task_events WHERE session_id = ? ORDER BY created_at DESC LIMIT ?
                """,
                (str(session_id), bounded),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["task"] = json.loads(item.pop("snapshot_json"))
            result.append(item)
        return result

    @staticmethod
    def _decode_agent_goal(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("success_criteria_json", "success_criteria"),
            ("constraints_json", "constraints"),
        ):
            try:
                item[target] = json.loads(item.pop(source))
            except (json.JSONDecodeError, TypeError):
                item[target] = []
        return item

    def create_agent_goal(
        self,
        *,
        run_id: str,
        session_id: str,
        objective: str,
        success_criteria: list[str],
        constraints: list[str],
        deliverable: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_goals
                   (run_id, session_id, objective, success_criteria_json,
                    constraints_json, deliverable, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                   ON CONFLICT(run_id) DO NOTHING""",
                (
                    str(run_id), str(session_id), str(objective),
                    json.dumps(success_criteria, ensure_ascii=False),
                    json.dumps(constraints, ensure_ascii=False),
                    str(deliverable), now, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM agent_goals WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        return self._decode_agent_goal(row)

    def get_agent_goal(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_goals WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        return self._decode_agent_goal(row) if row else None

    def update_agent_goal_status(self, run_id: str, status: str) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE agent_goals SET status = ?, updated_at = ? WHERE run_id = ?",
                (str(status), now, str(run_id)),
            )
        return self.get_agent_goal(run_id)

    @staticmethod
    def _decode_agent_plan_step(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for source, target, fallback in (
            ("dependencies_json", "dependencies", []),
            ("input_contract_json", "input_contract", {}),
            ("output_contract_json", "output_contract", {}),
            ("success_criteria_json", "success_criteria", []),
            ("output_json", "output", None),
        ):
            raw = item.pop(source, None)
            if raw is None:
                item[target] = fallback
                continue
            try:
                item[target] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                item[target] = fallback
        item["allow_failed_dependencies"] = bool(
            item.get("allow_failed_dependencies")
        )
        return item

    def create_agent_plan_steps(
        self,
        *,
        run_id: str,
        session_id: str,
        steps: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            for step in steps:
                step_id = str(step.get("id") or uuid4())
                status = str(step.get("status") or "pending")
                output = step.get("output")
                connection.execute(
                    """INSERT INTO agent_plan_steps
                       (id, run_id, session_id, step_key, sequence, phase, kind,
                        title, description, assigned_model, role, status,
                        dependencies_json, allow_failed_dependencies,
                        input_contract_json, output_contract_json,
                        success_criteria_json, output_json, output_sha256,
                        error, attempt, created_at, updated_at, started_at,
                        completed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(run_id, step_key) DO NOTHING""",
                    (
                        step_id, str(run_id), str(session_id),
                        str(step.get("step_key") or step_id),
                        int(step.get("sequence") or 0),
                        int(step.get("phase") or 0),
                        str(step.get("kind") or "worker"),
                        str(step.get("title") or "未命名步骤"),
                        str(step.get("description") or ""),
                        str(step.get("assigned_model") or "") or None,
                        str(step.get("role") or ""),
                        status,
                        json.dumps(step.get("dependencies") or [], ensure_ascii=False),
                        1 if step.get("allow_failed_dependencies") else 0,
                        json.dumps(step.get("input_contract") or {}, ensure_ascii=False),
                        json.dumps(step.get("output_contract") or {}, ensure_ascii=False),
                        json.dumps(step.get("success_criteria") or [], ensure_ascii=False),
                        (
                            json.dumps(output, ensure_ascii=False, default=str)
                            if output is not None else None
                        ),
                        step.get("output_sha256"),
                        step.get("error"),
                        int(step.get("attempt") or 0),
                        now, now,
                        now if status == "running" else None,
                        now if status in {"completed", "failed", "skipped"} else None,
                    ),
                )
        return self.list_agent_plan_steps(run_id)

    def list_agent_plan_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_plan_steps WHERE run_id = ?
                   ORDER BY phase, sequence, created_at""",
                (str(run_id),),
            ).fetchall()
        return [self._decode_agent_plan_step(row) for row in rows]

    def get_agent_plan_step(self, step_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_plan_steps WHERE id = ?", (str(step_id),)
            ).fetchone()
        return self._decode_agent_plan_step(row) if row else None

    @classmethod
    def _plan_snapshot_in_connection(
        cls,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        goal_row = connection.execute(
            "SELECT * FROM agent_goals WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        step_rows = connection.execute(
            """SELECT * FROM agent_plan_steps WHERE run_id = ?
               ORDER BY phase, sequence, created_at""",
            (str(run_id),),
        ).fetchall()
        goal = cls._decode_agent_goal(goal_row) if goal_row else {}
        steps = [cls._decode_agent_plan_step(row) for row in step_rows]
        return goal, steps

    @staticmethod
    def _insert_plan_revision_in_connection(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        session_id: str,
        revision: int,
        source: str,
        status: str,
        change_summary: str,
        goal: dict[str, Any],
        steps: list[dict[str, Any]],
        created_at: str,
        approved_at: str | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO agent_plan_revisions
               (id, run_id, session_id, revision, source, status,
                change_summary, goal_json, steps_json, created_at, approved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id, revision) DO NOTHING""",
            (
                str(uuid4()), str(run_id), str(session_id), int(revision),
                str(source), str(status), str(change_summary)[:4000],
                json.dumps(goal, ensure_ascii=False, default=str),
                json.dumps(steps, ensure_ascii=False, default=str),
                created_at, approved_at,
            ),
        )

    @staticmethod
    def _decode_agent_plan_revision(
        row: sqlite3.Row | dict[str, Any],
    ) -> dict[str, Any]:
        item = dict(row)
        for source, target, fallback in (
            ("goal_json", "goal", {}),
            ("steps_json", "steps", []),
        ):
            try:
                item[target] = json.loads(item.pop(source))
            except (json.JSONDecodeError, TypeError):
                item[target] = fallback
        return item

    def initialize_agent_plan(
        self,
        *,
        run_id: str,
        session_id: str,
        approval_required: bool,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        plan_status = "draft" if approval_required else "approved"
        approved_at = None if approval_required else now
        with self._lock, self._connect() as connection:
            run = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ? AND session_id = ?",
                (str(run_id), str(session_id)),
            ).fetchone()
            if run is None:
                return None
            if int(run["plan_revision"] or 0) > 0:
                return self.get_agent_run(run_id, include_events=False)
            goal, steps = self._plan_snapshot_in_connection(connection, run_id)
            if not goal or not steps:
                raise ValueError("计划快照缺少目标或步骤")
            connection.execute(
                """UPDATE agent_runs
                   SET plan_status = ?, plan_revision = 1,
                       plan_approval_required = ?, plan_approved_at = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (
                    plan_status, 1 if approval_required else 0,
                    approved_at, now, str(run_id),
                ),
            )
            self._insert_plan_revision_in_connection(
                connection,
                run_id=run_id,
                session_id=session_id,
                revision=1,
                source="planner",
                status=plan_status,
                change_summary="确定性 Planner 创建初始计划",
                goal=goal,
                steps=steps,
                created_at=now,
                approved_at=approved_at,
            )
        return self.get_agent_run(run_id, include_events=False)

    def list_agent_plan_revisions(
        self,
        run_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(200, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_plan_revisions WHERE run_id = ?
                   ORDER BY revision DESC LIMIT ?""",
                (str(run_id), bounded),
            ).fetchall()
        return [self._decode_agent_plan_revision(row) for row in rows]

    def revise_agent_plan_draft(
        self,
        *,
        run_id: str,
        session_id: str,
        expected_revision: int,
        goal: dict[str, Any],
        step_updates: list[dict[str, Any]],
        change_summary: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            run = connection.execute(
                """SELECT * FROM agent_runs
                   WHERE id = ? AND session_id = ?""",
                (str(run_id), str(session_id)),
            ).fetchone()
            if run is None:
                raise KeyError("运行记录不存在")
            if (
                run["status"] != "waiting_plan_confirmation"
                or run["plan_status"] != "draft"
            ):
                raise ValueError("只有等待确认的计划草稿可以编辑")
            if int(run["plan_revision"] or 0) != int(expected_revision):
                raise RuntimeError("计划版本已变化，请刷新后重试")
            connection.execute(
                """UPDATE agent_goals
                   SET objective = ?, success_criteria_json = ?,
                       constraints_json = ?, deliverable = ?,
                       updated_at = ?
                   WHERE run_id = ?""",
                (
                    str(goal["objective"]),
                    json.dumps(goal["success_criteria"], ensure_ascii=False),
                    json.dumps(goal["constraints"], ensure_ascii=False),
                    str(goal["deliverable"]),
                    now, str(run_id),
                ),
            )
            for step in step_updates:
                cursor = connection.execute(
                    """UPDATE agent_plan_steps
                       SET title = ?, description = ?, assigned_model = ?,
                           role = ?, dependencies_json = ?,
                           success_criteria_json = ?, updated_at = ?
                       WHERE id = ? AND run_id = ? AND kind != 'planner'""",
                    (
                        str(step["title"]), str(step["description"]),
                        str(step.get("assigned_model") or "") or None,
                        str(step["role"]),
                        json.dumps(step["dependencies"], ensure_ascii=False),
                        json.dumps(step["success_criteria"], ensure_ascii=False),
                        now, str(step["id"]), str(run_id),
                    ),
                )
                if cursor.rowcount != 1:
                    raise KeyError("计划步骤不存在或不可编辑")
            revision = int(expected_revision) + 1
            connection.execute(
                """UPDATE agent_runs
                   SET plan_revision = ?, plan_status = 'draft',
                       plan_approved_at = NULL, updated_at = ?
                   WHERE id = ?""",
                (revision, now, str(run_id)),
            )
            snapshot_goal, snapshot_steps = self._plan_snapshot_in_connection(
                connection, run_id
            )
            self._insert_plan_revision_in_connection(
                connection,
                run_id=run_id,
                session_id=session_id,
                revision=revision,
                source="user_edit",
                status="draft",
                change_summary=change_summary,
                goal=snapshot_goal,
                steps=snapshot_steps,
                created_at=now,
            )
        updated = self.get_agent_run(run_id, include_events=False)
        if updated is None:
            raise KeyError("运行记录不存在")
        return updated

    def approve_agent_plan(
        self,
        *,
        run_id: str,
        session_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            run = connection.execute(
                """SELECT * FROM agent_runs
                   WHERE id = ? AND session_id = ?""",
                (str(run_id), str(session_id)),
            ).fetchone()
            if run is None:
                raise KeyError("运行记录不存在")
            if (
                run["status"] != "waiting_plan_confirmation"
                or run["plan_status"] != "draft"
            ):
                raise ValueError("当前计划不在等待确认状态")
            if int(run["plan_revision"] or 0) != int(expected_revision):
                raise RuntimeError("计划版本已变化，请刷新后重试")
            connection.execute(
                """UPDATE agent_runs
                   SET status = 'running', plan_status = 'approved',
                       plan_approved_at = ?, error = NULL, updated_at = ?,
                       started_at = COALESCE(started_at, ?),
                       completed_at = NULL
                   WHERE id = ?""",
                (now, now, now, str(run_id)),
            )
            connection.execute(
                """UPDATE agent_plan_revisions
                   SET status = 'approved', approved_at = ?
                   WHERE run_id = ? AND revision = ?""",
                (now, str(run_id), int(expected_revision)),
            )
        updated = self.get_agent_run(run_id, include_events=False)
        if updated is None:
            raise KeyError("运行记录不存在")
        return updated

    def set_agent_plan_status(
        self,
        run_id: str,
        plan_status: str,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_runs SET plan_status = ?, updated_at = ?
                   WHERE id = ?""",
                (str(plan_status), now, str(run_id)),
            )
        return self.get_agent_run(run_id, include_events=False)

    def reject_agent_plan(
        self,
        *,
        run_id: str,
        session_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            run = connection.execute(
                """SELECT * FROM agent_runs
                   WHERE id = ? AND session_id = ?""",
                (str(run_id), str(session_id)),
            ).fetchone()
            if run is None:
                raise KeyError("运行记录不存在")
            if (
                run["status"] != "waiting_plan_confirmation"
                or run["plan_status"] != "draft"
            ):
                raise ValueError("当前计划不在等待确认状态")
            if int(run["plan_revision"] or 0) != int(expected_revision):
                raise RuntimeError("计划版本已变化，请刷新后重试")
            connection.execute(
                """UPDATE agent_runs
                   SET status = 'interrupted', plan_status = 'cancelled',
                       error = '用户拒绝执行计划', updated_at = ?,
                       completed_at = ?
                   WHERE id = ?""",
                (now, now, str(run_id)),
            )
            connection.execute(
                """UPDATE agent_goals
                   SET status = 'interrupted', updated_at = ?
                   WHERE run_id = ?""",
                (now, str(run_id)),
            )
            connection.execute(
                """UPDATE agent_plan_steps
                   SET status = 'skipped', error = '用户拒绝执行计划',
                       updated_at = ?, completed_at = ?
                   WHERE run_id = ? AND status IN ('pending', 'ready')""",
                (now, now, str(run_id)),
            )
            connection.execute(
                """UPDATE agent_plan_revisions
                   SET status = 'cancelled'
                   WHERE run_id = ? AND revision = ?""",
                (str(run_id), int(expected_revision)),
            )
        updated = self.get_agent_run(run_id, include_events=False)
        if updated is None:
            raise KeyError("运行记录不存在")
        return updated

    def update_agent_plan_step(
        self,
        step_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM agent_plan_steps WHERE id = ?", (str(step_id),)
            ).fetchone()
            if row is None:
                return None
            starting = status == "running" and row["status"] != "running"
            completed_at = (
                now if status in {"completed", "failed", "interrupted", "blocked", "skipped"}
                else None
            )
            connection.execute(
                """UPDATE agent_plan_steps
                   SET status = ?, error = ?, updated_at = ?,
                       attempt = attempt + ?,
                       started_at = CASE WHEN ? THEN ? ELSE started_at END,
                       completed_at = ?
                   WHERE id = ?""",
                (
                    str(status), error, now, 1 if starting else 0,
                    1 if starting else 0, now, completed_at, str(step_id),
                ),
            )
        return self.get_agent_plan_step(step_id)

    def finish_agent_plan_step(
        self,
        step_id: str,
        *,
        status: str,
        output: dict[str, Any] | None,
        output_sha256: str | None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_plan_steps
                   SET status = ?, output_json = ?, output_sha256 = ?,
                       error = ?, updated_at = ?, completed_at = ?
                   WHERE id = ?""",
                (
                    str(status),
                    (
                        json.dumps(output, ensure_ascii=False, default=str)
                        if output is not None else None
                    ),
                    output_sha256, error, now, now, str(step_id),
                ),
            )
        return self.get_agent_plan_step(step_id)

    def reset_agent_plan_for_retry(self, run_id: str) -> list[dict[str, Any]]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_plan_steps
                   SET status = CASE
                           WHEN kind = 'planner' THEN 'completed'
                           ELSE 'pending'
                       END,
                       output_json = CASE WHEN kind = 'planner' THEN output_json ELSE NULL END,
                       output_sha256 = CASE WHEN kind = 'planner' THEN output_sha256 ELSE NULL END,
                       error = NULL, updated_at = ?, started_at = NULL,
                       completed_at = CASE WHEN kind = 'planner' THEN completed_at ELSE NULL END
                   WHERE run_id = ? AND status != 'completed'""",
                (now, str(run_id)),
            )
        return self.list_agent_plan_steps(run_id)

    def reset_agent_plan_for_review_revision(
        self,
        *,
        run_id: str,
        session_id: str,
        change_summary: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            run = connection.execute(
                """SELECT * FROM agent_runs
                   WHERE id = ? AND session_id = ?""",
                (str(run_id), str(session_id)),
            ).fetchone()
            if run is None:
                raise KeyError("运行记录不存在")
            if run["plan_status"] != "executing":
                raise ValueError("只有执行中的计划可以进入自动修订")
            revision = int(run["plan_revision"] or 0) + 1
            automatic_revision_count = int(
                run["automatic_revision_count"] or 0
            ) + 1
            connection.execute(
                """UPDATE agent_plan_steps
                   SET status = CASE
                           WHEN kind = 'planner' THEN 'completed'
                           ELSE 'pending'
                       END,
                       output_json = CASE
                           WHEN kind IN ('planner', 'worker') THEN output_json
                           ELSE NULL
                       END,
                       output_sha256 = CASE
                           WHEN kind IN ('planner', 'worker') THEN output_sha256
                           ELSE NULL
                       END,
                       error = NULL, updated_at = ?, started_at = NULL,
                       completed_at = CASE
                           WHEN kind = 'planner' THEN completed_at
                           ELSE NULL
                       END
                   WHERE run_id = ?""",
                (now, str(run_id)),
            )
            connection.execute(
                """UPDATE agent_steps
                   SET status = 'pending', error = NULL, updated_at = ?,
                       started_at = NULL, completed_at = NULL
                   WHERE run_id = ?""",
                (now, str(run_id)),
            )
            connection.execute(
                """UPDATE agent_runs
                   SET plan_revision = ?, automatic_revision_count = ?,
                       plan_status = 'executing', updated_at = ?
                   WHERE id = ?""",
                (
                    revision, automatic_revision_count, now, str(run_id),
                ),
            )
            goal, steps = self._plan_snapshot_in_connection(connection, run_id)
            self._insert_plan_revision_in_connection(
                connection,
                run_id=run_id,
                session_id=session_id,
                revision=revision,
                source="verifier_replan",
                status="executing",
                change_summary=change_summary,
                goal=goal,
                steps=steps,
                created_at=now,
            )
        updated = self.get_agent_run(run_id, include_events=False)
        if updated is None:
            raise KeyError("运行记录不存在")
        return updated

    @staticmethod
    def _decode_agent_review(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("evidence_json", "evidence"),
            ("issues_json", "issues"),
        ):
            try:
                item[target] = json.loads(item.pop(source))
            except (json.JSONDecodeError, TypeError):
                item[target] = []
        return item

    def create_agent_review(
        self,
        *,
        run_id: str,
        plan_step_id: str | None = None,
        session_id: str,
        reviewer_model: str,
        verdict: str,
        summary: str,
        evidence: list[Any],
        issues: list[Any],
    ) -> dict[str, Any]:
        review_id = str(uuid4())
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_reviews
                   (id, run_id, plan_step_id, session_id, reviewer_model,
                    verdict, summary, evidence_json, issues_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    review_id, str(run_id), plan_step_id, str(session_id),
                    str(reviewer_model), str(verdict), str(summary)[:20_000],
                    json.dumps(evidence, ensure_ascii=False, default=str),
                    json.dumps(issues, ensure_ascii=False, default=str), now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM agent_reviews WHERE id = ?", (review_id,)
            ).fetchone()
        return self._decode_agent_review(row)

    def list_agent_reviews(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_reviews WHERE run_id = ?
                   ORDER BY created_at, rowid""",
                (str(run_id),),
            ).fetchall()
        return [self._decode_agent_review(row) for row in rows]

    @staticmethod
    def _decode_agent_run(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for source, target in (("input_json", "input"), ("targets_json", "targets")):
            try:
                item[target] = json.loads(item.pop(source))
            except (json.JSONDecodeError, TypeError):
                item[target] = {} if target == "input" else []
        item["plan_approval_required"] = bool(
            item.get("plan_approval_required")
        )
        return item

    def create_agent_run(self, session_id: str, action: dict[str, Any], targets: list[str]) -> dict[str, Any]:
        self.ensure_session(session_id, mode=str(action.get("mode") or "project"))
        now = _utc_now()
        run_id = str(uuid4())
        safe_action = {
            key: value for key, value in action.items()
            if key in {
                "content", "targets", "mode", "language", "attachment_ids",
                "execution_strategy", "goal_spec",
                "plan_confirmation_required", "max_revision_cycles",
                "repository_id", "repository_name",
                "repository_base_commit", "repository_branch",
                "verification_commands",
            }
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_runs
                   (id, session_id, status, mode, input_json, targets_json, created_at, updated_at)
                   VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)""",
                (
                    run_id, str(session_id), str(action.get("mode") or "project"),
                    json.dumps(safe_action, ensure_ascii=False),
                    json.dumps(list(dict.fromkeys(targets)), ensure_ascii=False), now, now,
                ),
            )
        self.append_agent_event(run_id, "run_created", {"targets": targets})
        return self.get_agent_run(run_id, include_events=False) or {}

    def list_agent_runs(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(500, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT agent_runs.*,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id) AS tool_call_count,
                          (SELECT COUNT(*) FROM agent_checkpoints
                           WHERE agent_checkpoints.run_id = agent_runs.id) AS checkpoint_count,
                          (SELECT COUNT(*) FROM agent_steers
                           WHERE agent_steers.run_id = agent_runs.id) AS steer_count,
                          (SELECT COUNT(*) FROM agent_plan_steps
                           WHERE agent_plan_steps.run_id = agent_runs.id) AS plan_step_count,
                          (SELECT COUNT(*) FROM agent_plan_steps
                           WHERE agent_plan_steps.run_id = agent_runs.id
                             AND agent_plan_steps.status = 'completed') AS completed_plan_step_count,
                          (SELECT COUNT(*) FROM agent_reviews
                           WHERE agent_reviews.run_id = agent_runs.id) AS review_count
                   FROM agent_runs
                   WHERE session_id = ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (str(session_id), bounded),
            ).fetchall()
        result = []
        for row in rows:
            run = self._decode_agent_run(row)
            run["steps"] = self.list_agent_steps(run["id"])
            goal = self.get_agent_goal(run["id"])
            if goal is not None and run["status"] != "waiting_plan_confirmation":
                goal = {
                    **goal,
                    "objective": str(goal.get("objective") or "")[:2000],
                    "deliverable": str(goal.get("deliverable") or "")[:1000],
                    "success_criteria": [],
                    "constraints": [],
                }
            run["goal"] = goal
            run["plan_steps"] = [
                {**step, "output": None}
                for step in self.list_agent_plan_steps(run["id"])
            ]
            reviews = self.list_agent_reviews(run["id"])
            run["reviews"] = [
                {
                    **review,
                    "summary": str(review.get("summary") or "")[:2000],
                    "evidence": [],
                    "issues": [],
                }
                for review in reviews[-1:]
            ]
            result.append(run)
        return result

    def get_agent_run(self, run_id: str, *, include_events: bool = True) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT agent_runs.*,
                          (SELECT COUNT(*) FROM agent_tool_calls
                           WHERE agent_tool_calls.run_id = agent_runs.id) AS tool_call_count,
                          (SELECT COUNT(*) FROM agent_checkpoints
                           WHERE agent_checkpoints.run_id = agent_runs.id) AS checkpoint_count,
                          (SELECT COUNT(*) FROM agent_steers
                           WHERE agent_steers.run_id = agent_runs.id) AS steer_count,
                          (SELECT COUNT(*) FROM agent_plan_steps
                           WHERE agent_plan_steps.run_id = agent_runs.id) AS plan_step_count,
                          (SELECT COUNT(*) FROM agent_plan_steps
                           WHERE agent_plan_steps.run_id = agent_runs.id
                             AND agent_plan_steps.status = 'completed') AS completed_plan_step_count,
                          (SELECT COUNT(*) FROM agent_reviews
                           WHERE agent_reviews.run_id = agent_runs.id) AS review_count
                   FROM agent_runs WHERE id = ?""",
                (str(run_id),),
            ).fetchone()
        if row is None:
            return None
        run = self._decode_agent_run(row)
        run["steps"] = self.list_agent_steps(run["id"])
        run["goal"] = self.get_agent_goal(run["id"])
        run["plan_steps"] = self.list_agent_plan_steps(run["id"])
        run["reviews"] = self.list_agent_reviews(run["id"])
        if include_events:
            run["plan_revisions"] = self.list_agent_plan_revisions(run["id"])
            run["events"] = self.list_agent_events(run["id"])
            run["tool_calls"] = self.list_agent_tool_calls(run["id"])
            run["checkpoints"] = self.list_agent_checkpoints(run["id"])
            run["steers"] = self.list_agent_steers(run["id"])
            run["tool_call_count"] = len(run["tool_calls"])
            run["checkpoint_count"] = len(run["checkpoints"])
            run["steer_count"] = len(run["steers"])
            run["plan_step_count"] = len(run["plan_steps"])
            run["completed_plan_step_count"] = len([
                step for step in run["plan_steps"] if step["status"] == "completed"
            ])
            run["review_count"] = len(run["reviews"])
            run["plan_revision_count"] = len(run["plan_revisions"])
        return run

    def update_agent_run(self, run_id: str, status: str, *, error: str | None = None, increment_retry: bool = False) -> dict[str, Any] | None:
        now = _utc_now()
        started_at = now if status == "running" else None
        completed_at = now if status in {"completed", "failed", "interrupted"} else None
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_runs SET status = ?, error = ?, updated_at = ?,
                   retry_count = retry_count + ?,
                   started_at = CASE WHEN ? IS NOT NULL THEN ? ELSE started_at END,
                   completed_at = ? WHERE id = ?""",
                (status, error, now, 1 if increment_retry else 0, started_at, started_at, completed_at, str(run_id)),
            )
        return self.get_agent_run(run_id, include_events=False)

    def list_agent_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_steps WHERE run_id = ? ORDER BY created_at, model", (str(run_id),)
            ).fetchall()
        return [dict(row) for row in rows]

    def update_agent_step(self, run_id: str, model: str, status: str, *, error: str | None = None) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT id, status FROM agent_steps WHERE run_id = ? AND model = ?", (str(run_id), str(model))
            ).fetchone()
            if row is None:
                run = connection.execute("SELECT session_id FROM agent_runs WHERE id = ?", (str(run_id),)).fetchone()
                if run is None:
                    return None
                step_id = str(uuid4())
                connection.execute(
                    """INSERT INTO agent_steps
                       (id, run_id, session_id, model, status, attempt, error, created_at, updated_at, started_at, completed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        step_id, str(run_id), run["session_id"], str(model), status,
                        1 if status == "running" else 0, error, now, now,
                        now if status == "running" else None,
                        now if status in {"completed", "failed", "interrupted"} else None,
                    ),
                )
            else:
                starting = status == "running" and row["status"] != "running"
                connection.execute(
                    """UPDATE agent_steps SET status = ?, error = ?, updated_at = ?,
                       attempt = attempt + ?,
                       started_at = CASE WHEN ? THEN ? ELSE started_at END,
                       completed_at = ? WHERE id = ?""",
                    (
                        status, error, now, 1 if starting else 0, 1 if starting else 0,
                        now, now if status in {"completed", "failed", "interrupted"} else None, row["id"],
                    ),
                )
            updated = connection.execute(
                "SELECT * FROM agent_steps WHERE run_id = ? AND model = ?", (str(run_id), str(model))
            ).fetchone()
        return dict(updated) if updated else None

    def append_agent_event(self, run_id: str, event_type: str, payload: dict[str, Any], step_id: str | None = None) -> dict[str, Any]:
        event = {
            "id": str(uuid4()), "run_id": str(run_id), "step_id": step_id,
            "event_type": str(event_type), "payload": payload, "created_at": _utc_now(),
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_events (id, run_id, step_id, event_type, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    event["id"], event["run_id"], step_id, event["event_type"],
                    json.dumps(payload, ensure_ascii=False, default=str)[:100_000], event["created_at"],
                ),
            )
        return event

    def list_agent_events(self, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        bounded = max(1, min(2000, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_events WHERE run_id = ?
                   ORDER BY created_at, rowid LIMIT ?""", (str(run_id), bounded)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.pop("payload_json"))
            except (json.JSONDecodeError, TypeError):
                item["payload"] = {}
            result.append(item)
        return result

    @staticmethod
    def _decode_agent_tool_call(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for source, target, fallback in (
            ("arguments_json", "arguments", {}),
            ("result_json", "result", None),
        ):
            raw = item.pop(source, None)
            if raw is None:
                item[target] = fallback
                continue
            try:
                item[target] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                item[target] = fallback
        item["side_effect"] = bool(item.get("side_effect"))
        return item

    def begin_agent_tool_call(
        self,
        *,
        run_id: str,
        step_id: str | None,
        plan_step_id: str | None = None,
        session_id: str,
        model: str,
        provider_type: str,
        provider_model: str,
        provider_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        arguments_sha256: str,
        idempotency_key: str,
        side_effect: bool,
        round_index: int,
        call_index: int,
    ) -> dict[str, Any]:
        now = _utc_now()
        arguments_json = json.dumps(arguments, ensure_ascii=False, default=str)
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                """SELECT * FROM agent_tool_calls
                   WHERE run_id = ? AND model = ? AND idempotency_key = ?""",
                (str(run_id), str(model), str(idempotency_key)),
            ).fetchone()
            if existing is not None:
                current = self._decode_agent_tool_call(existing)
                if side_effect and current["status"] == "completed":
                    current["disposition"] = "replay"
                    return current
                if side_effect and current["status"] in {
                    "pending", "waiting_approval", "running", "failed", "uncertain",
                }:
                    error = "上次副作用工具执行结果未确认；为避免重复执行，需人工检查后再处理。"
                    connection.execute(
                        """UPDATE agent_tool_calls
                           SET status = 'uncertain', error = ?, updated_at = ?
                           WHERE id = ?""",
                        (error, now, existing["id"]),
                    )
                    updated = connection.execute(
                        "SELECT * FROM agent_tool_calls WHERE id = ?", (existing["id"],)
                    ).fetchone()
                    item = self._decode_agent_tool_call(updated)
                    item["disposition"] = "uncertain"
                    return item
                connection.execute(
                    """UPDATE agent_tool_calls
                       SET step_id = ?, plan_step_id = ?,
                           provider_type = ?, provider_model = ?,
                           provider_call_id = ?, tool_name = ?, arguments_json = ?,
                           arguments_sha256 = ?, side_effect = ?, status = 'pending',
                           attempt = attempt + 1, round_index = ?, call_index = ?,
                           tool_run_id = NULL, result_json = NULL, result_sha256 = NULL,
                           error = NULL, updated_at = ?, completed_at = NULL
                       WHERE id = ?""",
                    (
                        step_id, plan_step_id, str(provider_type), str(provider_model),
                        str(provider_call_id), str(tool_name), arguments_json,
                        str(arguments_sha256), 1 if side_effect else 0,
                        int(round_index), int(call_index), now, existing["id"],
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM agent_tool_calls WHERE id = ?", (existing["id"],)
                ).fetchone()
            else:
                call_id = str(uuid4())
                connection.execute(
                    """INSERT INTO agent_tool_calls
                       (id, run_id, step_id, plan_step_id, session_id, model, provider_type,
                        provider_model, provider_call_id, tool_name, arguments_json,
                        arguments_sha256, idempotency_key, side_effect, status,
                        attempt, round_index, call_index, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending',
                               1, ?, ?, ?, ?)""",
                    (
                        call_id, str(run_id), step_id, plan_step_id,
                        str(session_id), str(model),
                        str(provider_type), str(provider_model), str(provider_call_id),
                        str(tool_name), arguments_json, str(arguments_sha256),
                        str(idempotency_key), 1 if side_effect else 0,
                        int(round_index), int(call_index), now, now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM agent_tool_calls WHERE id = ?", (call_id,)
                ).fetchone()
        item = self._decode_agent_tool_call(row)
        item["disposition"] = "execute"
        return item

    def update_agent_tool_call_status(
        self,
        call_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_tool_calls SET status = ?, error = ?, updated_at = ?
                   WHERE id = ?""",
                (str(status), error, now, str(call_id)),
            )
            row = connection.execute(
                "SELECT * FROM agent_tool_calls WHERE id = ?", (str(call_id),)
            ).fetchone()
        return self._decode_agent_tool_call(row) if row else None

    def finish_agent_tool_call(
        self,
        call_id: str,
        *,
        status: str,
        result: Any,
        result_sha256: str,
        tool_run_id: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        result_json = json.dumps(result, ensure_ascii=False, default=str)
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_tool_calls
                   SET status = ?, result_json = ?, result_sha256 = ?,
                       tool_run_id = ?, error = ?, updated_at = ?, completed_at = ?
                   WHERE id = ?""",
                (
                    str(status), result_json, str(result_sha256), tool_run_id,
                    error, now, now, str(call_id),
                ),
            )
            row = connection.execute(
                "SELECT * FROM agent_tool_calls WHERE id = ?", (str(call_id),)
            ).fetchone()
        return self._decode_agent_tool_call(row) if row else None

    def list_agent_tool_calls(self, run_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        bounded = max(1, min(5000, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_tool_calls WHERE run_id = ?
                   ORDER BY round_index, call_index, created_at LIMIT ?""",
                (str(run_id), bounded),
            ).fetchall()
        return [self._decode_agent_tool_call(row) for row in rows]

    def save_agent_checkpoint(
        self,
        *,
        run_id: str,
        step_id: str | None,
        plan_step_id: str | None,
        session_id: str,
        model: str,
        kind: str,
        state: dict[str, Any],
        state_sha256: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        checkpoint_id = str(uuid4())
        with self._lock, self._connect() as connection:
            sequence = int(connection.execute(
                """SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                   FROM agent_checkpoints WHERE run_id = ? AND model = ?""",
                (str(run_id), str(model)),
            ).fetchone()["next_sequence"])
            connection.execute(
                """INSERT INTO agent_checkpoints
                   (id, run_id, step_id, plan_step_id, session_id, model, sequence, kind,
                    state_json, state_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    checkpoint_id, str(run_id), step_id, plan_step_id,
                    str(session_id), str(model),
                    sequence, str(kind),
                    json.dumps(state, ensure_ascii=False, default=str),
                    str(state_sha256), now,
                ),
            )
        return {
            "id": checkpoint_id,
            "run_id": str(run_id),
            "step_id": step_id,
            "plan_step_id": plan_step_id,
            "session_id": str(session_id),
            "model": str(model),
            "sequence": sequence,
            "kind": str(kind),
            "state": state,
            "state_sha256": str(state_sha256),
            "created_at": now,
        }

    def list_agent_checkpoints(self, run_id: str, limit: int = 200) -> list[dict[str, Any]]:
        bounded = max(1, min(2000, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_checkpoints WHERE run_id = ?
                   ORDER BY model, sequence LIMIT ?""",
                (str(run_id), bounded),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["state"] = json.loads(item.pop("state_json"))
            except (json.JSONDecodeError, TypeError):
                item["state"] = {}
            result.append(item)
        return result

    def latest_agent_checkpoint(
        self,
        run_id: str,
        model: str,
        plan_step_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            if plan_step_id is None:
                row = connection.execute(
                    """SELECT * FROM agent_checkpoints
                       WHERE run_id = ? AND model = ? AND plan_step_id IS NULL
                       ORDER BY sequence DESC LIMIT 1""",
                    (str(run_id), str(model)),
                ).fetchone()
            else:
                row = connection.execute(
                    """SELECT * FROM agent_checkpoints
                       WHERE run_id = ? AND model = ? AND plan_step_id = ?
                       ORDER BY sequence DESC LIMIT 1""",
                    (str(run_id), str(model), str(plan_step_id)),
                ).fetchone()
        if row is None:
            return None
        item = dict(row)
        try:
            item["state"] = json.loads(item.pop("state_json"))
        except (json.JSONDecodeError, TypeError):
            item["state"] = {}
        return item

    def create_agent_steer(
        self,
        *,
        run_id: str,
        session_id: str,
        content: str,
        targets: list[str],
    ) -> dict[str, Any]:
        now = _utc_now()
        steer = {
            "id": str(uuid4()),
            "run_id": str(run_id),
            "session_id": str(session_id),
            "content": str(content),
            "targets": list(dict.fromkeys(str(value) for value in targets if str(value).strip())),
            "applied_models": [],
            "status": "queued",
            "created_at": now,
            "applied_at": None,
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_steers
                   (id, run_id, session_id, content, targets_json,
                    applied_models_json, status, created_at)
                   VALUES (?, ?, ?, ?, ?, '[]', 'queued', ?)""",
                (
                    steer["id"], steer["run_id"], steer["session_id"], steer["content"],
                    json.dumps(steer["targets"], ensure_ascii=False), now,
                ),
            )
        return steer

    def list_agent_steers(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_steers WHERE run_id = ? ORDER BY created_at, rowid",
                (str(run_id),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for source, target in (
                ("targets_json", "targets"),
                ("applied_models_json", "applied_models"),
            ):
                try:
                    item[target] = json.loads(item.pop(source))
                except (json.JSONDecodeError, TypeError):
                    item[target] = []
            result.append(item)
        return result

    def mark_agent_steer_applied(self, steer_id: str, model: str) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_steers WHERE id = ?", (str(steer_id),)
            ).fetchone()
            if row is None:
                return None
            try:
                applied = json.loads(row["applied_models_json"])
            except (json.JSONDecodeError, TypeError):
                applied = []
            if str(model) not in applied:
                applied.append(str(model))
            connection.execute(
                """UPDATE agent_steers
                   SET applied_models_json = ?, status = 'applied', applied_at = ?
                   WHERE id = ?""",
                (json.dumps(applied, ensure_ascii=False), now, str(steer_id)),
            )
        return next(
            (item for item in self.list_agent_steers(row["run_id"]) if item["id"] == str(steer_id)),
            None,
        )

    @staticmethod
    def _decode_workspace_repository(
        row: sqlite3.Row | dict[str, Any],
    ) -> dict[str, Any]:
        item = dict(row)
        try:
            item["verification_commands"] = json.loads(
                item.pop("verification_commands_json")
            )
        except (json.JSONDecodeError, TypeError):
            item["verification_commands"] = []
        return item

    def upsert_workspace_repository(
        self,
        *,
        session_id: str,
        root_path: str,
        display_name: str,
        head_commit: str,
        branch: str,
        verification_commands: list[str],
    ) -> dict[str, Any]:
        self.ensure_session(session_id)
        now = _utc_now()
        repository_id = str(uuid4())
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO workspace_repositories
                   (id, session_id, root_path, display_name, head_commit,
                    branch, verification_commands_json, status, created_at,
                    updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
                   ON CONFLICT(session_id, root_path) DO UPDATE SET
                       display_name = excluded.display_name,
                       head_commit = excluded.head_commit,
                       branch = excluded.branch,
                       verification_commands_json =
                           excluded.verification_commands_json,
                       status = 'ready',
                       updated_at = excluded.updated_at""",
                (
                    repository_id,
                    str(session_id),
                    str(root_path),
                    str(display_name),
                    str(head_commit),
                    str(branch),
                    json.dumps(
                        list(verification_commands),
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """SELECT * FROM workspace_repositories
                   WHERE session_id = ? AND root_path = ?""",
                (str(session_id), str(root_path)),
            ).fetchone()
        if row is None:
            raise RuntimeError("Git 仓库登记失败")
        return self._decode_workspace_repository(row)

    def get_workspace_repository(
        self,
        repository_id: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspace_repositories WHERE id = ?",
                (str(repository_id),),
            ).fetchone()
        return self._decode_workspace_repository(row) if row else None

    def list_workspace_repositories(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM workspace_repositories
                   WHERE session_id = ? ORDER BY updated_at DESC, rowid DESC""",
                (str(session_id),),
            ).fetchall()
        return [self._decode_workspace_repository(row) for row in rows]

    @staticmethod
    def _decode_agent_workspace(
        row: sqlite3.Row | dict[str, Any],
    ) -> dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("changed_files_json", "changed_files"),
            ("verification_json", "verification"),
        ):
            try:
                item[target] = json.loads(item.pop(source))
            except (json.JSONDecodeError, TypeError):
                item[target] = []
        patch = item.get("diff_patch")
        if isinstance(patch, memoryview):
            item["diff_patch"] = patch.tobytes()
        return item

    def create_agent_workspace(
        self,
        *,
        run_id: str,
        plan_step_id: str,
        repository_id: str,
        session_id: str,
        attempt: int,
        root_path: str,
        base_commit: str,
    ) -> dict[str, Any]:
        workspace_id = str(uuid4())
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_workspaces
                   (id, run_id, plan_step_id, repository_id, session_id,
                    attempt, root_path, base_commit, status, created_at,
                    updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?)
                   ON CONFLICT(run_id, plan_step_id, attempt) DO NOTHING""",
                (
                    workspace_id,
                    str(run_id),
                    str(plan_step_id),
                    str(repository_id),
                    str(session_id),
                    max(1, int(attempt)),
                    str(root_path),
                    str(base_commit),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """SELECT * FROM agent_workspaces
                   WHERE run_id = ? AND plan_step_id = ? AND attempt = ?""",
                (str(run_id), str(plan_step_id), max(1, int(attempt))),
            ).fetchone()
        if row is None:
            raise RuntimeError("Worker worktree 记录创建失败")
        return self._decode_agent_workspace(row)

    def get_agent_workspace(
        self,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_workspaces WHERE id = ?",
                (str(workspace_id),),
            ).fetchone()
        return self._decode_agent_workspace(row) if row else None

    def list_agent_workspaces(
        self,
        run_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_workspaces WHERE run_id = ?
                   ORDER BY created_at, rowid""",
                (str(run_id),),
            ).fetchall()
        return [self._decode_agent_workspace(row) for row in rows]

    def update_agent_workspace_evidence(
        self,
        workspace_id: str,
        *,
        status: str,
        changed_files: list[dict[str, Any]],
        diff_patch: bytes | None,
        diff_sha256: str | None,
        verification_status: str,
        verification: list[dict[str, Any]],
        error: str | None = None,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_workspaces
                   SET status = ?, changed_files_json = ?, diff_patch = ?,
                       diff_sha256 = ?, diff_bytes = ?,
                       verification_status = ?, verification_json = ?,
                       error = ?, updated_at = ?, verified_at = ?
                   WHERE id = ?""",
                (
                    str(status),
                    json.dumps(changed_files, ensure_ascii=False, default=str),
                    diff_patch,
                    str(diff_sha256) if diff_sha256 else None,
                    len(diff_patch or b""),
                    str(verification_status),
                    json.dumps(verification, ensure_ascii=False, default=str),
                    str(error or "")[:2000] or None,
                    now,
                    now,
                    str(workspace_id),
                ),
            )
        return self.get_agent_workspace(workspace_id)

    def update_agent_workspace_apply_status(
        self,
        workspace_id: str,
        *,
        apply_status: str,
        status: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE agent_workspaces
                   SET apply_status = ?,
                       status = COALESCE(?, status),
                       error = ?, updated_at = ?,
                       applied_at = CASE WHEN ? = 'applied' THEN ?
                                         ELSE applied_at END
                   WHERE id = ?""",
                (
                    str(apply_status),
                    str(status) if status else None,
                    str(error or "")[:2000] or None,
                    now,
                    str(apply_status),
                    now,
                    str(workspace_id),
                ),
            )
        return self.get_agent_workspace(workspace_id)

    def recover_incomplete_agent_runs(self) -> int:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            run_ids = [
                row["id"] for row in connection.execute(
                    "SELECT id FROM agent_runs WHERE status IN ('queued', 'running', 'waiting_approval')"
                ).fetchall()
            ]
            if not run_ids:
                return 0
            placeholders = ",".join("?" for _ in run_ids)
            connection.execute(
                f"UPDATE agent_runs SET status = 'recoverable', error = '应用退出前运行尚未完成', updated_at = ? WHERE id IN ({placeholders})",
                (now, *run_ids),
            )
            connection.execute(
                f"UPDATE agent_steps SET status = 'interrupted', error = '应用重启导致步骤中断', updated_at = ?, completed_at = ? WHERE run_id IN ({placeholders}) AND status IN ('pending', 'running', 'waiting_approval')",
                (now, now, *run_ids),
            )
            connection.execute(
                f"""UPDATE agent_tool_calls
                    SET status = CASE WHEN side_effect = 1 THEN 'uncertain' ELSE 'interrupted' END,
                        error = CASE
                            WHEN side_effect = 1
                            THEN '应用重启时副作用工具结果未确认；已阻止自动重放'
                            ELSE '应用重启导致工具调用中断'
                        END,
                        updated_at = ?
                    WHERE run_id IN ({placeholders})
                      AND status IN ('pending', 'waiting_approval', 'running')""",
                (now, *run_ids),
            )
            connection.execute(
                f"""UPDATE agent_plan_steps
                    SET status = CASE
                            WHEN status = 'running' THEN 'interrupted'
                            ELSE 'pending'
                        END,
                        error = CASE
                            WHEN status = 'running'
                            THEN '应用重启导致计划步骤中断'
                            ELSE error
                        END,
                        updated_at = ?,
                        completed_at = CASE
                            WHEN status = 'running' THEN ?
                            ELSE completed_at
                        END
                    WHERE run_id IN ({placeholders})
                      AND status IN ('pending', 'ready', 'running')""",
                (now, now, *run_ids),
            )
        for run_id in run_ids:
            self.append_agent_event(run_id, "recovery_required", {"reason": "process_restart"})
        return len(run_ids)


chat_database = ChatDatabase()
