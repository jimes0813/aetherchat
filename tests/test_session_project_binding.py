"""Batch 46: session/run governance project_id binding."""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))


@pytest.fixture()
def db_and_gov(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(data_dir))
    # Fresh modules against the temp data dir.
    from app.services.chat_database import CURRENT_SCHEMA_VERSION, ChatDatabase
    from app.services.platform_governance import PlatformGovernanceService

    gov = PlatformGovernanceService()
    database = ChatDatabase(path=data_dir / "aetherchat.sqlite3")
    assert CURRENT_SCHEMA_VERSION >= 4
    return database, gov


def test_create_session_binds_default_project(db_and_gov):
    database, gov = db_and_gov
    session_id = str(uuid4())
    row = database.create_session(
        session_id,
        "绑定测试",
        "project",
        project_id=gov.state.get("default_project_id") or "default",
    )
    assert row.get("project_id") == (gov.state.get("default_project_id") or "default")


def test_set_session_project_and_agent_run_inherits(db_and_gov):
    database, gov = db_and_gov
    from app.services.platform_governance import ProjectCreate

    created = gov.create_project(ProjectCreate(name="会话项目", slug="sess-proj"))
    session_id = str(uuid4())
    database.create_session(session_id, "Run 继承", "project", project_id=None)
    updated = database.set_session_project(session_id, created["id"])
    assert updated is not None
    assert updated["project_id"] == created["id"]

    run = database.create_agent_run(
        session_id,
        {"content": "hello", "mode": "project"},
        targets=["kimi"],
    )
    assert run.get("project_id") == created["id"]


def test_resolve_project_id_from_session(db_and_gov, monkeypatch):
    database, gov = db_and_gov
    session_id = str(uuid4())
    database.create_session(session_id, "解析", "casual", project_id="default")

    # Point global chat_database used by resolve_project_id at our temp DB.
    import app.services.chat_database as chat_db_mod
    import app.services.platform_governance as gov_mod

    monkeypatch.setattr(chat_db_mod, "chat_database", database)
    monkeypatch.setattr(gov_mod, "chat_database", database, raising=False)

    resolved = gov.resolve_project_id(session_id=session_id)
    assert resolved == "default"

    override = gov.resolve_project_id(project_id="explicit", session_id=session_id)
    assert override == "explicit"


def test_project_policy_gate_via_session(db_and_gov, monkeypatch):
    database, gov = db_and_gov
    import app.services.chat_database as chat_db_mod
    from app.services.platform_governance import PolicyPatch, ProjectCreate

    monkeypatch.setattr(chat_db_mod, "chat_database", database)

    project = gov.create_project(ProjectCreate(name="门禁项目", slug="gate-proj"))
    gov.patch_project_policy(
        project["id"],
        PolicyPatch(enforce_tool_allowlist=True, allowed_tools=["write_artifact"]),
    )
    session_id = str(uuid4())
    database.create_session(session_id, "门禁", "project", project_id=project["id"])

    gov.require_tool_invoke("write_artifact", session_id=session_id)
    with pytest.raises(PermissionError, match="白名单"):
        gov.require_tool_invoke("run_command", session_id=session_id)


def test_schema_migration_adds_project_id_column(tmp_path, monkeypatch):
    """Fresh DB should expose project_id on sessions and agent_runs."""
    data_dir = tmp_path / "mig"
    data_dir.mkdir()
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(data_dir))
    from app.services.chat_database import CURRENT_SCHEMA_VERSION, ChatDatabase

    database = ChatDatabase(path=data_dir / "aetherchat.sqlite3")
    assert CURRENT_SCHEMA_VERSION >= 4
    session = database.create_session(str(uuid4()), "mig", "project", project_id="default")
    assert "project_id" in session
    with database._connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        session_cols = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
        run_cols = {row[1] for row in connection.execute("PRAGMA table_info(agent_runs)")}
    assert version == CURRENT_SCHEMA_VERSION
    assert "project_id" in session_cols
    assert "project_id" in run_cols


def test_backfill_binds_legacy_null_project_ids(tmp_path, monkeypatch):
    data_dir = tmp_path / "backfill"
    data_dir.mkdir()
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(data_dir))
    from app.services.chat_database import ChatDatabase

    database = ChatDatabase(path=data_dir / "aetherchat.sqlite3")
    unbound_session = str(uuid4())
    bound_session = str(uuid4())
    database.create_session(unbound_session, "历史会话", "project", project_id=None)
    database.create_session(bound_session, "已绑定", "project", project_id="keep-me")
    with database._connect() as connection:
        connection.execute(
            "UPDATE sessions SET project_id = NULL WHERE id = ?",
            (unbound_session,),
        )
        connection.execute(
            """INSERT INTO agent_runs
               (id, session_id, status, mode, project_id, input_json, targets_json, created_at, updated_at)
               VALUES (?, ?, 'completed', 'project', NULL, '{}', '[]', '2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z')""",
            (str(uuid4()), unbound_session),
        )
        connection.commit()

    result = database.backfill_unbound_projects("default")
    assert result["sessions"] >= 1
    rebound = database.get_session(unbound_session)
    kept = database.get_session(bound_session)
    assert rebound is not None and rebound["project_id"] == "default"
    assert kept is not None and kept["project_id"] == "keep-me"
    with database._connect() as connection:
        run_ids = connection.execute(
            "SELECT project_id FROM agent_runs WHERE session_id = ?",
            (unbound_session,),
        ).fetchall()
    assert run_ids
    assert all(row[0] == "default" for row in run_ids)
