"""Deterministic Agent benchmark evaluation runner.

Covers pure domain contracts used by Agent runs: execution strategy, effect
verification, run creation, admission resources, event envelopes, and tool
lifecycle. No paid providers, network, or live model calls.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models.schemas import ProviderConfig
from app.services.agent_run_service import AgentRunService
from app.services.chat_database import ChatDatabase
from app.services.event_envelope import EVENT_CONTRACT_VERSION, EventEnvelopeBuilder
from app.services.execution_strategy import resolve_execution_strategy
from app.services.execution_verification import execution_verification_service
from app.services.run_admission import admission_resources_for_action, provider_resource

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "agent_benchmark_cases.json"
)

FIXED_PROVIDER_CONFIGS = (
    ProviderConfig(
        name="kimi",
        type="openai_compat",
        model="kimi-k3",
        api_key_ref="KIMI_API_KEY",
    ),
    ProviderConfig(
        name="Qwen",
        type="local",
        model="qwen.gguf",
        api_key_ref="MODEL_KEY_QWEN",
        local_runtime="llama_cpp",
    ),
)


def _digest(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_benchmark_cases(path: Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fixture_path = path or DEFAULT_FIXTURE
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
        raise ValueError(f"Unsupported agent benchmark fixture: {fixture_path}")
    meta = {
        "schema_version": raw.get("schema_version"),
        "name": raw.get("name"),
        "count": raw.get("count", len(raw["cases"])),
        "min_categories": raw.get("min_categories"),
        "min_per_category": raw.get("min_per_category"),
        "description": raw.get("description"),
        "path": str(fixture_path),
    }
    return meta, list(raw["cases"])


@dataclass
class CaseResult:
    id: str
    category: str
    kind: str
    polarity: str
    matched: bool
    detail: str = ""


@dataclass
class BenchmarkReport:
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

    def category_summary(self) -> dict[str, dict[str, int]]:
        summary: dict[str, Counter[str]] = {}
        for item in self.results:
            row = summary.setdefault(item.category or "unknown", Counter())
            row["total"] += 1
            if item.matched:
                row["matched"] += 1
        return {key: dict(value) for key, value in summary.items()}

    def to_dict(self) -> dict[str, Any]:
        categories = self.category_summary()
        return {
            "meta": self.meta,
            "summary": {
                "total": self.total,
                "matched": self.matched,
                "mismatches": self.mismatches,
                "category_count": len(categories),
                "min_per_category_ok": all(
                    value.get("total", 0) >= int(self.meta.get("min_per_category") or 3)
                    for value in categories.values()
                ),
                "duration_ms": round(self.duration_ms, 3),
                "pass": self.mismatches == 0
                and len(categories) >= int(self.meta.get("min_categories") or 20),
            },
            "category_summary": categories,
            "mismatched_ids": [item.id for item in self.results if not item.matched],
            "mismatch_details": [
                {"id": item.id, "detail": item.detail}
                for item in self.results
                if not item.matched
            ],
        }


def _contains(text: str, needle: str) -> bool:
    return needle in text


def _run_execution_strategy(case_input: Mapping[str, Any], expect: Mapping[str, Any]) -> tuple[bool, str]:
    action = dict(case_input.get("action") or {})
    if expect.get("ok", True):
        resolved = resolve_execution_strategy(action)
        checks = [
            (resolved.get("mode") == expect.get("mode"), f"mode {resolved.get('mode')!r}"),
            (resolved.get("effective") == expect.get("effective"), f"effective {resolved.get('effective')!r}"),
            (resolved.get("source") == expect.get("source"), f"source {resolved.get('source')!r}"),
            (resolved.get("requested") == expect.get("requested"), f"requested {resolved.get('requested')!r}"),
        ]
        for ok, detail in checks:
            if not ok:
                return False, detail
        return True, ""
    try:
        resolve_execution_strategy(action)
    except Exception as exc:  # noqa: BLE001 - benchmark expects rejection
        needle = str(expect.get("error_contains") or "")
        if needle and not _contains(str(exc), needle):
            return False, f"error {exc!r} missing {needle!r}"
        return True, ""
    return False, "expected rejection"


def _run_effect_verification(case_input: Mapping[str, Any], expect: Mapping[str, Any]) -> tuple[bool, str]:
    status, evidence = execution_verification_service.verify_effect(
        tool_call=dict(case_input.get("tool_call") or {}),
        payload=dict(case_input.get("payload") or {}),
        tool_run_id=case_input.get("tool_run_id"),
        digest=_digest,
    )
    kinds = [str(item.get("kind") or "") for item in evidence]
    if status != expect.get("status"):
        return False, f"status {status!r} != {expect.get('status')!r}"
    if kinds != list(expect.get("evidence_kinds") or []):
        return False, f"evidence {kinds!r} != {expect.get('evidence_kinds')!r}"
    expected_path = expect.get("path")
    if expected_path and not any(item.get("path") == expected_path for item in evidence):
        return False, f"missing path {expected_path!r}"
    return True, ""


def _run_create(case_input: Mapping[str, Any], expect: Mapping[str, Any]) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="aether-agent-bench-") as tmp:
        database = ChatDatabase(Path(tmp) / "bench.sqlite3")
        service = AgentRunService(database=database)
        session_id = str(case_input.get("session_id") or "11111111-1111-4111-8111-111111111111")
        action = dict(case_input.get("action") or {})
        if expect.get("ok", True):
            run = service.create(session_id, action)
            if str(run.get("status")) != str(expect.get("status")):
                return False, f"run status {run.get('status')!r}"
            if expect.get("effective") and run.get("effective_execution_strategy") != expect.get("effective"):
                return False, f"run effective {run.get('effective_execution_strategy')!r}"
            if expect.get("source") and run.get("execution_strategy_source") != expect.get("source"):
                return False, f"run source {run.get('execution_strategy_source')!r}"
            events = database.list_agent_events(run["id"], limit=50)
            types = [str(item.get("event_type") or item.get("type") or "") for item in events]
            for needed in expect.get("events_contain") or []:
                if needed not in types:
                    return False, f"missing event {needed!r} in {types}"
            return True, ""
        try:
            service.create(session_id, action)
        except Exception as exc:  # noqa: BLE001
            needle = str(expect.get("error_contains") or "")
            if needle and not _contains(str(exc), needle):
                return False, f"error {exc!r} missing {needle!r}"
            return True, ""
        return False, "expected create rejection"


def _run_admission_provider(case_input: Mapping[str, Any], expect: Mapping[str, Any]) -> tuple[bool, str]:
    resource = provider_resource(str(case_input.get("name") or ""))
    if resource != expect.get("resource"):
        return False, f"resource {resource!r} != {expect.get('resource')!r}"
    return True, ""


def _run_admission_resources(case_input: Mapping[str, Any], expect: Mapping[str, Any]) -> tuple[bool, str]:
    resources = set(
        admission_resources_for_action(
            dict(case_input.get("action") or {}),
            FIXED_PROVIDER_CONFIGS,
        )
    )
    must = set(expect.get("must_include") or [])
    if not must.issubset(resources):
        return False, f"resources {sorted(resources)!r} missing {sorted(must - resources)!r}"
    return True, ""


def _run_event_envelope(case_input: Mapping[str, Any], expect: Mapping[str, Any]) -> tuple[bool, str]:
    builder = EventEnvelopeBuilder(
        str(case_input.get("session_id") or "sess"),
        event_id_factory=lambda: "fixed-event-id",
    )
    envelope = builder.build(dict(case_input.get("payload") or {}))
    if envelope.get("contract_version") != expect.get("contract_version", EVENT_CONTRACT_VERSION):
        return False, f"contract_version {envelope.get('contract_version')!r}"
    if expect.get("has_event_id") and not envelope.get("event_id"):
        return False, "missing event_id"
    if expect.get("sequence_positive") and int(envelope.get("sequence") or 0) < 1:
        return False, f"sequence {envelope.get('sequence')!r}"
    if expect.get("session_id") and envelope.get("session_id") != expect.get("session_id"):
        return False, f"session_id {envelope.get('session_id')!r}"
    return True, ""


def _run_tool_lifecycle(case_input: Mapping[str, Any], expect: Mapping[str, Any]) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="aether-agent-tool-") as tmp:
        database = ChatDatabase(Path(tmp) / "tool.sqlite3")
        service = AgentRunService(database=database)
        session_id = str(case_input.get("session_id") or "11111111-1111-4111-8111-111111111111")
        run = service.create(session_id, dict(case_input.get("action") or {}))
        call = service.begin_tool_call(
            run_id=run["id"],
            session_id=session_id,
            model="coder",
            provider_type="mcp",
            provider_model="remote",
            provider_call_id=f"bench-{case_input.get('tool_name')}",
            tool_name=str(case_input.get("tool_name") or "remote__x"),
            arguments={"x": 1},
            round_index=0,
            call_index=0,
        )
        finished = service.finish_tool_call(
            call["id"],
            dict(case_input.get("payload") or {}),
            tool_run_id="bench-tool-run",
        )
        if finished is None:
            return False, "finish_tool_call returned None"
        status = finished.get("verification_status")
        if status != expect.get("verification_status"):
            return False, f"verification_status {status!r}"
        return True, ""


_HANDLERS = {
    "execution_strategy": _run_execution_strategy,
    "effect_verification": _run_effect_verification,
    "run_create": _run_create,
    "admission_provider": _run_admission_provider,
    "admission_resources": _run_admission_resources,
    "event_envelope": _run_event_envelope,
    "tool_lifecycle": _run_tool_lifecycle,
}


def evaluate_agent_benchmarks(
    *,
    cases: Sequence[Mapping[str, Any]] | None = None,
    fixture_path: Path | None = None,
) -> BenchmarkReport:
    if cases is None:
        meta, loaded = load_benchmark_cases(fixture_path)
    else:
        meta = {
            "schema_version": None,
            "name": "inline",
            "count": len(cases),
            "min_categories": 1,
            "min_per_category": 1,
            "path": str(fixture_path) if fixture_path else None,
        }
        loaded = [dict(item) for item in cases]

    started = time.perf_counter()
    results: list[CaseResult] = []
    for case in loaded:
        kind = str(case.get("kind") or "")
        handler = _HANDLERS.get(kind)
        if handler is None:
            results.append(
                CaseResult(
                    id=str(case.get("id") or ""),
                    category=str(case.get("category") or "unknown"),
                    kind=kind,
                    polarity=str(case.get("polarity") or "unknown"),
                    matched=False,
                    detail=f"unknown kind {kind!r}",
                )
            )
            continue
        try:
            matched, detail = handler(
                dict(case.get("input") or {}),
                dict(case.get("expect") or {}),
            )
        except Exception as exc:  # noqa: BLE001
            matched, detail = False, f"handler error: {exc}"
        results.append(
            CaseResult(
                id=str(case.get("id") or ""),
                category=str(case.get("category") or "unknown"),
                kind=kind,
                polarity=str(case.get("polarity") or "unknown"),
                matched=matched,
                detail=detail,
            )
        )
    duration_ms = (time.perf_counter() - started) * 1000.0
    return BenchmarkReport(meta=meta, results=results, duration_ms=duration_ms)
