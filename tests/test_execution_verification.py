from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.agent_run_service import AgentRunService  # noqa: E402
from app.services.chat_database import ChatDatabase  # noqa: E402
from app.services.execution_verification import (  # noqa: E402
    ExecutionVerificationService,
    execution_verification_service,
)
from app.services.execution_verification_eval import (  # noqa: E402
    assert_no_error_green,
    evaluate_declarations,
    load_declaration_cases,
)

FIXTURE_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "execution_verification_cases.json"
)
_META, VERIFICATION_CASES = load_declaration_cases(FIXTURE_PATH)


def _digest(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    "case",
    VERIFICATION_CASES,
    ids=[str(item["id"]) for item in VERIFICATION_CASES],
)
def test_execution_verification_characterization_fixture(case: dict[str, Any]):
    status, evidence = execution_verification_service.verify_effect(
        tool_call=case["tool_call"],
        payload=case["payload"],
        tool_run_id=case.get("tool_run_id"),
        digest=_digest,
    )

    assert status == case["expected_status"]
    assert [item["kind"] for item in evidence] == case["expected_evidence_kinds"]
    if evidence:
        assert evidence[0] == {
            "kind": "tool_result_digest",
            "sha256": _digest(case["payload"]),
            "tool_run_id": case.get("tool_run_id"),
        }
    if expected_path := case.get("expected_path"):
        assert any(item.get("path") == expected_path for item in evidence)


def test_declaration_suite_has_one_hundred_versioned_cases():
    assert _META.get("schema_version") == 1
    assert _META.get("count") == 100
    assert len(VERIFICATION_CASES) == 100
    ids = [item["id"] for item in VERIFICATION_CASES]
    assert len(ids) == len(set(ids))
    # Original seed ids remain stable.
    for seed in (
        "read_only_not_applicable",
        "failed_side_effect",
        "unknown_success_unverified",
        "trusted_browser_receipt",
        "untrusted_browser_receipt",
        "artifact_version_verified",
        "file_digest_verified",
        "invalid_file_digest_unverified",
    ):
        assert seed in ids


def test_declaration_eval_runner_reports_zero_error_green_and_full_match():
    report = evaluate_declarations(fixture_path=FIXTURE_PATH, digest=_digest)
    payload = report.to_dict()

    assert payload["summary"]["total"] == 100
    assert payload["summary"]["matched"] == 100
    assert payload["summary"]["mismatches"] == 0
    assert payload["summary"]["false_verified_error_green"] == 0
    assert payload["summary"]["false_reject"] == 0
    assert payload["summary"]["pass"] is True
    assert_no_error_green(report)

    # Expected diagonal of the status matrix is fully populated for seed classes.
    matrix = payload["status_matrix"]
    for status in ("not_applicable", "failed", "unverified", "verified"):
        assert matrix.get(status, {}).get(status, 0) >= 1


def test_error_green_gate_detects_false_verified(monkeypatch):
    """If the verifier wrongly returns verified, the gate must fail."""

    class AlwaysVerified(ExecutionVerificationService):
        @staticmethod
        def verify_effect(**kwargs):
            return "verified", [{"kind": "tool_result_digest"}]

    bad = evaluate_declarations(
        cases=[
            {
                "id": "should_fail_but_green",
                "category": "failed",
                "polarity": "negative",
                "tool_call": {"side_effect": True, "tool_name": "remote__x"},
                "payload": {"status": "error"},
                "expected_status": "failed",
                "expected_evidence_kinds": ["tool_result_digest"],
            }
        ],
        verifier=AlwaysVerified(),
        digest=_digest,
    )
    assert bad.false_verified == 1
    assert bad.matched is False or bad.mismatches == 1
    with pytest.raises(AssertionError, match="错误绿色成功数必须为 0"):
        assert_no_error_green(bad)


def test_agent_run_service_keeps_the_legacy_verification_entrypoint():
    case = next(
        item
        for item in VERIFICATION_CASES
        if item["id"] == "trusted_browser_receipt"
    )

    legacy_result = AgentRunService._effect_verification(
        tool_call=case["tool_call"],
        payload=case["payload"],
        tool_run_id=case.get("tool_run_id"),
    )
    service_result = execution_verification_service.verify_effect(
        tool_call=case["tool_call"],
        payload=case["payload"],
        tool_run_id=case.get("tool_run_id"),
        digest=AgentRunService._digest,
    )

    assert legacy_result == service_result


def test_agent_run_service_uses_an_injected_verification_boundary(tmp_path):
    class RecordingVerificationService(ExecutionVerificationService):
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        def verify_effect(self, **kwargs):
            self.calls.append(kwargs)
            return "unverified", [{"kind": "injected_verifier"}]

    database = ChatDatabase(tmp_path / "injected-verifier.sqlite3")
    verifier = RecordingVerificationService()
    service = AgentRunService(
        database=database,
        verification_service=verifier,
    )
    session_id = "11111111-1111-4111-8111-111111111111"
    run = service.create(session_id, {
        "content": "验证核验服务注入",
        "targets": ["coder"],
        "mode": "project",
        "execution_strategy": "direct",
    })
    call = service.begin_tool_call(
        run_id=run["id"],
        session_id=session_id,
        model="coder",
        provider_type="mcp",
        provider_model="remote",
        provider_call_id="injected-verifier-call",
        tool_name="remote__send_message",
        arguments={"message": "hello"},
        round_index=0,
        call_index=0,
    )
    result = service.finish_tool_call(
        call["id"],
        {"status": "success", "result": {"ok": True}},
        tool_run_id="injected-run",
    )
    assert verifier.calls
    assert result is not None
    assert result["verification_status"] == "unverified"
