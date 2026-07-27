from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any
from uuid import uuid4

from ..sandbox.executor import execute_in_sandbox
from .artifact_service import _data_root
from .chat_database import ChatDatabase, chat_database

MAX_PATCH_BYTES = 2 * 1024 * 1024
MAX_CHANGED_FILES = 200
MAX_WORKSPACE_FILE_BYTES = 2 * 1024 * 1024
MAX_VERIFICATION_COMMANDS = 4
MAX_VERIFICATION_OUTPUT_CHARS = 20_000
WORKSPACE_TOOL_NAMES = {
    "workspace_list_files",
    "workspace_read_file",
    "workspace_write_file",
    "workspace_delete_file",
    "workspace_run_command",
    "workspace_status",
}


class GitWorkspaceError(ValueError):
    pass


class GitWorkspaceService:
    """Owns trusted Git registrations and disposable Worker worktrees.

    Repository paths can only enter through the desktop-token-protected route.
    Models receive opaque workspace IDs; they never choose a host path.
    """

    def __init__(
        self,
        database: ChatDatabase | None = None,
        worktree_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self.database = database or chat_database
        self.worktree_root = (
            Path(worktree_root).expanduser().resolve()
            if worktree_root
            else (_data_root() / "git-worktrees").resolve()
        )
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    @staticmethod
    def _bounded_commands(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        commands: list[str] = []
        for raw in value:
            command = str(raw or "").strip()
            if not command:
                continue
            if "\x00" in command or len(command) > 2000:
                raise GitWorkspaceError("核验命令为空、过长或包含无效字符")
            if command not in commands:
                commands.append(command)
            if len(commands) >= MAX_VERIFICATION_COMMANDS:
                break
        return commands

    @staticmethod
    def _git_environment() -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {
                "GIT_DIR",
                "GIT_WORK_TREE",
                "GIT_INDEX_FILE",
                "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                "GIT_CONFIG",
                "GIT_CONFIG_COUNT",
            }
            and not key.startswith("GIT_CONFIG_KEY_")
            and not key.startswith("GIT_CONFIG_VALUE_")
        }
        environment["GIT_CONFIG_NOSYSTEM"] = "1"
        environment["GIT_CONFIG_GLOBAL"] = os.devnull
        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        environment.pop("GIT_EXTERNAL_DIFF", None)
        return environment

    @classmethod
    def _run_git(
        cls,
        repository: Path,
        *arguments: str,
        input_bytes: bytes | None = None,
        timeout: int = 30,
        allowed_exit_codes: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[bytes]:
        command = [
            "git",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repository),
            *arguments,
        ]
        try:
            result = subprocess.run(
                command,
                input=input_bytes,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=cls._git_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitWorkspaceError(f"Git 命令不可用或超时：{exc}") from exc
        if result.returncode not in allowed_exit_codes:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise GitWorkspaceError(
                detail[:2000] or f"Git 命令失败（退出码 {result.returncode}）"
            )
        return result

    @classmethod
    def _inspect_repository(cls, selected_path: str | os.PathLike[str]) -> dict[str, Any]:
        selected = Path(selected_path).expanduser().resolve(strict=True)
        if not selected.is_dir():
            raise GitWorkspaceError("请选择一个 Git 仓库目录")
        top_level_raw = cls._run_git(
            selected, "rev-parse", "--show-toplevel"
        ).stdout.decode("utf-8", errors="strict").strip()
        top_level = Path(top_level_raw).resolve(strict=True)
        if not top_level.is_dir():
            raise GitWorkspaceError("Git 工作树不存在")
        bare = cls._run_git(
            top_level, "rev-parse", "--is-bare-repository"
        ).stdout.decode("ascii", errors="replace").strip()
        if bare == "true":
            raise GitWorkspaceError("裸仓库不能作为 Agent 工作区")

        unsafe_filters = cls._run_git(
            top_level,
            "config",
            "--local",
            "--get-regexp",
            r"^filter\..*\.(clean|smudge|process|required)$",
            allowed_exit_codes=(0, 1),
        ).stdout
        if unsafe_filters.strip():
            raise GitWorkspaceError(
                "仓库配置了自定义 Git 过滤器；为避免主机侧执行外部程序，当前版本拒绝接入"
            )

        head = cls._run_git(top_level, "rev-parse", "HEAD").stdout.decode(
            "ascii", errors="strict"
        ).strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", head):
            raise GitWorkspaceError("仓库 HEAD 无法解析为提交哈希")
        branch_result = cls._run_git(
            top_level,
            "symbolic-ref",
            "--quiet",
            "--short",
            "HEAD",
            allowed_exit_codes=(0, 1),
        )
        branch = branch_result.stdout.decode("utf-8", errors="replace").strip()
        status = cls._run_git(
            top_level,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ).stdout
        return {
            "root": top_level,
            "head_commit": head.lower(),
            "branch": branch or "(detached)",
            "clean": not bool(status),
        }

    @staticmethod
    def _default_verification_commands(root: Path) -> list[str]:
        commands: list[str] = []
        if (
            (root / "pytest.ini").is_file()
            or (root / "tests").is_dir()
            and (
                (root / "pyproject.toml").is_file()
                or (root / "requirements.txt").is_file()
            )
        ):
            commands.append("python -m pytest -q")
        package_json = root / "package.json"
        if package_json.is_file():
            try:
                package = json.loads(
                    package_json.read_text(encoding="utf-8")[:512_000]
                )
                scripts = package.get("scripts") if isinstance(package, dict) else {}
                test_script = scripts.get("test") if isinstance(scripts, dict) else None
                if isinstance(test_script, str) and test_script.strip():
                    if "vitest" in test_script and "--run" not in test_script:
                        commands.append("npm test -- --run")
                    else:
                        commands.append("npm test")
                if (
                    isinstance(scripts, dict)
                    and isinstance(scripts.get("lint"), str)
                    and scripts["lint"].strip()
                ):
                    commands.append("npm run lint")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
        if (root / "Cargo.toml").is_file():
            commands.append("cargo test --locked")
        if (root / "go.mod").is_file():
            commands.append("go test ./...")
        return commands[:MAX_VERIFICATION_COMMANDS]

    def register_repository(
        self,
        *,
        session_id: str,
        selected_path: str,
    ) -> dict[str, Any]:
        inspected = self._inspect_repository(selected_path)
        if not inspected["clean"]:
            raise GitWorkspaceError(
                "仓库存在未提交或未跟踪改动；请先提交、暂存到其他位置或清理后再接入"
            )
        root = inspected["root"]
        repository = self.database.upsert_workspace_repository(
            session_id=str(session_id),
            root_path=str(root),
            display_name=root.name,
            head_commit=inspected["head_commit"],
            branch=inspected["branch"],
            verification_commands=self._default_verification_commands(root),
        )
        return self.public_repository(repository)

    def snapshot_repository_for_run(
        self,
        *,
        session_id: str,
        repository_id: str,
        verification_commands: Any = None,
    ) -> dict[str, Any]:
        repository = self.database.get_workspace_repository(repository_id)
        if (
            repository is None
            or repository["session_id"] != str(session_id)
            or repository.get("status") != "ready"
        ):
            raise GitWorkspaceError("当前会话未登记该 Git 仓库")
        inspected = self._inspect_repository(repository["root_path"])
        if inspected["root"] != Path(repository["root_path"]).resolve():
            raise GitWorkspaceError("仓库根目录已变化，请重新选择")
        if not inspected["clean"]:
            raise GitWorkspaceError("创建 Agent Run 前仓库必须保持干净")
        commands = (
            self._bounded_commands(verification_commands)
            if verification_commands is not None
            else self._bounded_commands(repository.get("verification_commands"))
        )
        return {
            "repository_id": repository["id"],
            "repository_name": repository["display_name"],
            "repository_base_commit": inspected["head_commit"],
            "repository_branch": inspected["branch"],
            "verification_commands": commands,
        }

    @staticmethod
    def public_repository(repository: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": repository["id"],
            "session_id": repository["session_id"],
            "display_name": repository["display_name"],
            "head_commit": repository["head_commit"],
            "head_short": str(repository["head_commit"])[:12],
            "branch": repository.get("branch") or "(detached)",
            "verification_commands": list(
                repository.get("verification_commands") or []
            ),
            "status": repository.get("status") or "ready",
            "updated_at": repository.get("updated_at"),
        }

    def list_repositories(self, session_id: str) -> list[dict[str, Any]]:
        return [
            self.public_repository(item)
            for item in self.database.list_workspace_repositories(session_id)
        ]

    @staticmethod
    def _owned_component(value: str) -> str:
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:20]

    def _assert_owned_worktree_path(self, value: str | os.PathLike[str]) -> Path:
        path = Path(value).resolve()
        if path == self.worktree_root or not path.is_relative_to(self.worktree_root):
            raise GitWorkspaceError("worktree 路径不属于 AetherChat")
        return path

    def prepare_worker_workspace(
        self,
        *,
        run_id: str,
        plan_step_id: str,
        session_id: str,
        repository_id: str,
        base_commit: str,
        attempt: int,
    ) -> dict[str, Any]:
        existing = next(
            (
                item
                for item in self.database.list_agent_workspaces(run_id)
                if item["plan_step_id"] == str(plan_step_id)
                and int(item["attempt"]) == max(1, int(attempt))
            ),
            None,
        )
        if existing is not None:
            path = self._assert_owned_worktree_path(existing["root_path"])
            if not path.is_dir():
                raise GitWorkspaceError(
                    "已记录的 Worker worktree 不存在，需人工检查后重试"
                )
            return existing

        repository = self.database.get_workspace_repository(repository_id)
        if repository is None or repository["session_id"] != str(session_id):
            raise GitWorkspaceError("当前会话未登记该 Git 仓库")
        inspected = self._inspect_repository(repository["root_path"])
        if inspected["head_commit"] != str(base_commit):
            raise GitWorkspaceError(
                "仓库 HEAD 已在计划确认后变化；为避免覆盖新工作，已停止创建 worktree"
            )
        if not inspected["clean"]:
            raise GitWorkspaceError(
                "仓库在计划确认后出现改动；为避免混入用户工作，已停止创建 worktree"
            )

        repository_root = inspected["root"]
        worktree_path = (
            self.worktree_root
            / self._owned_component(run_id)
            / (
                f"{self._owned_component(plan_step_id)}"
                f"-a{max(1, int(attempt))}"
            )
        )
        with self._lock:
            worktree_path.parent.mkdir(parents=True, exist_ok=True)
            if worktree_path.exists():
                raise GitWorkspaceError("目标 worktree 路径已存在，已拒绝覆盖")
            self._run_git(
                repository_root,
                "worktree",
                "add",
                "--detach",
                str(worktree_path),
                str(base_commit),
                timeout=60,
            )
            try:
                workspace = self.database.create_agent_workspace(
                    run_id=run_id,
                    plan_step_id=plan_step_id,
                    repository_id=repository_id,
                    session_id=session_id,
                    attempt=attempt,
                    root_path=str(worktree_path),
                    base_commit=base_commit,
                )
            except Exception:
                self._run_git(
                    repository_root,
                    "worktree",
                    "remove",
                    "--force",
                    str(worktree_path),
                    allowed_exit_codes=(0, 128),
                )
                raise
        return workspace

    def _workspace_for_context(
        self,
        *,
        workspace_id: str,
        session_id: str,
        run_id: str | None,
        plan_step_id: str | None,
    ) -> tuple[dict[str, Any], Path]:
        workspace = self.database.get_agent_workspace(workspace_id)
        if (
            workspace is None
            or workspace["session_id"] != str(session_id)
            or (run_id and workspace["run_id"] != str(run_id))
            or (
                plan_step_id
                and workspace["plan_step_id"] != str(plan_step_id)
            )
        ):
            raise GitWorkspaceError("Worker worktree 不存在或不属于当前执行上下文")
        root = self._assert_owned_worktree_path(workspace["root_path"])
        if not root.is_dir():
            raise GitWorkspaceError("Worker worktree 已被清理")
        return workspace, root

    @staticmethod
    def _relative_path(root: Path, value: Any, *, must_exist: bool) -> Path:
        raw = str(value or "").strip()
        if not raw or "\x00" in raw or "\\" in raw:
            raise GitWorkspaceError("文件路径必须是 worktree 内的相对 POSIX 路径")
        relative = PurePosixPath(raw)
        if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
            raise GitWorkspaceError("文件路径不得离开 worktree 或访问 .git")
        candidate = root.joinpath(*relative.parts)
        resolved = candidate.resolve(strict=must_exist)
        if not resolved.is_relative_to(root):
            raise GitWorkspaceError("文件路径通过符号链接离开 worktree")
        return resolved

    def invoke_workspace_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        workspace_id: str,
        session_id: str,
        run_id: str | None,
        plan_step_id: str | None,
    ) -> Any:
        workspace, root = self._workspace_for_context(
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
            plan_step_id=plan_step_id,
        )
        if name == "workspace_list_files":
            raw = self._run_git(
                root,
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ).stdout
            files = [
                value.decode("utf-8", errors="strict")
                for value in raw.split(b"\0")
                if value
            ]
            return {"files": files[:2000], "truncated": len(files) > 2000}
        if name == "workspace_read_file":
            path = self._relative_path(
                root, arguments.get("path"), must_exist=True
            )
            if path.is_symlink() or not path.is_file():
                raise GitWorkspaceError("只能读取 worktree 内的普通文件")
            if path.stat().st_size > MAX_WORKSPACE_FILE_BYTES:
                raise GitWorkspaceError("文件超过 Worker 读取上限")
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise GitWorkspaceError("当前 Worker 工具只读取 UTF-8 文本文件") from exc
            return {
                "path": path.relative_to(root).as_posix(),
                "content": content,
            }
        if name == "workspace_write_file":
            content = str(arguments.get("content") or "")
            if len(content.encode("utf-8")) > MAX_WORKSPACE_FILE_BYTES:
                raise GitWorkspaceError("写入内容超过 Worker 文件上限")
            path = self._relative_path(
                root, arguments.get("path"), must_exist=False
            )
            if path.exists() and (path.is_symlink() or not path.is_file()):
                raise GitWorkspaceError("只能覆盖 worktree 内的普通文件")
            parent = path.parent.resolve()
            if not parent.is_relative_to(root):
                raise GitWorkspaceError("目标目录通过符号链接离开 worktree")
            parent.mkdir(parents=True, exist_ok=True)
            temporary = parent / f".aether-write-{uuid4().hex}"
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
            return {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(content.encode("utf-8")),
                "written": True,
            }
        if name == "workspace_delete_file":
            path = self._relative_path(
                root, arguments.get("path"), must_exist=True
            )
            if path.is_symlink() or not path.is_file():
                raise GitWorkspaceError("只能删除 worktree 内的普通文件")
            path.unlink()
            return {
                "path": path.relative_to(root).as_posix(),
                "deleted": True,
            }
        if name == "workspace_status":
            return self._status_summary(root)
        if name == "workspace_run_command":
            return {
                "command": str(arguments.get("cmd") or ""),
                "workspace_id": workspace["id"],
                "deferred": True,
            }
        raise GitWorkspaceError(f"未知 Worker worktree 工具：{name}")

    async def run_workspace_command(
        self,
        *,
        arguments: dict[str, Any],
        workspace_id: str,
        session_id: str,
        run_id: str | None,
        plan_step_id: str | None,
    ) -> dict[str, Any]:
        workspace, root = self._workspace_for_context(
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
            plan_step_id=plan_step_id,
        )
        timeout = max(1, min(300, int(arguments.get("timeout", 60))))
        result = await execute_in_sandbox(
            str(arguments.get("cmd") or ""),
            str(arguments.get("cwd") or "."),
            timeout,
            session_id,
            "agent-worktree",
            allow_net=False,
            workspace_path=root,
        )
        return {
            "command": str(arguments.get("cmd") or ""),
            "workspace_id": workspace["id"],
            "sandbox": result,
        }

    def _status_entries(self, root: Path) -> list[dict[str, str]]:
        raw = self._run_git(
            root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ).stdout
        chunks = raw.split(b"\0")
        entries: list[dict[str, str]] = []
        index = 0
        while index < len(chunks):
            chunk = chunks[index]
            index += 1
            if not chunk:
                continue
            if len(chunk) < 4:
                raise GitWorkspaceError("Git status 返回了无法解析的记录")
            status = chunk[:2].decode("ascii", errors="strict")
            path = chunk[3:].decode("utf-8", errors="strict")
            entry = {"status": status, "path": path}
            if "R" in status or "C" in status:
                if index >= len(chunks) or not chunks[index]:
                    raise GitWorkspaceError("Git rename 记录不完整")
                entry["source_path"] = chunks[index].decode(
                    "utf-8", errors="strict"
                )
                index += 1
            entries.append(entry)
        return entries

    def _status_summary(self, root: Path) -> dict[str, Any]:
        entries = self._status_entries(root)
        tracked_diff = self._run_git(
            root,
            "diff",
            "--stat",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
        ).stdout.decode("utf-8", errors="replace").strip()
        return {
            "clean": not entries,
            "changed_files": entries[:MAX_CHANGED_FILES],
            "truncated": len(entries) > MAX_CHANGED_FILES,
            "diff_stat": tracked_diff[:20_000],
        }

    def _capture_patch(
        self,
        root: Path,
        base_commit: str,
    ) -> tuple[list[dict[str, str]], bytes]:
        entries = self._status_entries(root)
        if len(entries) > MAX_CHANGED_FILES:
            raise GitWorkspaceError(
                f"变更文件超过安全上限 {MAX_CHANGED_FILES}"
            )
        for entry in entries:
            path = self._relative_path(
                root, entry["path"], must_exist="D" not in entry["status"]
            )
            if path.exists() and path.is_symlink():
                raise GitWorkspaceError("候选变更包含符号链接，已拒绝生成补丁")
            stage = self._run_git(
                root,
                "ls-files",
                "--stage",
                "--",
                entry["path"],
            ).stdout
            if stage.startswith(b"160000 "):
                raise GitWorkspaceError("候选变更包含子模块，当前版本不支持安全应用")

        tracked_patch = self._run_git(
            root,
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            str(base_commit),
            "--",
        ).stdout
        pieces = [tracked_patch]
        for entry in entries:
            if entry["status"] != "??":
                continue
            result = self._run_git(
                root,
                "diff",
                "--no-index",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                "--no-textconv",
                "--",
                os.devnull,
                entry["path"],
                allowed_exit_codes=(0, 1),
            )
            patch = result.stdout.replace(
                f"a{os.devnull}".encode(),
                b"a/" + entry["path"].encode("utf-8"),
            )
            pieces.append(patch)
        patch = b"".join(piece for piece in pieces if piece)
        if len(patch) > MAX_PATCH_BYTES:
            raise GitWorkspaceError(
                f"候选补丁超过安全上限 {MAX_PATCH_BYTES // 1024} KiB"
            )
        return entries, patch

    async def finalize_workspace(
        self,
        workspace_id: str,
        verification_commands: Any,
    ) -> dict[str, Any]:
        workspace = self.database.get_agent_workspace(workspace_id)
        if workspace is None:
            raise GitWorkspaceError("Worker worktree 不存在")
        root = self._assert_owned_worktree_path(workspace["root_path"])
        commands = self._bounded_commands(verification_commands)
        verification: list[dict[str, Any]] = []
        for command in commands:
            result = await execute_in_sandbox(
                command,
                ".",
                120,
                workspace["session_id"],
                "deterministic-verifier",
                allow_net=False,
                workspace_path=root,
            )
            stdout = str(result.get("stdout") or "")
            error = str(result.get("error") or "")
            verification.append({
                "command": command,
                "exit_code": int(result.get("exit_code", 1)),
                "timed_out": bool(result.get("timed_out")),
                "network": str(result.get("network") or "none"),
                "output": (stdout or error)[:MAX_VERIFICATION_OUTPUT_CHARS],
                "output_sha256": hashlib.sha256(
                    (stdout + "\n" + error).encode("utf-8")
                ).hexdigest(),
            })

        try:
            changed_files, patch = await asyncio.to_thread(
                self._capture_patch,
                root,
                workspace["base_commit"],
            )
            if not patch:
                verification_status = "no_changes"
            elif verification and any(
                int(item["exit_code"]) != 0 for item in verification
            ):
                verification_status = "failed"
            elif verification:
                verification_status = "passed"
            else:
                verification_status = "unverified"
            updated = self.database.update_agent_workspace_evidence(
                workspace_id,
                status="verified",
                changed_files=changed_files,
                diff_patch=patch,
                diff_sha256=hashlib.sha256(patch).hexdigest() if patch else None,
                verification_status=verification_status,
                verification=verification,
            )
        except (GitWorkspaceError, OSError, UnicodeDecodeError) as exc:
            updated = self.database.update_agent_workspace_evidence(
                workspace_id,
                status="failed",
                changed_files=[],
                diff_patch=None,
                diff_sha256=None,
                verification_status="failed",
                verification=verification,
                error=str(exc),
            )
        if updated is None:
            raise GitWorkspaceError("Worker worktree 核验记录不存在")
        return updated

    @staticmethod
    def public_workspace(workspace: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": workspace["id"],
            "run_id": workspace["run_id"],
            "plan_step_id": workspace["plan_step_id"],
            "repository_id": workspace["repository_id"],
            "attempt": int(workspace["attempt"]),
            "base_commit": workspace["base_commit"],
            "base_short": str(workspace["base_commit"])[:12],
            "status": workspace["status"],
            "changed_files": list(workspace.get("changed_files") or []),
            "diff_sha256": workspace.get("diff_sha256"),
            "diff_bytes": int(workspace.get("diff_bytes") or 0),
            "verification_status": workspace.get("verification_status"),
            "verification": list(workspace.get("verification") or []),
            "apply_status": workspace.get("apply_status"),
            "error": workspace.get("error"),
            "created_at": workspace.get("created_at"),
            "updated_at": workspace.get("updated_at"),
            "verified_at": workspace.get("verified_at"),
            "applied_at": workspace.get("applied_at"),
        }

    def list_run_workspaces(
        self,
        *,
        run_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        run = self.database.get_agent_run(run_id, include_events=False)
        if run is None or run["session_id"] != str(session_id):
            raise KeyError("运行记录不存在")
        return [
            self.public_workspace(item)
            for item in self.database.list_agent_workspaces(run_id)
        ]

    def deterministic_evidence(self, run_id: str) -> list[dict[str, Any]]:
        latest_by_step: dict[str, dict[str, Any]] = {}
        for item in self.database.list_agent_workspaces(run_id):
            if item.get("status") not in {"verified", "failed"}:
                continue
            step_id = str(item["plan_step_id"])
            current = latest_by_step.get(step_id)
            if current is None or int(item["attempt"]) >= int(
                current["attempt"]
            ):
                latest_by_step[step_id] = item
        return [
            self.public_workspace(item)
            for item in latest_by_step.values()
        ]

    def enforce_review(
        self,
        run_id: str,
        review: dict[str, Any],
    ) -> dict[str, Any]:
        evidence = self.deterministic_evidence(run_id)
        if not evidence:
            return review
        passed = [
            item for item in evidence
            if item["verification_status"] == "passed"
        ]
        hard_failures = [
            item for item in evidence
            if item["verification_status"] in {"failed", "no_changes"}
        ]
        unverified = [
            item for item in evidence
            if item["verification_status"] == "unverified"
        ]
        result = dict(review)
        result_evidence = list(result.get("evidence") or [])
        result_evidence.append({
            "type": "deterministic_workspace_verification",
            "candidates": [
                {
                    "workspace_id": item["id"],
                    "diff_sha256": item.get("diff_sha256"),
                    "diff_bytes": item.get("diff_bytes"),
                    "changed_files": len(item.get("changed_files") or []),
                    "verification_status": item.get("verification_status"),
                    "commands": [
                        {
                            "command": command.get("command"),
                            "exit_code": command.get("exit_code"),
                            "output_sha256": command.get("output_sha256"),
                        }
                        for command in item.get("verification") or []
                    ],
                }
                for item in evidence
            ],
        })
        result["evidence"] = result_evidence
        if passed:
            return result
        if hard_failures and len(hard_failures) == len(evidence):
            result["verdict"] = "fail"
            result["issues"] = [
                *list(result.get("issues") or []),
                "所有 Worker 候选均无有效 diff 或确定性核验命令失败。",
            ]
            result["summary"] = (
                "确定性 Git diff/test 核验未通过；"
                + str(result.get("summary") or "")
            )[:20_000]
        elif unverified and result.get("verdict") == "pass":
            result["verdict"] = "unknown"
            result["issues"] = [
                *list(result.get("issues") or []),
                "候选存在 diff，但没有配置可执行的确定性核验命令。",
            ]
        return result

    def _remove_worktree(
        self,
        repository_root: Path,
        worktree_path: Path,
    ) -> None:
        owned = self._assert_owned_worktree_path(worktree_path)
        self._run_git(
            repository_root,
            "worktree",
            "remove",
            "--force",
            str(owned),
            allowed_exit_codes=(0, 128),
        )

    def apply_workspace(
        self,
        *,
        workspace_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        workspace = self.database.get_agent_workspace(workspace_id)
        if workspace is None or workspace["session_id"] != str(session_id):
            raise KeyError("候选 worktree 不存在")
        if workspace.get("apply_status") != "pending":
            raise GitWorkspaceError("该候选已经处理，不能重复应用")
        patch = workspace.get("diff_patch")
        if not isinstance(patch, bytes) or not patch:
            raise GitWorkspaceError("候选没有可应用的 Git diff")
        if hashlib.sha256(patch).hexdigest() != workspace.get("diff_sha256"):
            raise GitWorkspaceError("候选补丁哈希不一致，已拒绝应用")
        run = self.database.get_agent_run(
            workspace["run_id"], include_events=False
        )
        if (
            run is None
            or run["session_id"] != str(session_id)
            or run["status"] not in {"completed", "failed", "interrupted"}
        ):
            raise GitWorkspaceError("Agent Run 尚未结束，不能应用候选")
        repository = self.database.get_workspace_repository(
            workspace["repository_id"]
        )
        if repository is None or repository["session_id"] != str(session_id):
            raise GitWorkspaceError("候选对应的仓库登记已失效")

        with self._lock:
            inspected = self._inspect_repository(repository["root_path"])
            if inspected["head_commit"] != workspace["base_commit"]:
                raise GitWorkspaceError(
                    "主仓库 HEAD 已变化；请重新运行或人工合并候选"
                )
            if not inspected["clean"]:
                raise GitWorkspaceError(
                    "主仓库存在新改动；为避免覆盖用户工作，已拒绝应用"
                )
            root = inspected["root"]
            self._run_git(
                root,
                "apply",
                "--check",
                "--binary",
                "--whitespace=nowarn",
                "-",
                input_bytes=patch,
                timeout=60,
            )
            self._run_git(
                root,
                "apply",
                "--binary",
                "--whitespace=nowarn",
                "-",
                input_bytes=patch,
                timeout=60,
            )
            updated = self.database.update_agent_workspace_apply_status(
                workspace_id,
                apply_status="applied",
                status="applied",
            )
            self._remove_worktree(
                root,
                Path(workspace["root_path"]),
            )
        if updated is None:
            raise GitWorkspaceError("候选应用记录不存在")
        self.database.append_agent_event(
            workspace["run_id"],
            "workspace_applied",
            {
                "workspace_id": workspace_id,
                "diff_sha256": workspace.get("diff_sha256"),
                "changed_files": len(workspace.get("changed_files") or []),
            },
        )
        return self.public_workspace(updated)

    def reject_workspace(
        self,
        *,
        workspace_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        workspace = self.database.get_agent_workspace(workspace_id)
        if workspace is None or workspace["session_id"] != str(session_id):
            raise KeyError("候选 worktree 不存在")
        if workspace.get("apply_status") != "pending":
            raise GitWorkspaceError("该候选已经处理")
        repository = self.database.get_workspace_repository(
            workspace["repository_id"]
        )
        if repository is None:
            raise GitWorkspaceError("候选对应的仓库登记已失效")
        with self._lock:
            self._remove_worktree(
                Path(repository["root_path"]).resolve(),
                Path(workspace["root_path"]),
            )
            updated = self.database.update_agent_workspace_apply_status(
                workspace_id,
                apply_status="rejected",
                status="rejected",
            )
        if updated is None:
            raise GitWorkspaceError("候选拒绝记录不存在")
        self.database.append_agent_event(
            workspace["run_id"],
            "workspace_rejected",
            {"workspace_id": workspace_id},
        )
        return self.public_workspace(updated)


git_workspace_service = GitWorkspaceService()
