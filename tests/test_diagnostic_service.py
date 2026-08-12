from __future__ import annotations

import atexit
import io
import json
import logging
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="aetherchat-diagnostic-tests-"))
os.environ.setdefault("AETHERCHAT_DATA_DIR", str(TEST_DATA_DIR))

@atexit.register
def _cleanup_test_data() -> None:
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


from app.services.diagnostic_service import DiagnosticService  # noqa: E402
from app.services.structured_logging import (  # noqa: E402
    JsonLineFormatter,
    configure_structured_logging,
    diagnostic_ref,
    read_recent_log_entries,
)


class ReadyDatabase:
    def readiness_probe(self) -> dict[str, object]:
        return {
            "status": "ready",
            "schema_version": 3,
            "path": "/home/private/aetherchat.sqlite3",
        }


def test_json_log_formatter_redacts_credentials_paths_queries_and_tracebacks() -> None:
    try:
        raise RuntimeError("exception contains super-secret-value")
    except RuntimeError:
        exception = sys.exc_info()

    record = logging.LogRecord(
        name="app.tests",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=(
            "Bearer abc.def api_key=super-secret-value token=plain-secret "
            "client_secret=client-secret-value "
            "https://localhost/run?prompt=private /home/alice/private.txt"
        ),
        args=(),
        exc_info=exception,
    )
    record.event = "request_failed"
    record.request_id = "diagnostic-request-42"

    payload = json.loads(JsonLineFormatter().format(record))
    encoded = json.dumps(payload)
    assert "abc.def" not in encoded
    assert "super-secret-value" not in encoded
    assert "plain-secret" not in encoded
    assert "client-secret-value" not in encoded
    assert "prompt=private" not in encoded
    assert "/home/alice" not in encoded
    assert payload["exception_type"] == "RuntimeError"
    assert payload["request_id"] == "diagnostic-request-42"
    assert payload["request_ref"] == diagnostic_ref(
        "request",
        "diagnostic-request-42",
    )
    assert "Traceback" not in encoded


def test_recent_log_reader_drops_unknown_fields_and_resanitizes(tmp_path: Path) -> None:
    path = tmp_path / "aetherchat.jsonl"
    path.write_text(
        json.dumps({
            "timestamp": "2026-08-11T00:00:00Z",
            "level": "INFO",
            "message": "password=hunter2 /home/alice/report.txt",
            "prompt": "full private prompt",
            "api_key": "should-never-export",
        })
        + "\nnot-json\n",
        encoding="utf-8",
    )

    entries = read_recent_log_entries(path)
    assert len(entries) == 1
    encoded = json.dumps(entries)
    assert "hunter2" not in encoded
    assert "/home/alice" not in encoded
    assert "full private prompt" not in encoded
    assert "should-never-export" not in encoded
    assert "prompt" not in entries[0]
    assert "api_key" not in entries[0]


def test_recent_log_reader_refuses_symbolic_links(tmp_path: Path) -> None:
    private_log = tmp_path / "private.jsonl"
    private_log.write_text(
        json.dumps({"level": "INFO", "message": "must-not-follow"}) + "\n",
        encoding="utf-8",
    )
    linked_log = tmp_path / "aetherchat.jsonl"
    linked_log.symlink_to(private_log)

    assert read_recent_log_entries(linked_log) == []


def test_structured_log_file_is_private_and_uses_json_lines(tmp_path: Path) -> None:
    logger_name = f"app.diagnostic-test.{tmp_path.name}"
    path = configure_structured_logging(tmp_path, logger_name=logger_name)
    assert path is not None

    logger = logging.getLogger(logger_name)
    logger.info("token=never-write-this", extra={"event": "test_event"})
    for handler in list(logger.handlers):
        handler.flush()
        logger.removeHandler(handler)
        handler.close()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["event"] == "test_event"
    assert "never-write-this" not in json.dumps(payload)


def test_diagnostic_bundle_uses_allowlists_and_aggregates_provider_resources(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "aetherchat.jsonl"
    log_path.write_text(
        json.dumps({
            "timestamp": "2026-08-11T00:00:00Z",
            "level": "WARNING",
            "message": "secret=do-not-export /home/alice/file.md",
            "request_body": "full prompt",
        })
        + "\n",
        encoding="utf-8",
    )
    service = DiagnosticService(
        ReadyDatabase(),
        log_path=log_path,
        now=lambda: datetime(2026, 8, 11, 0, 0, tzinfo=UTC),
    )
    automation = {
        "scheduler_running": True,
        "active_workers": 1,
        "worker_id": "private-worker-id",
        "api_key": "private-key",
    }
    dispatcher = {
        "accepting": True,
        "capacity": 2,
        "resources": {
            "provider:secret-provider-name": {"capacity": 1, "running": 1, "queued": 2},
            "gpu": {"capacity": 1, "running": 1, "queued": 0},
        },
    }

    snapshot = service.snapshot(
        automation=automation,
        run_dispatcher=dispatcher,
    )
    assert snapshot["schema"] == "aetherchat-diagnostics-v1"
    assert snapshot["database"] == {
        "status": "ready",
        "schema_version": 3,
        "expected_schema_version": 3,
    }
    assert snapshot["runtime"]["run_dispatcher"]["resource_summary"] == {
        "provider_slots": 1,
        "provider_running": 1,
        "provider_queued": 2,
        "gpu_running": 1,
        "gpu_queued": 0,
    }
    assert "worker_id" not in snapshot["runtime"]["automation"]

    bundle = service.bundle(
        automation=automation,
        run_dispatcher=dispatcher,
    )
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert set(archive.namelist()) == {
            "README.txt",
            "diagnostics.json",
            "recent-logs.jsonl",
            "run-telemetry.json",
        }
        contents = b"\n".join(archive.read(name) for name in archive.namelist())
        telemetry = json.loads(archive.read("run-telemetry.json"))
    assert telemetry == {
        "schema": "aetherchat-run-telemetry-v1",
        "status": "unavailable",
        "error_code": "telemetry_not_configured",
    }
    for forbidden in (
        b"do-not-export",
        b"/home/alice",
        b"full prompt",
        b"secret-provider-name",
        b"private-worker-id",
        b"private-key",
    ):
        assert forbidden not in contents
    assert service.bundle_filename() == "aetherchat-diagnostics-20260811T000000Z.zip"
