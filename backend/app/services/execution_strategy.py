"""Pure execution-strategy contract resolution.

This module owns the wire-to-domain rule for direct and planned runs. It has
no database, provider, or orchestration dependencies, so the compatibility
contract can be tested independently from ``agent_run_service``.
"""

from __future__ import annotations

from typing import Any, TypedDict

VALID_EXECUTION_MODES = frozenset({"casual", "roundtable", "project"})
VALID_EXECUTION_STRATEGIES = frozenset({"direct", "planned"})


class ExecutionStrategyResolution(TypedDict):
    mode: str
    requested: str | None
    effective: str
    source: str


def resolve_execution_strategy(
    action: dict[str, Any],
) -> ExecutionStrategyResolution:
    """Resolve and persist one explicit execution contract on ``action``.

    Legacy clients may omit ``execution_strategy``. They remain compatible,
    but resolve to a persisted direct strategy whose source is explicitly
    marked ``compatibility_default``. A mapping containing
    ``requested_execution_strategy`` is treated as a previously persisted
    contract restored for retry or recovery.
    """

    mode = str(action.get("mode") or "project").strip()
    if mode not in VALID_EXECUTION_MODES:
        raise ValueError(f"不支持的运行模式: {mode}")

    already_resolved = "requested_execution_strategy" in action
    raw_strategy = (
        action.get("requested_execution_strategy")
        if already_resolved
        else action.get("execution_strategy")
    )
    requested_strategy = (
        str(raw_strategy).strip() if raw_strategy is not None else None
    )
    if (
        requested_strategy is not None
        and requested_strategy not in VALID_EXECUTION_STRATEGIES
    ):
        raise ValueError(f"不支持的执行策略: {requested_strategy}")

    if already_resolved:
        source = str(
            action.get("execution_strategy_source")
            or (
                "explicit"
                if requested_strategy is not None
                else "compatibility_default"
            )
        )
        effective_strategy = str(
            action.get("effective_execution_strategy")
            or action.get("execution_strategy")
            or requested_strategy
            or "direct"
        )
    else:
        source = (
            "explicit"
            if requested_strategy is not None
            else "compatibility_default"
        )
        effective_strategy = requested_strategy or "direct"

    if source not in {"explicit", "compatibility_default"}:
        raise ValueError(f"不支持的执行策略来源: {source}")
    if effective_strategy not in VALID_EXECUTION_STRATEGIES:
        raise ValueError(f"不支持的实际执行策略: {effective_strategy}")
    if source == "explicit" and requested_strategy is None:
        raise ValueError("显式执行策略缺少 requested_execution_strategy")
    if source == "compatibility_default" and requested_strategy is not None:
        raise ValueError("兼容默认策略不能包含显式 requested_execution_strategy")
    if requested_strategy is not None and effective_strategy != requested_strategy:
        raise ValueError("请求执行策略与实际执行策略不一致")
    if mode != "project" and effective_strategy == "planned":
        raise ValueError("planned 执行策略仅适用于 project 模式")

    action["mode"] = mode
    action["requested_execution_strategy"] = requested_strategy
    action["execution_strategy"] = effective_strategy
    action["effective_execution_strategy"] = effective_strategy
    action["execution_strategy_source"] = source
    return {
        "mode": mode,
        "requested": requested_strategy,
        "effective": effective_strategy,
        "source": source,
    }
