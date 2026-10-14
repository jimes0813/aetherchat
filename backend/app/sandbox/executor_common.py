from __future__ import annotations

from pathlib import Path, PurePosixPath

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


def normalize_host_cwd(workspace: Path, cwd: str) -> Path:
    """Map a relative POSIX cwd into an absolute path under workspace."""
    container = normalize_container_cwd(cwd)
    relative = PurePosixPath(container).relative_to("/workspace")
    target = (workspace / relative).resolve()
    if not target.is_relative_to(workspace.resolve()):
        raise ValueError("Sandbox cwd must stay inside the workspace")
    if not target.is_dir():
        raise ValueError("Sandbox cwd does not exist inside the workspace")
    return target


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
