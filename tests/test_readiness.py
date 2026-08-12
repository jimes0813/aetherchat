from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from typing import Any

import pytest
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="aetherchat-readiness-tests-"))
if "app.services.chat_database" not in sys.modules:
    os.environ["AETHERCHAT_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ.setdefault("AETHERCHAT_ALLOW_UNAUTHENTICATED", "1")


@atexit.register
def _cleanup_test_data() -> None:
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


from app import main as main_module  # noqa: E402
from app.main import app  # noqa: E402
from app.services.chat_database import (  # noqa: E402
    CURRENT_SCHEMA_VERSION,
    ChatDatabase,
)
from app.services.readiness_service import ReadinessService  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_rate_limit_store():
    main_module.rate_limit_store.clear()
    yield
    main_module.rate_limit_store.clear()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_database_readiness_probe_checks_schema_and_core_read(tmp_path):
    database = ChatDatabase(tmp_path / "ready.sqlite3")

    assert database.readiness_probe() == {
        "status": "ready",
        "schema_version": CURRENT_SCHEMA_VERSION,
        "expected_schema_version": CURRENT_SCHEMA_VERSION,
    }

    with sqlite3.connect(database.path) as connection:
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION - 1}")

    assert database.readiness_probe() == {
        "status": "schema_mismatch",
        "schema_version": CURRENT_SCHEMA_VERSION - 1,
        "expected_schema_version": CURRENT_SCHEMA_VERSION,
    }


def test_database_readiness_probe_detects_missing_core_table(tmp_path):
    database = ChatDatabase(tmp_path / "missing-table.sqlite3")
    with sqlite3.connect(database.path) as connection:
        connection.execute("DROP TABLE sessions")

    probe = database.readiness_probe()

    assert probe["status"] == "schema_mismatch"
    assert probe["schema_version"] == CURRENT_SCHEMA_VERSION
    assert probe["missing_tables"] == ["sessions"]


def test_liveness_endpoint_does_not_query_database(client, monkeypatch):
    def fail_if_called():
        raise AssertionError("liveness must not query database diagnostics")

    monkeypatch.setattr(main_module.automation_service, "status", fail_if_called)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["automation"]["scheduler_running"] is True


def test_readiness_endpoint_reports_optional_dependencies_without_blocking(
    client,
    monkeypatch,
    tmp_path,
):
    service = ReadinessService(ChatDatabase(tmp_path / "endpoint-ready.sqlite3"))
    monkeypatch.setattr(main_module, "readiness_service", service)
    monkeypatch.setattr(main_module, "redis_client", None)

    response = client.get(
        "/health/ready",
        headers={"X-Request-ID": "ready-check-2026"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "ready-check-2026"
    assert response.json() == {
        "status": "ready",
        "ready": True,
        "service": "aetherchat",
        "checks": {
            "database": {
                "status": "ready",
                "schema_version": CURRENT_SCHEMA_VERSION,
                "expected_schema_version": CURRENT_SCHEMA_VERSION,
            }
        },
        "optional_dependencies": {
            "redis": {"status": "unavailable", "required": False},
            "automation": {"status": "ready", "required": False},
            "comfyui": {"status": "not_probed", "required": False},
            "local_models": {"status": "not_probed", "required": False},
        },
        "request_id": "ready-check-2026",
    }


def test_readiness_endpoint_returns_stable_schema_error(client, monkeypatch, tmp_path):
    database = ChatDatabase(tmp_path / "schema-mismatch.sqlite3")
    with sqlite3.connect(database.path) as connection:
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION - 1}")
    monkeypatch.setattr(main_module, "readiness_service", ReadinessService(database))

    response = client.get(
        "/health/ready",
        headers={"X-Request-ID": "schema-mismatch-2026"},
    )

    assert response.status_code == 503
    assert response.headers["X-Request-ID"] == "schema-mismatch-2026"
    assert response.json()["error"] == {
        "code": "database_schema_mismatch",
        "message": "Core database schema is incompatible",
    }
    assert response.json()["checks"]["database"] == {
        "status": "schema_mismatch",
        "schema_version": CURRENT_SCHEMA_VERSION - 1,
        "expected_schema_version": CURRENT_SCHEMA_VERSION,
    }


def test_readiness_endpoint_hides_database_exception_details(client, monkeypatch):
    class BrokenDatabase:
        def readiness_probe(self) -> dict[str, Any]:
            raise sqlite3.OperationalError(
                "unable to open database file /private/aetherchat.sqlite3"
            )

    monkeypatch.setattr(
        main_module,
        "readiness_service",
        ReadinessService(BrokenDatabase()),
    )

    response = client.get(
        "/health/ready",
        headers={"X-Request-ID": "database-error-2026"},
    )

    assert response.status_code == 503
    assert response.json()["request_id"] == "database-error-2026"
    assert response.json()["error"] == {
        "code": "database_unavailable",
        "message": "Core database is unavailable",
    }
    assert "/private" not in response.text
    assert "unable to open" not in response.text


def test_readiness_endpoint_is_exempt_from_rate_limiting(
    client,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        main_module,
        "readiness_service",
        ReadinessService(ChatDatabase(tmp_path / "rate-limit.sqlite3")),
    )
    monkeypatch.setattr(main_module, "RATE_LIMIT", 1)

    for _ in range(3):
        assert client.get("/health/ready").status_code == 200
    assert not main_module.rate_limit_store

    assert client.get("/models/").status_code == 200
    limited = client.get("/models/")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
