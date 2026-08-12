from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .chat_database import chat_database
from .structured_logging import diagnostic_ref

_RUN_STATUSES = {
    "queued",
    "running",
    "waiting_approval",
    "waiting_plan_confirmation",
    "recoverable",
    "cancelled",
    "completed",
    "failed",
    "interrupted",
}
_STEP_STATUSES = {
    "pending",
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "blocked",
    "interrupted",
    "cancelled",
    "skipped",
}
_TOOL_STATUSES = {
    "pending",
    "waiting_approval",
    "running",
    "completed",
    "failed",
    "uncertain",
}
_VERIFICATION_STATUSES = {
    "verified",
    "unverified",
    "uncertain",
    "failed",
    "not_applicable",
}
_PROVIDER_TYPES = {"openai_compat", "anthropic", "xai", "local"}
_FAILURE_CODES = {
    "action_failed",
    "budget_exceeded",
    "invalid_action",
    "invalid_file_tool_arguments",
    "model_turn_limit",
    "provider_error",
    "required_tool_not_called",
    "run_dispatcher_unavailable",
    "run_in_progress",
    "run_queue_full",
    "tool_loop_limit",
    "tool_result_uncertain",
    "unknown_tool",
}
_PROVIDER_PHASES = {"requesting", "streaming", "tool", "finalizing"}
_STEP_ROW_LIMIT = 2000
_PLAN_STEP_ROW_LIMIT = 2000
_TOOL_ROW_LIMIT = 5000
_EVENT_ROW_LIMIT = 10000
_REQUEST_REF_RE = re.compile(r"^request:[0-9a-f]{12}$")


def _safe_enum(value: object, allowed: set[str]) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else "unknown"


def _nonnegative_int(value: object, *, maximum: int = 1_000_000_000_000) -> int:
    if isinstance(value, bool):
        return 0
    if not isinstance(value, str | int | float):
        return 0
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(parsed, maximum))


def _nonnegative_float(value: object, *, maximum: float = 1_000_000_000.0) -> float:
    if isinstance(value, bool):
        return 0.0
    if not isinstance(value, str | int | float):
        return 0.0
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(parsed):
        return 0.0
    return max(0.0, min(parsed, maximum))


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(started_at: object, completed_at: object) -> float | None:
    started = _parse_datetime(started_at)
    completed = _parse_datetime(completed_at)
    if started is None or completed is None:
        return None
    try:
        if completed < started:
            return None
        return round((completed - started).total_seconds() * 1000, 2)
    except TypeError:
        return None


def _safe_timestamp(value: object) -> str | None:
    text = str(value or "").strip()
    return text[:40] if text and _parse_datetime(text) is not None else None


def _timestamp_sort_key(value: object) -> tuple[int, float, str]:
    text = _safe_timestamp(value)
    parsed = _parse_datetime(text)
    if text is None or parsed is None:
        return (0, 0.0, "")
    try:
        return (1, parsed.timestamp(), text)
    except (OSError, OverflowError, ValueError):
        return (0, 0.0, "")


def _distribution(values: list[float]) -> dict[str, int | float | None]:
    bounded = sorted(value for value in values if value >= 0 and math.isfinite(value))
    if not bounded:
        return {"count": 0, "p50": None, "p95": None, "max": None}

    def percentile(fraction: float) -> float:
        index = max(0, math.ceil(len(bounded) * fraction) - 1)
        return round(bounded[index], 2)

    return {
        "count": len(bounded),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "max": round(bounded[-1], 2),
    }


