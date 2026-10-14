from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

from .bwrap_executor import SandboxUnavailableError, bwrap_available, execute_with_bwrap
from .executor_common import (
    MAX_TIMEOUT_SECONDS,
    normalize_container_cwd,
    validate_command,
)

logger = logging.getLogger(__name__)

SANDBOX_IMAGE = os.environ.get("AETHERCHAT_SANDBOX_IMAGE", "aether-sandbox:latest")
SandboxBackend = Literal["docker", "bwrap", "auto"]
SandboxTier = Literal["bwrap", "docker", "strong"]

# Re-export for callers/tests.
__all__ = [
    "MAX_TIMEOUT_SECONDS",
    "SandboxUnavailableError",
    "execute_in_sandbox",
    "normalize_container_cwd",
    "select_sandbox_backend",
    "select_sandbox_tier",
    "sandbox_status",
    "validate_command",
]


def docker_available() -> bool:
    try:
        import docker

        client = docker.from_env()
        try:
            client.ping()
            return True
        finally:
            with suppress(Exception):
                client.close()
    except Exception:
        return False



def select_sandbox_tier(tier: SandboxTier | None = None) -> SandboxBackend:
    """Map configured isolation tier to a concrete backend.

    - bwrap: OS bubblewrap only
    - docker: Docker hardened container only
    - strong: best available hardened tier (Docker today; future Firecracker/Kata).
      Fail closed if Docker is unavailable — do not silently fall back to bwrap.
    """
    chosen = (tier or os.environ.get("AETHERCHAT_SANDBOX_TIER", "").strip().lower() or None)
    if chosen not in {"bwrap", "docker", "strong", None}:
        chosen = None
    if chosen == "bwrap":
        return select_sandbox_backend(prefer="bwrap")
    if chosen == "docker":
        return select_sandbox_backend(prefer="docker")
    if chosen == "strong":
        if docker_available():
            return "docker"
        raise SandboxUnavailableError(
            "sandbox_tier=strong requires Docker (hardened image); "
            "Firecracker/Kata is not yet wired. Refusing unsandboxed execution. "
            "See docs/SANDBOX.md."
        )
    return select_sandbox_backend(prefer="auto")


def sandbox_status() -> dict[str, object]:
    tier = os.environ.get("AETHERCHAT_SANDBOX_TIER", "").strip().lower() or "auto"
    try:
        backend = select_sandbox_tier(tier if tier in {"bwrap", "docker", "strong"} else None)  # type: ignore[arg-type]
        error = None
    except SandboxUnavailableError as exc:
        backend = "none"
        error = str(exc)
    return {
        "tier": tier,
        "backend": backend,
        "docker_available": docker_available(),
        "bwrap_available": bwrap_available(),
        "error": error,
        "strong_provider": "docker" if docker_available() else None,
    }

def select_sandbox_backend(
    *,
    prefer: SandboxBackend | None = None,
    allow_net: bool = False,
) -> SandboxBackend:
    """Choose docker or bwrap. Never returns a bare-host backend."""
    forced = (prefer or os.environ.get("AETHERCHAT_SANDBOX_BACKEND", "auto")).strip().lower()
    if forced not in {"docker", "bwrap", "auto"}:
        forced = "auto"
    if forced == "docker":
        if docker_available():
            return "docker"
        raise SandboxUnavailableError(
            "AETHERCHAT_SANDBOX_BACKEND=docker but Docker is unavailable; refusing unsandboxed execution"
        )
    if forced == "bwrap":
        if bwrap_available():
            return "bwrap"
        raise SandboxUnavailableError(
            "AETHERCHAT_SANDBOX_BACKEND=bwrap but bubblewrap is missing; refusing unsandboxed execution"
        )
    # auto: prefer Docker for stronger isolation when present; else bwrap.
    if docker_available():
        return "docker"
    if bwrap_available():
        return "bwrap"
    raise SandboxUnavailableError(
        "Neither Docker nor bubblewrap (bwrap) is available; refusing to run unsandboxed. "
        "Install bubblewrap or the aether-sandbox Docker image. See docs/SANDBOX.md."
    )


