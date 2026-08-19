from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


class ComfyUIRuntimeError(RuntimeError):
    pass


class ComfyUIRuntimeService:
    """Owns a ComfyUI process only when AetherChat had to start it."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._managed_unit: str | None = None
        self._started_at: str | None = None
        self._command_label: str | None = None

    @staticmethod
    def _docker_container() -> str | None:
        if not sys.platform.startswith("linux") or shutil.which("docker") is None:
            return None
        container = os.getenv(
            "AETHERCHAT_COMFYUI_DOCKER_CONTAINER",
            "comfy-spark",
        ).strip()
        if not container:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", container):
            raise ComfyUIRuntimeError("ComfyUI Docker 容器名称无效")
        return container

    @staticmethod
    def _endpoint(base_url: str) -> tuple[str, int]:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.path not in {"", "/"}
        ):
            raise ComfyUIRuntimeError(
                "ComfyUI 自动启动仅支持不带路径的 HTTP 回环地址"
            )
        if not parsed.port:
            raise ComfyUIRuntimeError("ComfyUI 地址必须包含端口")
        return "127.0.0.1", parsed.port

    @staticmethod
    def _enabled() -> bool:
        return os.getenv("AETHERCHAT_COMFYUI_AUTOSTART", "1").strip().lower() not in {
            "0", "false", "no", "off",
        }

    @staticmethod
    def _root(workflow_roots: list[Path]) -> Path:
        configured = os.getenv("AETHERCHAT_COMFYUI_ROOT")
        if configured:
            return Path(configured).expanduser().resolve()
        for workflow_root in workflow_roots:
            for candidate in (workflow_root, *workflow_root.parents):
                if candidate.name.lower() == "comfyui" and (candidate / "main.py").is_file():
                    return candidate.resolve()
        return (Path.home() / "comfy-spark" / "ComfyUI").resolve()

    @staticmethod
    def _python(root: Path) -> Path:
        configured = os.getenv("AETHERCHAT_COMFYUI_PYTHON")
        candidates = [
            Path(configured).expanduser() if configured else None,
            root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"),
            root / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python"),
            root.parent / "sdvenv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
            Path(sys.executable) if not getattr(sys, "frozen", False) else None,
            Path(found) if (found := shutil.which("python3")) else None,
        ]
        for candidate in candidates:
            if candidate is None:
                continue
            # Keep the venv executable path intact. Resolving its `python`
            # symlink to /usr/bin/python discards pyvenv.cfg and silently runs
            # ComfyUI with the wrong dependency set.
            executable = candidate.expanduser().absolute()
            if executable.is_file() and os.access(executable, os.X_OK):
                return executable
        raise ComfyUIRuntimeError("找不到可执行的 ComfyUI Python 环境")

    @staticmethod
    def _systemd_unit() -> str | None:
        if not sys.platform.startswith("linux"):
            return None
        configured = os.getenv("AETHERCHAT_COMFYUI_SYSTEMD_UNIT", "").strip()
        unit = configured or "aetherchat-comfyui.service"
        if not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", unit):
            raise ComfyUIRuntimeError("ComfyUI systemd user unit 名称无效")
        if configured:
            return unit
        candidates = (
            Path.home() / ".config/systemd/user" / unit,
            Path("/etc/systemd/user") / unit,
            Path("/usr/lib/systemd/user") / unit,
        )
        return unit if any(path.is_file() for path in candidates) else None

    def _command(
        self,
        base_url: str,
        workflow_roots: list[Path],
    ) -> tuple[list[str], Path]:
        host, port = self._endpoint(base_url)
        root = self._root(workflow_roots)
        main = (root / "main.py").resolve()
        if not main.is_file() or main.parent != root:
            raise ComfyUIRuntimeError(f"ComfyUI main.py 不存在：{main}")
        python = self._python(root)
        extra = shlex.split(os.getenv("AETHERCHAT_COMFYUI_ARGS", ""))
        blocked = {"--listen", "--port"}
        if any(value in blocked for value in extra):
            raise ComfyUIRuntimeError(
                "AETHERCHAT_COMFYUI_ARGS 不能覆盖 --listen 或 --port"
            )
        return [
            str(python),
            str(main),
            "--listen",
            host,
            "--port",
            str(port),
            *extra,
        ], root

    async def _probe(self, base_url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
                response = await client.get(f"{base_url}/system_stats")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    @staticmethod
    async def _systemctl(
        *arguments: str,
        timeout: float = 45,
    ) -> tuple[int, str]:
        try:
            process = await asyncio.create_subprocess_exec(
                "systemctl",
                "--user",
                *arguments,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except (OSError, TimeoutError):
            return 127, "systemctl --user 不可用或超时"
        return process.returncode or 0, output.decode(
            "utf-8",
            errors="replace",
        ).strip()

    @staticmethod
    async def _command_output(
        *command: str,
        timeout: float = 10,
    ) -> tuple[int, str]:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except (OSError, TimeoutError):
            return 127, f"{command[0]} 不可用或超时"
        return process.returncode or 0, output.decode(
            "utf-8",
            errors="replace",
        ).strip()

    async def _docker_diagnostics(self, base_url: str) -> dict[str, Any] | None:
        container = self._docker_container()
        if container is None:
            return None
        returncode, output = await self._command_output(
            "docker",
            "inspect",
            "--format",
            "{{json .State}}|{{.HostConfig.Memory}}",
            container,
        )
        if returncode != 0:
            return None
        state_json, _, memory_text = output.rpartition("|")
        try:
            state = json.loads(state_json)
        except (TypeError, ValueError):
            state = {}
        try:
            memory_limit = int(memory_text or 0)
        except ValueError:
            memory_limit = 0
        _, port = self._endpoint(base_url)
        process_running = False
        if str(state.get("Status") or "").lower() == "running":
            process_running = (
                await self._command_output(
                    "docker",
                    "exec",
                    container,
                    "pgrep",
                    "-f",
                    rf"^python(3)? main.py .*--port {port}([[:space:]]|$)",
                )
            )[0] == 0
        return {
            "container": container,
            "status": str(state.get("Status") or "unknown"),
            "oom_killed": bool(state.get("OOMKilled")),
            "exit_code": state.get("ExitCode"),
            "memory_limit_bytes": memory_limit or None,
            "comfyui_process_running": process_running,
        }

    async def diagnostics(self, base_url: str) -> dict[str, Any]:
        self._endpoint(base_url)
        ready = await self._probe(base_url)
        unit = self._systemd_unit()
        unit_active = await self._unit_active(unit) if unit is not None else False
        docker = (
            await self._docker_diagnostics(base_url)
            if unit is not None
            or bool(os.getenv("AETHERCHAT_COMFYUI_DOCKER_CONTAINER", "").strip())
            else None
        )
        reason = "ready" if ready else "endpoint_unreachable"
        message = "ComfyUI HTTP 接口已就绪。" if ready else "ComfyUI HTTP 接口当前不可访问。"
        if not ready and docker is not None:
            process_running = bool(docker.get("comfyui_process_running"))
            if not process_running and docker.get("oom_killed"):
                reason = "process_oom_killed"
                message = (
                    "Docker 容器仍在运行，但 ComfyUI Python 进程已因内存不足退出；"
                    "请改用较小模型，或先停止占用统一内存的本地大模型后重启 ComfyUI。"
                )
            elif not process_running:
                reason = "process_missing"
                message = (
                    "Docker 容器和 systemd 单元可能仍显示运行，但容器内没有 ComfyUI Python 进程。"
                )
            else:
                reason = "process_starting"
                message = "ComfyUI Python 进程存在，但 HTTP 接口仍在启动。"
        elif not ready and unit_active:
            reason = "unit_active_endpoint_unreachable"
            message = "ComfyUI systemd 单元显示 active，但 HTTP 接口不可访问。"
        return {
            "ready": ready,
            "reason": reason,
            "message": message,
            "unit": unit,
            "unit_active": unit_active,
            "docker": docker,
        }

    async def _unit_active(self, unit: str) -> bool:
        return (await self._systemctl("is-active", "--quiet", unit))[0] == 0

    async def _start_unit(self, unit: str) -> tuple[bool, bool, str]:
        already_active = await self._unit_active(unit)
        action = "restart" if already_active else "start"
        returncode, output = await self._systemctl(action, unit)
        if returncode != 0:
            return False, already_active, output
        if not already_active:
            self._managed_unit = unit
            self._started_at = datetime.now(UTC).isoformat()
            self._command_label = f"systemctl --user {action} {unit}"
        return True, already_active, output

    async def _stop_unit(self, unit: str) -> tuple[bool, str]:
        returncode, output = await self._systemctl("stop", unit)
        return returncode == 0, output

    @staticmethod
    def _signal_process(process: asyncio.subprocess.Process, force: bool = False) -> None:
        try:
            if os.name != "nt":
                os.killpg(
                    process.pid,
                    signal.SIGKILL if force else signal.SIGTERM,
                )
            elif force:
                process.kill()
            else:
                process.terminate()
        except ProcessLookupError:
            return

    async def _stop_process(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        if process.returncode is not None:
            return
        self._signal_process(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=12)
        except TimeoutError:
            self._signal_process(process, force=True)
            await process.wait()

    async def ensure_ready(
        self,
        base_url: str,
        workflow_roots: list[Path],
    ) -> dict[str, Any]:
        self._endpoint(base_url)
        if await self._probe(base_url):
            return await self.status(base_url)
        if not self._enabled():
            raise ComfyUIRuntimeError(
                "ComfyUI 未运行，且 AETHERCHAT_COMFYUI_AUTOSTART 已关闭"
            )
        async with self._lock:
            if await self._probe(base_url):
                return await self.status(base_url)
            process = self._process
            unit_for_wait = self._managed_unit
            systemd_error = ""
            if (
                unit_for_wait is None
                and (process is None or process.returncode is not None)
            ):
                unit = self._systemd_unit()
                if unit is not None:
                    started, _already_active, systemd_error = (
                        await self._start_unit(unit)
                    )
                    if started:
                        unit_for_wait = unit
            if (
                unit_for_wait is None
                and (process is None or process.returncode is not None)
            ):
                command, root = self._command(base_url, workflow_roots)
                from ..data_dir import default_data_dir

                data_root = default_data_dir().expanduser().resolve()
                log_root = data_root / "comfyui-logs"
                log_root.mkdir(parents=True, exist_ok=True)
                log_handle = (log_root / "comfyui.log").open("ab", buffering=0)
                try:
                    process = await asyncio.create_subprocess_exec(
                        *command,
                        cwd=root,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=log_handle,
                        stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                    )
                finally:
                    log_handle.close()
                self._process = process
                self._started_at = datetime.now(UTC).isoformat()
                self._command_label = f"{Path(command[0]).name} main.py"

            timeout = max(
                10.0,
                min(float(os.getenv("AETHERCHAT_COMFYUI_STARTUP_TIMEOUT", "180")), 900.0),
            )
            deadline = asyncio.get_running_loop().time() + timeout
            next_process_check = asyncio.get_running_loop().time() + 3
            while asyncio.get_running_loop().time() < deadline:
                if await self._probe(base_url):
                    return await self.status(base_url)
                if process is not None and process.returncode is not None:
                    raise ComfyUIRuntimeError(
                        "ComfyUI 启动失败"
                        f"（退出码 {process.returncode}），请查看 comfyui-logs/comfyui.log"
                    )
                if (
                    unit_for_wait is not None
                    and not await self._unit_active(unit_for_wait)
                ):
                    if self._managed_unit == unit_for_wait:
                        self._managed_unit = None
                        self._started_at = None
                        self._command_label = None
                    raise ComfyUIRuntimeError(
                        f"ComfyUI systemd user unit 已停止：{unit_for_wait}"
                    )
                if (
                    unit_for_wait is not None
                    and asyncio.get_running_loop().time() >= next_process_check
                ):
                    diagnostic = await self.diagnostics(base_url)
                    if diagnostic["reason"] in {
                        "process_oom_killed",
                        "process_missing",
                    }:
                        raise ComfyUIRuntimeError(str(diagnostic["message"]))
                    next_process_check = asyncio.get_running_loop().time() + 2
                await asyncio.sleep(0.5)
            if process is not None:
                await self._stop_process(process)
                self._process = None
            if self._managed_unit is not None:
                await self._stop_unit(self._managed_unit)
                self._managed_unit = None
            self._started_at = None
            self._command_label = None
            diagnostic = await self.diagnostics(base_url)
            detail = (
                f"；systemd 回退原因：{systemd_error}"
                if systemd_error
                else ""
            )
            raise ComfyUIRuntimeError(
                f"ComfyUI 在 {int(timeout)} 秒内未就绪：{diagnostic['message']}{detail}"
            )

    async def status(self, base_url: str) -> dict[str, Any]:
        process = self._process
        managed_process = (
            process
            if process is not None and process.returncode is None
            else None
        )
        managed_unit = self._managed_unit
        unit_active = (
            await self._unit_active(managed_unit)
            if managed_unit is not None
            else False
        )
        managed = managed_process is not None or unit_active
        diagnostic = await self.diagnostics(base_url)
        return {
            "ready": diagnostic["ready"],
            "managed": managed,
            "started_by_aetherchat": managed,
            "pid": managed_process.pid if managed_process is not None else None,
            "unit": managed_unit if unit_active else None,
            "runtime": (
                "systemd_user"
                if unit_active
                else "process" if managed_process is not None else "external"
            ),
            "started_at": self._started_at if managed else None,
            "command": self._command_label if managed else None,
            "autostart_enabled": self._enabled(),
            "diagnostics": diagnostic,
        }

    async def shutdown(self) -> dict[str, Any]:
        async with self._lock:
            process = self._process
            unit = self._managed_unit
            stopped_pid: int | None = None
            if process is not None and process.returncode is None:
                await self._stop_process(process)
                stopped_pid = process.pid
            self._process = None
            if unit is not None:
                stopped, output = await self._stop_unit(unit)
                if not stopped:
                    return {
                        "stopped": False,
                        "reason": "systemd_stop_failed",
                        "unit": unit,
                        "error": output,
                    }
                self._managed_unit = None
            if stopped_pid is None and unit is None:
                return {"stopped": False, "reason": "not_managed"}
            self._started_at = None
            self._command_label = None
            return {
                "stopped": True,
                "pid": stopped_pid,
                "unit": unit,
            }


comfyui_runtime = ComfyUIRuntimeService()
