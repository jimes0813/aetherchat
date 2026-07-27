from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any

import docker

SANDBOX_IMAGE = os.environ.get("AETHERCHAT_SANDBOX_IMAGE", "aether-sandbox:latest")
MAX_COMMAND_LENGTH = 20_000
MAX_TIMEOUT_SECONDS = 300


def normalize_container_cwd(cwd: str) -> str:
    value = str(cwd or ".").strip() or "."
    if "\x00" in value or "\\" in value:
        raise ValueError("Sandbox cwd must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Sandbox cwd must stay inside /workspace")
    normalized = path.as_posix().lstrip("./")
    return "/workspace" if not normalized else f"/workspace/{normalized}"


def validate_command(command: str, timeout: int) -> tuple[str, int]:
    value = str(command or "").strip()
    if not value:
        raise ValueError("Sandbox command is required")
    if len(value) > MAX_COMMAND_LENGTH or "\x00" in value:
        raise ValueError("Sandbox command is too large or invalid")
    bounded_timeout = int(timeout)
    if not 1 <= bounded_timeout <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"Sandbox timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds")
    return value, bounded_timeout


def _run_container(
    workspace: Path,
    command: str,
    working_dir: str,
    timeout: int,
    allow_net: bool,
) -> dict[str, Any]:
    client = docker.from_env()
    container = None
    run_options: dict[str, Any] = {
        "image": SANDBOX_IMAGE,
        # The only shell is inside the isolated container. No user text is
        # interpolated into a host command line.
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
) -> dict[str, Any]:
    command, bounded_timeout = validate_command(cmd, timeout)
    working_dir = normalize_container_cwd(cwd)
    if workspace_path is None:
        raise ValueError("Sandbox workspace was not prepared")

    workspace = Path(workspace_path).resolve(strict=True)
    requested_cwd = workspace / PurePosixPath(working_dir).relative_to("/workspace")
    if not requested_cwd.resolve().is_relative_to(workspace) or not requested_cwd.is_dir():
        raise ValueError("Sandbox cwd does not exist inside the workspace")

    try:
        return await asyncio.to_thread(
            _run_container,
            workspace,
            command,
            working_dir,
            bounded_timeout,
            bool(allow_net),
        )
    except Exception as exc:
        return {"error": str(exc), "exit_code": 1, "timed_out": False}