def _counts(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _tool_family(tool_name: object) -> str:
    name = str(tool_name or "")
    if name.startswith("plugin__"):
        return "plugin"
    if name.startswith("mcp__"):
        return "mcp"
    if "__" in name:
        return "extension"
    return "builtin" if name else "unknown"


def _decode_payload(value: object) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


class RunTelemetryService:
    """Aggregate persisted run evidence without exporting user or provider content."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        configured = database_path if database_path is not None else chat_database.path
        self.database_path = Path(configured).expanduser().resolve()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"{self.database_path.as_uri()}?mode=ro",
            uri=True,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _related_rows(
        connection: sqlite3.Connection,
        table: str,
        columns: str,
        run_ids: list[str],
        *,
        order_by: str,
        limit: int,
    ) -> list[sqlite3.Row]:
        if not run_ids:
            return []
        placeholders = ", ".join("?" for _item in run_ids)
        return connection.execute(
            f"SELECT {columns} FROM {table} "
            f"WHERE run_id IN ({placeholders}) ORDER BY {order_by} LIMIT ?",
            [*run_ids, max(1, int(limit)) + 1],
        ).fetchall()

    def snapshot(
        self,
        *,
        run_limit: int = 50,
        message_limit: int = 1000,
    ) -> dict[str, Any]:
        bounded_run_limit = max(1, min(int(run_limit), 200))
        bounded_message_limit = max(1, min(int(message_limit), 5000))
        try:
            with self._connect() as connection:
                runs = connection.execute(
                    """SELECT id, status, mode, retry_count, created_at,
                              started_at, completed_at
                       FROM agent_runs
                       ORDER BY COALESCE(started_at, created_at) DESC, rowid DESC
                       LIMIT ?""",
                    (bounded_run_limit,),
                ).fetchall()
                run_ids = [str(row["id"]) for row in runs]
                steps = self._related_rows(
                    connection,
                    "agent_steps",
                    "id, run_id, status, attempt, started_at, completed_at",
                    run_ids,
                    order_by="created_at DESC, rowid DESC",
                    limit=_STEP_ROW_LIMIT,
                )
                plan_steps = self._related_rows(
                    connection,
                    "agent_plan_steps",
                    "id, run_id, status, attempt, started_at, completed_at",
                    run_ids,
                    order_by="created_at DESC, rowid DESC",
                    limit=_PLAN_STEP_ROW_LIMIT,
                )
                tools = self._related_rows(
                    connection,
                    "agent_tool_calls",
                    "id, run_id, provider_type, tool_name, side_effect, status, "
                    "verification_status, created_at, completed_at",
                    run_ids,
                    order_by="created_at DESC, rowid DESC",
                    limit=_TOOL_ROW_LIMIT,
                )
                events = self._related_rows(
                    connection,
                    "agent_events",
                    "id, run_id, event_type, payload_json, created_at",
                    run_ids,
                    order_by="created_at DESC, rowid DESC",
                    limit=_EVENT_ROW_LIMIT,
                )
                messages = connection.execute(
                    """SELECT tokens_in, tokens_out, cost_usd
                       FROM messages WHERE role = 'assistant'
                       ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                    (bounded_message_limit + 1,),
                ).fetchall()
        except (OSError, sqlite3.Error, ValueError):
            return {
                "schema": "aetherchat-run-telemetry-v1",
                "status": "unavailable",
                "error_code": "telemetry_database_unavailable",
            }

        rows_truncated = {
            "steps": len(steps) > _STEP_ROW_LIMIT,
            "plan_steps": len(plan_steps) > _PLAN_STEP_ROW_LIMIT,
            "tools": len(tools) > _TOOL_ROW_LIMIT,
            "events": len(events) > _EVENT_ROW_LIMIT,
            "assistant_messages": len(messages) > bounded_message_limit,
        }
        steps = steps[:_STEP_ROW_LIMIT]
        plan_steps = plan_steps[:_PLAN_STEP_ROW_LIMIT]
        tools = tools[:_TOOL_ROW_LIMIT]
        events = events[:_EVENT_ROW_LIMIT]
        messages = messages[:bounded_message_limit]

        steps_by_run: dict[str, list[sqlite3.Row]] = defaultdict(list)
        plan_steps_by_run: dict[str, list[sqlite3.Row]] = defaultdict(list)
        tools_by_run: dict[str, list[sqlite3.Row]] = defaultdict(list)
        events_by_run: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in steps:
            steps_by_run[str(row["run_id"])].append(row)
        for row in plan_steps:
            plan_steps_by_run[str(row["run_id"])].append(row)
        for row in tools:
            tools_by_run[str(row["run_id"])].append(row)
        for row in events:
            events_by_run[str(row["run_id"])].append(row)

        run_durations = [
            duration
            for row in runs
            if (duration := _duration_ms(row["started_at"], row["completed_at"]))
            is not None
        ]
        step_durations = [
            duration
            for row in steps
            if (duration := _duration_ms(row["started_at"], row["completed_at"]))
            is not None
        ]
        plan_step_durations = [
            duration
            for row in plan_steps
            if (duration := _duration_ms(row["started_at"], row["completed_at"]))
            is not None
        ]
        tool_durations = [
            duration
            for row in tools
            if (duration := _duration_ms(row["created_at"], row["completed_at"]))
            is not None
        ]

        provider_first_token_ms: list[float] = []
        provider_elapsed_ms: list[float] = []
        provider_turn_types: list[str] = []
        failure_codes: list[str] = []
        assistant_event_tokens_in = 0
        assistant_event_tokens_out = 0
        assistant_event_cost = 0.0
        traces: list[dict[str, Any]] = []

        for run in runs:
            run_id = str(run["id"])
            trace_events = sorted(
                events_by_run[run_id],
                key=lambda row: _timestamp_sort_key(row["created_at"]),
            )
            trace_provider_types: list[str] = []
            trace_first_token_ms: list[float] = []
            trace_failure_codes: list[str] = []
            request_refs: list[str] = []
            trace_tokens_in = 0
            trace_tokens_out = 0
            trace_cost = 0.0
            provider_turns = 0
            provider_spans: list[dict[str, Any]] = []

            for event in trace_events:
                event_type = str(event["event_type"] or "")
                payload = _decode_payload(event["payload_json"])
                if event_type == "trace_context":
                    persisted_ref = str(payload.get("request_ref") or "").strip()
                    if _REQUEST_REF_RE.fullmatch(persisted_ref):
                        request_ref = persisted_ref
                        if request_ref not in request_refs:
                            request_refs.append(request_ref)
                    else:
                        request_id = str(payload.get("request_id") or "").strip()
                        if request_id:
                            request_ref = diagnostic_ref("request", request_id)
                            if request_ref not in request_refs:
                                request_refs.append(request_ref)
                    continue
                if event_type == "provider_status":
                    provider_type = _safe_enum(
                        payload.get("provider_type"),
                        _PROVIDER_TYPES,
                    )
                    if str(payload.get("phase") or "") == "requesting":
                        provider_turns += 1
                        provider_turn_types.append(provider_type)
                        trace_provider_types.append(provider_type)
                    if payload.get("first_token_ms") is not None:
                        first_token = _nonnegative_float(
                            payload.get("first_token_ms"),
                            maximum=86_400_000,
                        )
                        provider_first_token_ms.append(first_token)
                        trace_first_token_ms.append(first_token)
                    if (
                        str(payload.get("phase") or "") == "finalizing"
                        and payload.get("elapsed_ms") is not None
                    ):
                        provider_elapsed_ms.append(
                            _nonnegative_float(
                                payload.get("elapsed_ms"),
                                maximum=86_400_000,
                            )
                        )
                    provider_spans.append({
                        "span_ref": diagnostic_ref("provider", event["id"]),
                        "provider_type": provider_type,
                        "phase": _safe_enum(
                            payload.get("phase"),
                            _PROVIDER_PHASES,
                        ),
                        "round_index": _nonnegative_int(
                            payload.get("round_index"),
                            maximum=1000,
                        ),
                        "occurred_at": _safe_timestamp(event["created_at"]),
                        "first_token_ms": (
                            _nonnegative_float(
                                payload.get("first_token_ms"),
                                maximum=86_400_000,
                            )
                            if payload.get("first_token_ms") is not None
                            else None
                        ),
                        "elapsed_ms": (
                            _nonnegative_float(
                                payload.get("elapsed_ms"),
                                maximum=86_400_000,
                            )
                            if payload.get("elapsed_ms") is not None
                            else None
                        ),
                    })
                    continue
                if event_type == "message_complete":
                    if str(payload.get("role") or "") != "assistant":
                        continue
                    tokens_in = _nonnegative_int(payload.get("tokens_in"))
                    tokens_out = _nonnegative_int(payload.get("tokens_out"))
                    cost = _nonnegative_float(payload.get("cost_usd"))
                    trace_tokens_in += tokens_in
                    trace_tokens_out += tokens_out
                    trace_cost += cost
                    assistant_event_tokens_in += tokens_in
                    assistant_event_tokens_out += tokens_out
                    assistant_event_cost += cost
                    continue
                if event_type == "error":
                    code = _safe_enum(payload.get("code"), _FAILURE_CODES)
                    failure_codes.append(code)
                    trace_failure_codes.append(code)

            trace_steps = steps_by_run[run_id]
            trace_plan_steps = plan_steps_by_run[run_id]
            trace_tools = tools_by_run[run_id]
            traces.append(
                {
                    "trace_id": diagnostic_ref("run", run_id),
                    "request_refs": request_refs[-3:],
                    "status": _safe_enum(run["status"], _RUN_STATUSES),
                    "mode": _safe_enum(
                        run["mode"],
                        {"casual", "roundtable", "project"},
                    ),
                    "retry_count": _nonnegative_int(run["retry_count"], maximum=1000),
                    "started_at": _safe_timestamp(run["started_at"])
                    or _safe_timestamp(run["created_at"]),
                    "completed_at": _safe_timestamp(run["completed_at"]),
                    "duration_ms": _duration_ms(
                        run["started_at"],
                        run["completed_at"],
                    ),
                    "steps": {
                        "count": len(trace_steps),
                        "status_counts": _counts([
                            _safe_enum(row["status"], _STEP_STATUSES)
                            for row in trace_steps
                        ]),
                    },
                    "plan_steps": {
                        "count": len(trace_plan_steps),
                        "status_counts": _counts([
                            _safe_enum(row["status"], _STEP_STATUSES)
                            for row in trace_plan_steps
                        ]),
                    },
                    "providers": {
                        "turns": provider_turns,
                        "type_counts": _counts(trace_provider_types),
                        "first_token_ms": _distribution(trace_first_token_ms),
                    },
                    "tools": {
                        "calls": len(trace_tools),
                        "family_counts": _counts([
                            _tool_family(row["tool_name"])
                            for row in trace_tools
                        ]),
                        "status_counts": _counts([
                            _safe_enum(row["status"], _TOOL_STATUSES)
                            for row in trace_tools
                        ]),
                        "side_effect_calls": sum(
                            1 for row in trace_tools if bool(row["side_effect"])
                        ),
                    },
                    "usage": {
                        "tokens_in": trace_tokens_in,
                        "tokens_out": trace_tokens_out,
                        "cost_usd": round(trace_cost, 6),
                    },
                    "failure_code_counts": _counts(trace_failure_codes),
                    "spans": {
                        "steps": [
                            {
                                "span_ref": diagnostic_ref("step", row["id"]),
                                "status": _safe_enum(
                                    row["status"],
                                    _STEP_STATUSES,
                                ),
                                "attempt": _nonnegative_int(
                                    row["attempt"],
                                    maximum=1000,
                                ),
                                "started_at": _safe_timestamp(row["started_at"]),
                                "completed_at": _safe_timestamp(row["completed_at"]),
                                "duration_ms": _duration_ms(
                                    row["started_at"],
                                    row["completed_at"],
                                ),
                            }
                            for row in sorted(
                                trace_steps,
                                key=lambda item: _timestamp_sort_key(
                                    item["started_at"]
                                ),
                            )[-100:]
                        ],
                        "plan_steps": [
                            {
                                "span_ref": diagnostic_ref("plan-step", row["id"]),
                                "status": _safe_enum(
                                    row["status"],
                                    _STEP_STATUSES,
                                ),
                                "attempt": _nonnegative_int(
                                    row["attempt"],
                                    maximum=1000,
                                ),
                                "started_at": _safe_timestamp(row["started_at"]),
                                "completed_at": _safe_timestamp(row["completed_at"]),
                                "duration_ms": _duration_ms(
                                    row["started_at"],
                                    row["completed_at"],
                                ),
                            }
                            for row in sorted(
                                trace_plan_steps,
                                key=lambda item: _timestamp_sort_key(
                                    item["started_at"]
                                ),
                            )[-100:]
                        ],
                        "providers": provider_spans[-100:],
                        "tools": [
                            {
                                "span_ref": diagnostic_ref("tool", row["id"]),
                                "provider_type": _safe_enum(
                                    row["provider_type"],
                                    _PROVIDER_TYPES,
                                ),
                                "family": _tool_family(row["tool_name"]),
                                "status": _safe_enum(
                                    row["status"],
                                    _TOOL_STATUSES,
                                ),
                                "verification_status": _safe_enum(
                                    row["verification_status"],
                                    _VERIFICATION_STATUSES,
                                ),
                                "side_effect": bool(row["side_effect"]),
                                "started_at": _safe_timestamp(row["created_at"]),
                                "completed_at": _safe_timestamp(row["completed_at"]),
                                "duration_ms": _duration_ms(
                                    row["created_at"],
                                    row["completed_at"],
                                ),
                            }
                            for row in sorted(
                                trace_tools,
                                key=lambda item: _timestamp_sort_key(
                                    item["created_at"]
                                ),
                            )[-100:]
                        ],
                    },
                }
            )

        persisted_tokens_in = sum(
            _nonnegative_int(row["tokens_in"]) for row in messages
        )
        persisted_tokens_out = sum(
            _nonnegative_int(row["tokens_out"]) for row in messages
        )
        persisted_cost = sum(
            _nonnegative_float(row["cost_usd"]) for row in messages
        )

        start_values = [
            timestamp
            for row in runs
            if (
                timestamp := _safe_timestamp(row["started_at"])
                or _safe_timestamp(row["created_at"])
            )
        ]
        return {
            "schema": "aetherchat-run-telemetry-v1",
            "status": "ready",
            "sample": {
                "run_limit": bounded_run_limit,
                "runs": len(runs),
                "oldest_started_at": (
                    min(start_values, key=_timestamp_sort_key)
                    if start_values
                    else None
                ),
                "newest_started_at": (
                    max(start_values, key=_timestamp_sort_key)
                    if start_values
                    else None
                ),
                "assistant_message_limit": bounded_message_limit,
                "assistant_messages": len(messages),
                "row_limits": {
                    "steps": _STEP_ROW_LIMIT,
                    "plan_steps": _PLAN_STEP_ROW_LIMIT,
                    "tools": _TOOL_ROW_LIMIT,
                    "events": _EVENT_ROW_LIMIT,
                },
                "rows_truncated": rows_truncated,
            },
            "runs": {
                "status_counts": _counts([
                    _safe_enum(row["status"], _RUN_STATUSES) for row in runs
                ]),
                "duration_ms": _distribution(run_durations),
                "retry_count": sum(
                    _nonnegative_int(row["retry_count"], maximum=1000)
                    for row in runs
                ),
            },
            "steps": {
                "status_counts": _counts([
                    _safe_enum(row["status"], _STEP_STATUSES) for row in steps
                ]),
                "duration_ms": _distribution(step_durations),
            },
            "plan_steps": {
                "status_counts": _counts([
                    _safe_enum(row["status"], _STEP_STATUSES)
                    for row in plan_steps
                ]),
                "duration_ms": _distribution(plan_step_durations),
            },
            "providers": {
                "turns": len(provider_turn_types),
                "type_counts": _counts(provider_turn_types),
                "first_token_ms": _distribution(provider_first_token_ms),
                "elapsed_ms": _distribution(provider_elapsed_ms),
            },
            "tools": {
                "calls": len(tools),
                "family_counts": _counts([
                    _tool_family(row["tool_name"]) for row in tools
                ]),
                "status_counts": _counts([
                    _safe_enum(row["status"], _TOOL_STATUSES) for row in tools
                ]),
                "verification_counts": _counts([
                    _safe_enum(
                        row["verification_status"],
                        _VERIFICATION_STATUSES,
                    )
                    for row in tools
                ]),
                "side_effect_calls": sum(
                    1 for row in tools if bool(row["side_effect"])
                ),
                "duration_ms": _distribution(tool_durations),
            },
            "usage": {
                "persisted_assistant_messages": {
                    "tokens_in": persisted_tokens_in,
                    "tokens_out": persisted_tokens_out,
                    "cost_usd": round(persisted_cost, 6),
                },
                "run_attributed_events": {
                    "tokens_in": assistant_event_tokens_in,
                    "tokens_out": assistant_event_tokens_out,
                    "cost_usd": round(assistant_event_cost, 6),
                },
            },
            "failures": {
                "run_failed": sum(
                    1 for row in runs if str(row["status"]) == "failed"
                ),
                "step_failed": sum(
                    1 for row in steps if str(row["status"]) == "failed"
                ),
                "plan_step_failed": sum(
                    1 for row in plan_steps if str(row["status"]) == "failed"
                ),
                "tool_failed": sum(
                    1 for row in tools if str(row["status"]) == "failed"
                ),
                "code_counts": _counts(failure_codes),
            },
            "traces": traces,
            "privacy": {
                "session_ids_included": False,
                "raw_run_ids_included": False,
                "model_names_included": False,
                "provider_names_included": False,
                "tool_names_included": False,
                "tool_arguments_included": False,
                "prompt_or_error_text_included": False,
            },
        }