def _run_container(
    workspace: Path,
    command: str,
    working_dir: str,
    timeout: int,
    allow_net: bool,
) -> dict[str, Any]:
    import docker

    client = docker.from_env()
    container = None
    run_options: dict[str, Any] = {
        "image": SANDBOX_IMAGE,
        "command": ["timeout", "--signal=KILL", f"{timeout}s", "bash", "-lc", command],
        "working_dir": working_dir,
        "detach": True,
        "network_mode": "bridge" if allow_net else "none",
        "mem_limit": "1g",
        "cpu_quota": 150000,
        "pids_limit": 128,
        "security_opt": ["no-new-privileges"],
        "cap_drop": ["ALL"],
        "read_only": True,
        "volumes": {str(workspace): {"bind": "/workspace", "mode": "rw"}},
        "tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=64m"},
        "stdout": True,
        "stderr": True,
        "labels": {"com.aetherchat.sandbox": "true"},
    }
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        run_options["user"] = f"{os.getuid()}:{os.getgid()}"

    try:
        container = client.containers.run(**run_options)
        result = container.wait(timeout=timeout + 5)
        output = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
        status_code = int(result.get("StatusCode", 1))
        return {
            "stdout": output,
            "exit_code": status_code,
            "timed_out": status_code == 124,
            "network": "bridge" if allow_net else "none",
            "backend": "docker",
        }
    finally:
        if container is not None:
            with suppress(Exception):
                container.remove(force=True)
        with suppress(Exception):
            client.close()


async def execute_in_sandbox(
    cmd: str,
    cwd: str = ".",
    timeout: int = 30,
    session_id: str = "",
    tenant_id: str = "",
    allow_net: bool = False,
    workspace_path: str | os.PathLike[str] | None = None,
    backend: SandboxBackend | None = None,
) -> dict[str, Any]:
    """Run a command in Docker or bubblewrap. Never executes on a bare host shell."""
    del session_id, tenant_id  # reserved for future tenancy labels
    command, bounded_timeout = validate_command(cmd, timeout)
    if workspace_path is None:
        raise ValueError("Sandbox workspace was not prepared")

    workspace = Path(workspace_path).resolve(strict=True)
    try:
        if backend is None and os.environ.get("AETHERCHAT_SANDBOX_TIER", "").strip():
            chosen = select_sandbox_tier()
        else:
            chosen = select_sandbox_backend(prefer=backend, allow_net=allow_net)
    except SandboxUnavailableError as exc:
        from ..services import runtime_metrics

        runtime_metrics.incr("sandbox_failures")
        return {"error": str(exc), "exit_code": 1, "timed_out": False, "backend": "none"}

    try:
        if chosen == "bwrap":
            return await execute_with_bwrap(
                command,
                cwd=cwd,
                timeout=bounded_timeout,
                allow_net=bool(allow_net),
                workspace_path=workspace,
            )
        from pathlib import PurePosixPath

        working_dir = normalize_container_cwd(cwd)
        requested_cwd = workspace / PurePosixPath(working_dir).relative_to("/workspace")
        if not requested_cwd.resolve().is_relative_to(workspace) or not requested_cwd.is_dir():
            raise ValueError("Sandbox cwd does not exist inside the workspace")
        return await asyncio.to_thread(
            _run_container,
            workspace,
            command,
            working_dir,
            bounded_timeout,
            bool(allow_net),
        )
    except SandboxUnavailableError as exc:
        from ..services import runtime_metrics

        runtime_metrics.incr("sandbox_failures")
        return {"error": str(exc), "exit_code": 1, "timed_out": False, "backend": chosen}
    except Exception as exc:
        from ..services import runtime_metrics

        runtime_metrics.incr("sandbox_failures")
        logger.exception("sandbox execution failed via %s", chosen)
        return {"error": str(exc), "exit_code": 1, "timed_out": False, "backend": chosen}
