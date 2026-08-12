"""Deterministic execution-declaration evaluation runner.

Loads the versioned fixture set, classifies each case with
``ExecutionVerificationService``, and reports:

- status matrix
- false-verified count (错误绿色成功)
- false-reject count
- mismatches and wall time

No paid providers, network, or database access.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .execution_verification import (
    DigestFunction,
    ExecutionVerificationService,
    execution_verification_service,
)

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "execution_verification_cases.json"
)


def canonical_digest(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_declaration_cases(path: Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fixture_path = path or DEFAULT_FIXTURE
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        meta = {
            "schema_version": 0,
            "name": "legacy_list",
            "count": len(raw),
            "path": str(fixture_path),
        }
        return meta, list(raw)
    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
        raise ValueError(f"Unsupported execution verification fixture shape: {fixture_path}")
    meta = {
        "schema_version": raw.get("schema_version"),
        "name": raw.get("name"),
        "count": raw.get("count", len(raw["cases"])),
        "description": raw.get("description"),
        "error_green_definition": raw.get("error_green_definition"),
        "path": str(fixture_path),
    }
    return meta, list(raw["cases"])


@dataclass
class CaseResult:
    id: str
    category: str
    polarity: str
    expected_status: str
    actual_status: str
    expected_evidence_kinds: list[str]
    actual_evidence_kinds: list[str]
    matched: bool
    false_verified: bool
    false_reject: bool
    detail: str = ""


@dataclass
class EvaluationReport:
    meta: dict[str, Any]
    results: list[CaseResult] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def matched(self) -> int:
        return sum(1 for item in self.results if item.matched)

    @property
    def mismatches(self) -> int:
        return self.total - self.matched

    @property
    def false_verified(self) -> int:
        """错误绿色成功：系统给出 verified，但样本期望并非 verified。"""
        return sum(1 for item in self.results if item.false_verified)

    @property
    def false_reject(self) -> int:
        return sum(1 for item in self.results if item.false_reject)

    def status_matrix(self) -> dict[str, dict[str, int]]:
        matrix: dict[str, Counter[str]] = {}
        for item in self.results:
            row = matrix.setdefault(item.expected_status, Counter())
            row[item.actual_status] += 1
        return {expected: dict(actuals) for expected, actuals in matrix.items()}

    def category_summary(self) -> dict[str, dict[str, int]]:
        summary: dict[str, Counter[str]] = {}
        for item in self.results:
            row = summary.setdefault(item.category or "unknown", Counter())
            row["total"] += 1
            if item.matched:
                row["matched"] += 1
            if item.false_verified:
                row["false_verified"] += 1
            if item.false_reject:
                row["false_reject"] += 1
        return {key: dict(value) for key, value in summary.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "meta": self.meta,
            "summary": {
                "total": self.total,
                "matched": self.matched,
                "mismatches": self.mismatches,
                "false_verified_error_green": self.false_verified,
                "false_reject": self.false_reject,
                "duration_ms": round(self.duration_ms, 3),
                "pass": self.mismatches == 0 and self.false_verified == 0,
            },
            "status_matrix": self.status_matrix(),
            "category_summary": self.category_summary(),
            "mismatched_ids": [item.id for item in self.results if not item.matched],
            "false_verified_ids": [item.id for item in self.results if item.false_verified],
            "false_reject_ids": [item.id for item in self.results if item.false_reject],
        }


def evaluate_declarations(
    *,
    cases: Sequence[Mapping[str, Any]] | None = None,
    fixture_path: Path | None = None,
    verifier: ExecutionVerificationService | None = None,
    digest: DigestFunction | None = None,
) -> EvaluationReport:
    meta: dict[str, Any]
    loaded_cases: list[dict[str, Any]]
    if cases is None:
        meta, loaded_cases = load_declaration_cases(fixture_path)
    else:
        meta = {
            "schema_version": None,
            "name": "inline",
            "count": len(cases),
            "path": str(fixture_path) if fixture_path else None,
        }
        loaded_cases = [dict(item) for item in cases]

    service = verifier or execution_verification_service
    digest_fn: DigestFunction = digest or canonical_digest
    started = time.perf_counter()
    results: list[CaseResult] = []

    for case in loaded_cases:
        case_id = str(case.get("id") or "")
        expected_status = str(case.get("expected_status") or "")
        expected_kinds = [str(item) for item in (case.get("expected_evidence_kinds") or [])]
        actual_status, evidence = service.verify_effect(
            tool_call=dict(case.get("tool_call") or {}),
            payload=dict(case.get("payload") or {}),
            tool_run_id=case.get("tool_run_id"),
            digest=digest_fn,
        )
        actual_kinds = [str(item.get("kind") or "") for item in evidence]
        detail_parts: list[str] = []
        matched = actual_status == expected_status and actual_kinds == expected_kinds
        if expected_path := case.get("expected_path"):
            path_ok = any(item.get("path") == expected_path for item in evidence)
            matched = matched and path_ok
            if not path_ok:
                detail_parts.append(f"missing expected_path={expected_path}")
        if actual_status != expected_status:
            detail_parts.append(f"status {actual_status!r} != {expected_status!r}")
        if actual_kinds != expected_kinds:
            detail_parts.append(f"evidence {actual_kinds!r} != {expected_kinds!r}")

        false_verified = actual_status == "verified" and expected_status != "verified"
        false_reject = expected_status == "verified" and actual_status != "verified"
        results.append(
            CaseResult(
                id=case_id,
                category=str(case.get("category") or "unknown"),
                polarity=str(case.get("polarity") or "unknown"),
                expected_status=expected_status,
                actual_status=str(actual_status),
                expected_evidence_kinds=expected_kinds,
                actual_evidence_kinds=actual_kinds,
                matched=matched,
                false_verified=false_verified,
                false_reject=false_reject,
                detail="; ".join(detail_parts),
            )
        )

    duration_ms = (time.perf_counter() - started) * 1000.0
    return EvaluationReport(meta=meta, results=results, duration_ms=duration_ms)


def assert_no_error_green(report: EvaluationReport) -> None:
    if report.false_verified:
        ids = [item.id for item in report.results if item.false_verified]
        raise AssertionError(
            "错误绿色成功数必须为 0，但发现 "
            f"{report.false_verified} 条：{ids[:20]}"
        )
