from __future__ import annotations

import sqlite3

from ._shared import (
    CURRENT_SCHEMA_VERSION,
)


class SchemaMixin:
    def _migration_1(self, connection: sqlite3.Connection) -> None:
        additions = {
            "messages": (
                ("attachments_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("generated_files_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("reasoning_summary_json", "TEXT NOT NULL DEFAULT '[]'"),
            ),
            "session_memory": (
                ("project_note", "TEXT NOT NULL DEFAULT ''"),
                ("entities_json", "TEXT NOT NULL DEFAULT '[]'"),
            ),
            "tasks": (("depends_on_json", "TEXT NOT NULL DEFAULT '[]'"),),
            "agent_tool_calls": (("plan_step_id", "TEXT"),),
            "agent_checkpoints": (("plan_step_id", "TEXT"),),
            "agent_runs": (
                ("plan_status", "TEXT NOT NULL DEFAULT 'none'"),
                ("plan_revision", "INTEGER NOT NULL DEFAULT 0"),
                ("automatic_revision_count", "INTEGER NOT NULL DEFAULT 0"),
                ("plan_approval_required", "INTEGER NOT NULL DEFAULT 0"),
                ("plan_approved_at", "TEXT"),
            ),
        }
        for table, columns in additions.items():
            existing = self._table_columns(connection, table)
            for column, definition in columns:
                if column not in existing:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                    )

    def _migration_2(self, connection: sqlite3.Connection) -> None:
        existing = self._table_columns(connection, "agent_tool_calls")
        for column, definition in (
            ("verification_status", "TEXT NOT NULL DEFAULT 'unverified'"),
            ("evidence_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("verified_at", "TEXT"),
        ):
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE agent_tool_calls ADD COLUMN {column} {definition}"
                )
        connection.execute(
            """UPDATE agent_tool_calls
               SET verification_status = 'not_applicable'
               WHERE side_effect = 0 AND verification_status = 'unverified'"""
        )
        connection.execute(
            """UPDATE agent_tool_calls
               SET verification_status = 'failed'
               WHERE side_effect = 1 AND status IN ('failed', 'denied')
                 AND verification_status = 'unverified'"""
        )
        connection.execute(
            """UPDATE agent_tool_calls
               SET verification_status = 'uncertain'
               WHERE side_effect = 1 AND status = 'uncertain'
                 AND verification_status = 'unverified'"""
        )

    def _migration_3(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS event_streams (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                stream_id TEXT NOT NULL,
                last_sequence INTEGER NOT NULL DEFAULT 0 CHECK(last_sequence >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(session_id, stream_id)
            )
            """
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_event_streams_updated
               ON event_streams(updated_at DESC)"""
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS event_journal (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                stream_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK(sequence >= 1),
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id, stream_id)
                    REFERENCES event_streams(session_id, stream_id) ON DELETE CASCADE,
                UNIQUE(session_id, stream_id, sequence)
            )
            """
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_event_journal_replay
               ON event_journal(session_id, stream_id, sequence)"""
        )

    def _migration_4(self, connection: sqlite3.Connection) -> None:
        """Bind sessions/runs to WP-8 governance project_id (nullable for backfill)."""
        session_cols = self._table_columns(connection, "sessions")
        if "project_id" not in session_cols:
            connection.execute(
                "ALTER TABLE sessions ADD COLUMN project_id TEXT"
            )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_sessions_project
               ON sessions(project_id)"""
        )
        run_cols = self._table_columns(connection, "agent_runs")
        if "project_id" not in run_cols:
            connection.execute(
                "ALTER TABLE agent_runs ADD COLUMN project_id TEXT"
            )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_agent_runs_project
               ON agent_runs(project_id)"""
        )
        self._backfill_unbound_projects(connection, "default")

    def _run_migrations(self, connection: sqlite3.Connection) -> None:
        row = connection.execute("PRAGMA user_version").fetchone()
        version = int(row[0]) if row else 0
        migrations = {
            1: self._migration_1,
            2: self._migration_2,
            3: self._migration_3,
            4: self._migration_4,
        }
        for target_version in range(version + 1, CURRENT_SCHEMA_VERSION + 1):
            migration = migrations[target_version]
            connection.execute("BEGIN IMMEDIATE")
            try:
                migration(connection)
                connection.execute(f"PRAGMA user_version = {target_version}")
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    @staticmethod
    def _backfill_unbound_projects(
        connection: sqlite3.Connection,
        default_project_id: str,
    ) -> dict[str, int]:
        """Bind leftover NULL/empty project_id rows to the default project."""
        clean = (default_project_id or "default").strip() or "default"
        sessions_updated = connection.execute(
            """
            UPDATE sessions
            SET project_id = ?
            WHERE project_id IS NULL OR project_id = ''
            """,
            (clean,),
        ).rowcount
        runs_from_session = connection.execute(
            """
            UPDATE agent_runs
            SET project_id = (
                SELECT sessions.project_id FROM sessions
                WHERE sessions.id = agent_runs.session_id
            )
            WHERE (project_id IS NULL OR project_id = '')
              AND EXISTS (
                SELECT 1 FROM sessions
                WHERE sessions.id = agent_runs.session_id
                  AND sessions.project_id IS NOT NULL
                  AND sessions.project_id != ''
              )
            """
        ).rowcount
        runs_default = connection.execute(
            """
            UPDATE agent_runs
            SET project_id = ?
            WHERE project_id IS NULL OR project_id = ''
            """,
            (clean,),
        ).rowcount
        return {
            "sessions": int(sessions_updated or 0),
            "runs_from_session": int(runs_from_session or 0),
            "runs_default": int(runs_default or 0),
        }

    def backfill_unbound_projects(self, default_project_id: str = "default") -> dict[str, int]:
        """Idempotent startup backfill for schema 4 DBs that already migrated."""
        with self._lock, self._connect() as connection:
            result = self._backfill_unbound_projects(connection, default_project_id)
            connection.commit()
        return result

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
                    project_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_message_at TEXT
                );
                -- idx_sessions_project is created in migration 4 after legacy tables gain project_id.

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    author_model TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tokens_in INTEGER NOT NULL DEFAULT 0,
                    tokens_out INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0,
                    attachments_json TEXT NOT NULL DEFAULT '[]',
                    generated_files_json TEXT NOT NULL DEFAULT '[]',
                    reasoning_summary_json TEXT NOT NULL DEFAULT '[]',
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
                    depends_on_json TEXT NOT NULL DEFAULT '[]',
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
                    entities_json TEXT NOT NULL DEFAULT '[]',
                    message_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'project',
                    project_id TEXT,
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
                -- idx_agent_runs_project is created in migration 4 after legacy tables gain project_id.
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
                    verification_status TEXT NOT NULL DEFAULT 'unverified',
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    verified_at TEXT,
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

                CREATE TABLE IF NOT EXISTS automation_schedules (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'project',
                    targets_json TEXT NOT NULL DEFAULT '["all"]',
                    trigger_type TEXT NOT NULL,
                    interval_minutes INTEGER,
                    timezone TEXT NOT NULL DEFAULT 'UTC',
                    next_run_at TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    max_budget_usd REAL NOT NULL DEFAULT 1.0,
                    permission_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    last_run_at TEXT,
                    last_status TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_automation_schedules_due
                    ON automation_schedules(enabled, next_run_at);
                CREATE INDEX IF NOT EXISTS idx_automation_schedules_session
                    ON automation_schedules(session_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS background_jobs (
                    id TEXT PRIMARY KEY,
                    schedule_id TEXT REFERENCES automation_schedules(id) ON DELETE SET NULL,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    agent_run_id TEXT REFERENCES agent_runs(id) ON DELETE SET NULL,
                    trigger_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    permission_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    max_budget_usd REAL NOT NULL DEFAULT 1.0,
                    budget_spent_usd REAL NOT NULL DEFAULT 0.0,
                    scheduled_for TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    summary TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    reviewed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    UNIQUE(schedule_id, scheduled_for)
                );
                CREATE INDEX IF NOT EXISTS idx_background_jobs_claim
                    ON background_jobs(status, scheduled_for, lease_expires_at);
                CREATE INDEX IF NOT EXISTS idx_background_jobs_session
                    ON background_jobs(session_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_background_jobs_review
                    ON background_jobs(session_id, reviewed_at, completed_at DESC);
                """
            )
            self._run_migrations(connection)
            try:
                connection.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS message_search USING fts5(
                        message_id UNINDEXED,
                        session_id UNINDEXED,
                        author_model,
                        content,
                        tokenize='trigram'
                    );
                    CREATE TRIGGER IF NOT EXISTS messages_search_insert
                    AFTER INSERT ON messages BEGIN
                        INSERT INTO message_search(message_id, session_id, author_model, content)
                        VALUES (new.id, new.session_id, new.author_model, new.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS messages_search_delete
                    AFTER DELETE ON messages BEGIN
                        DELETE FROM message_search WHERE message_id = old.id;
                    END;
                    CREATE TRIGGER IF NOT EXISTS messages_search_update
                    AFTER UPDATE OF author_model, content ON messages BEGIN
                        DELETE FROM message_search WHERE message_id = old.id;
                        INSERT INTO message_search(message_id, session_id, author_model, content)
                        VALUES (new.id, new.session_id, new.author_model, new.content);
                    END;
                    """
                )
                connection.execute(
                    """
                    INSERT INTO message_search(message_id, session_id, author_model, content)
                    SELECT m.id, m.session_id, m.author_model, m.content
                    FROM messages m
                    WHERE NOT EXISTS (
                        SELECT 1 FROM message_search f WHERE f.message_id = m.id
                    )
                    """
                )
                connection.execute(
                    """
                    DELETE FROM message_search
                    WHERE NOT EXISTS (
                        SELECT 1 FROM messages m WHERE m.id = message_search.message_id
                    )
                    """
                )
            except sqlite3.OperationalError:
                # Older system SQLite builds may omit FTS5 or its trigram
                # tokenizer. Chat persistence must still start; search falls
                # back to an indexed-session/LIKE query below.
                pass
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check is None or str(quick_check[0]).lower() != "ok":
                detail = quick_check[0] if quick_check else "no result"
                raise RuntimeError(f"数据库迁移后的完整性检查失败: {detail}")
