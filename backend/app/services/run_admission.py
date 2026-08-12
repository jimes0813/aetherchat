from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ..models.schemas import ProviderConfig

GPU_RESOURCE = "gpu"
PROVIDER_RESOURCE_PREFIX = "provider:"


def provider_resource(name: str) -> str:
    """Return a stable, non-secret dispatcher resource key for a model entry."""

    normalized = " ".join(str(name).split()).casefold()
    if not normalized:
        raise ValueError("provider name is required")
    return f"{PROVIDER_RESOURCE_PREFIX}{normalized[:120]}"


def admission_resources_for_action(
    action: dict[str, Any],
    provider_configs: Iterable[ProviderConfig] | None = None,
) -> tuple[str, ...]:
    """Resolve conservative provider/GPU resources before a Run starts.

    The resolver mirrors the orchestrator's target rules but deliberately
    over-reserves in roundtable/project modes. A local provider also consumes
    the shared GPU slot. Resource decisions are process-local scheduling hints;
    durable Run and Job state remains in SQLite.
    """

    if provider_configs is None:
        # Keep the pure dispatcher import free of registry/filesystem side
        # effects; production resolution loads the registry only on demand.
        from .model_registry import model_registry

        configs = model_registry.configs(available_only=True)
    else:
        configs = list(provider_configs)
    by_name = {config.name: config for config in configs}
    raw_targets = action.get("targets")
    default_targets = (
        [str(value) for value in raw_targets if str(value).strip()]
        if isinstance(raw_targets, list)
        else []
    )
    if not default_targets or "all" in default_targets:
        selected_names = list(by_name)
    else:
        selected_names = [name for name in default_targets if name in by_name]

    mentions = re.findall(r"@(\w+)", str(action.get("content") or ""))
    if mentions:
        if "all" in mentions:
            selected_names = list(by_name)
        else:
            mentioned = [name for name in mentions if name in by_name]
            if mentioned:
                selected_names = mentioned

    resources: set[str] = set()
    for name in selected_names:
        config = by_name[name]
        resources.add(provider_resource(name))
        if config.type == "local":
            resources.add(GPU_RESOURCE)
    return tuple(sorted(resources))
