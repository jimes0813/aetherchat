"""Bubblewrap (bwrap) OS-level sandbox for Linux when Docker is unavailable.

Deny-by-default: workspace-scoped write, no network unless allow_net=True.
Fail closed if `bwrap` is missing — never falls back to a bare host shell.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .executor_common import normalize_host_cwd, validate_command


class SandboxUnavailableError(RuntimeError):
    """Raised when no safe sandbox backend can run the command."""


def bwrap_available() -> bool:
    return shutil.which("bwrap") is not None


def _build_bwrap_argv(
    workspace: Path,
    command: str,
    working_dir: Path,
    timeout: int,
    allow_net: bool,
) -> list[str]:
    argv = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--bind",
        str(workspace),
        str(workspace),
        "--chdir",
        str(working_dir),
        "--setenv",
        "HOME",
        str(workspace),
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--cap-drop",
        "ALL",
    ]
    if not allow_net:
        argv.insert(argv.index("--unshare-uts") + 1, "--unshare-net")
    argv.extend(
        [
            "timeout",
            "--signal=KILL",
            f"{timeout}s",
            "bash",
            "-lc",
            command,
        ]
    )
    return argv


def _decode_blob(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _run_bwrap(
    workspace: Path,
    command: str,
    cwd: str,
    timeout: int,
    allow_net: bool,
) -> dict[str, Any]:
    if not bwrap_available():
        raise SandboxUnavailableError(
            "bubblewrap (bwrap) is not installed; refusing to run unsandboxed. "
            "Install bubblewrap or provide Docker (aether-sandbox image). "
            "See docs/SANDBOX.md."
        )
    working_dir = normalize_host_cwd(workspace, cwd)
    argv = _build_bwrap_argv(workspace, command, working_dir, timeout, allow_net)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout + 5,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = _decode_blob(exc.stdout) + _decode_blob(exc.stderr)
        return {
            "stdout": output,
            "exit_code": 124,
            "timed_out": True,
            "network": "host" if allow_net else "none",
            "backend": "bwrap",
        }
    except FileNotFoundError as exc:
        raise SandboxUnavailableError(
            "bubblewrap (bwrap) disappeared; refusing unsandboxed execution"
        ) from exc

    output = (proc.stdout or "") + (proc.stderr or "")
    return {
        "stdout": output,
        "exit_code": int(proc.returncode),
        "timed_out": int(proc.returncode) == 124,
        "network": "host" if allow_net else "none",
        "backend": "bwrap",
    }


async def execute_with_bwrap(
    cmd: str,
    cwd: str = ".",
    timeout: int = 30,
    allow_net: bool = False,
    workspace_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    command, bounded_timeout = validate_command(cmd, timeout)
    if workspace_path is None:
        raise ValueError("Sandbox workspace was not prepared")
    workspace = Path(workspace_path).resolve(strict=True)
    return await asyncio.to_thread(
        _run_bwrap,
        workspace,
        command,
        cwd,
        bounded_timeout,
        bool(allow_net),
    )
