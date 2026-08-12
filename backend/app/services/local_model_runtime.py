import asyncio
import errno
import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..models.schemas import ProviderConfig


class LocalModelRuntimeError(RuntimeError):
    """Actionable local-runtime failure with optional structured fields."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        hints: list[str] | None = None,
        log_path: str | None = None,
        log_tail: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.hints = list(hints or [])
        self.log_path = log_path
        self.log_tail = log_tail

    def as_detail(self) -> str:
        parts = [str(self)]
        if self.log_tail:
            parts.append(f"日志摘录：{self.log_tail}")
        if self.log_path:
            parts.append(f"完整日志：{self.log_path}")
        if self.hints:
            parts.append("建议：" + "；".join(self.hints))
        return "。".join(parts)


class LocalModelRuntimeService:
    """Starts explicitly configured local model servers without invoking a shell."""

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    @staticmethod
    def _log_root() -> Path:
        return Path(os.environ.get("AETHERCHAT_DATA_DIR", Path.home() / ".aetherchat")) / "model-logs"

    def _log_path_for(self, name: str) -> Path:
        return self._log_root() / f"{name}.log"

    @staticmethod
    def _read_log_tail(log_path: Path, *, max_bytes: int = 900) -> str:
        try:
            if not log_path.is_file():
                return ""
            data = log_path.read_bytes()
            if not data:
                return ""
            chunk = data[-max_bytes:]
            text = chunk.decode("utf-8", errors="replace").strip()
            # Keep the last few non-empty lines for scanability.
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return " | ".join(lines[-6:])[:max_bytes]
        except OSError:
            return ""

    @staticmethod
    def _endpoint(config: ProviderConfig) -> tuple[str, int, str]:
        parsed = urlparse(config.base_url or "")
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise LocalModelRuntimeError(
                "自动启动仅支持 http 回环地址（127.0.0.1 / localhost）",
                code="non_loopback_base_url",
                hints=["把 Base URL 改成 http://127.0.0.1:端口/v1"],
            )
        port = parsed.port
        if not port:
            raise LocalModelRuntimeError(
                "本地模型接口地址必须包含端口，例如 http://127.0.0.1:8080/v1",
                code="missing_port",
                hints=["在 Base URL 中补上端口号"],
            )
        base = f"http://127.0.0.1:{port}{parsed.path.rstrip('/')}"
        return "127.0.0.1", port, base

    async def _probe(self, config: ProviderConfig) -> bool:
        try:
            _, _, base = self._endpoint(config)
            async with httpx.AsyncClient(trust_env=False, timeout=2.0) as client:
                response = await client.get(f"{base}/models")
            return response.is_success
        except Exception:
            return False

    @classmethod
    def _port_is_bound(cls, config: ProviderConfig) -> bool:
        """Return whether another process already owns the configured loopback port."""
        host, port, _ = cls._endpoint(config)
        probe_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe_socket.bind((host, port))
            return False
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                return True
            raise LocalModelRuntimeError(
                f"无法检查本地模型端口 {host}:{port}：{exc}",
                code="port_probe_failed",
            ) from exc
        finally:
            probe_socket.close()

    async def _wait_for_endpoint(self, config: ProviderConfig, timeout: float) -> bool:
        """Give an already-bound endpoint time to recover from a transient probe failure."""
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
        while True:
            if await self._probe(config):
                return True
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.5, remaining))

    @staticmethod
    def _validate_file(value: str | None, label: str, *, executable: bool = False) -> Path:
        if not value:
            raise LocalModelRuntimeError(
                f"{label}未配置",
                code="path_missing",
                hints=[f"在模型面板填写有效的{label}绝对路径"],
            )
        path = Path(value).expanduser()
        try:
            path = path.resolve()
        except OSError as exc:
            raise LocalModelRuntimeError(
                f"{label}路径无效：{value}（{exc}）",
                code="path_invalid",
                hints=[f"检查{label}是否为可解析的绝对路径"],
            ) from exc
        if not path.is_file():
            raise LocalModelRuntimeError(
                f"{label}不存在：{path}",
                code="path_not_found",
                hints=[
                    f"确认{label}文件仍在磁盘上",
                    "路径需为绝对路径（以 / 开头）",
                ],
            )
        if executable and not os.access(path, os.X_OK):
            raise LocalModelRuntimeError(
                f"{label}不可执行：{path}",
                code="not_executable",
                hints=[f"执行 chmod +x {path}"],
            )
        return path

    def diagnose_config(self, config: ProviderConfig) -> list[str]:
        """Return human-readable config problems without starting a process."""
        issues: list[str] = []
        if config.type != "local":
            return issues
        try:
            self._endpoint(config)
        except LocalModelRuntimeError as exc:
            issues.append(str(exc))
            return issues
        if config.local_runtime == "llama_cpp":
            try:
                self._validate_file(config.model_path, "模型文件")
            except LocalModelRuntimeError as exc:
                issues.append(str(exc))
            try:
                executable = self._validate_file(
                    config.runtime_executable, "llama-server", executable=True,
                )
                if executable.name != "llama-server":
                    issues.append("本地运行程序文件名必须是 llama-server")
            except LocalModelRuntimeError as exc:
                issues.append(str(exc))
        return issues

    def _command(self, config: ProviderConfig) -> list[str]:
        if config.local_runtime != "llama_cpp":
            raise LocalModelRuntimeError(
                "本地端点未运行，且未配置受控启动方式",
                code="runtime_not_managed",
                hints=["将 local_runtime 设为 llama_cpp，或先手动启动兼容服务"],
            )
        host, port, _ = self._endpoint(config)
        executable = self._validate_file(config.runtime_executable, "llama-server", executable=True)
        if executable.name != "llama-server":
            raise LocalModelRuntimeError(
                "本地运行程序必须是 llama-server",
                code="bad_executable_name",
                hints=["runtime_executable 应指向名为 llama-server 的二进制文件"],
            )
        model_path = self._validate_file(config.model_path, "模型文件")
        extra = list(config.runtime_args or [])
        blocked = {"--host", "--port", "--model", "-m"}
        if any(arg in blocked for arg in extra):
            raise LocalModelRuntimeError(
                "附加参数不能覆盖 model、host 或 port",
                code="blocked_runtime_args",
                hints=["从 runtime_args 中移除 --model / --host / --port / -m"],
            )
        return [
            str(executable), "--model", str(model_path), "--host", host, "--port", str(port),
            *extra,
        ]

    async def ensure_ready(self, config: ProviderConfig) -> dict[str, Any]:
        if config.type != "local":
            return {"ready": True, "managed": False, "started": False}
        if await self._probe(config):
            process = self._processes.get(config.name)
            managed = bool(process and process.returncode is None)
            if process and process.returncode is not None:
                self._processes.pop(config.name, None)
            return {"ready": True, "managed": managed, "started": False}
        if config.local_runtime == "external":
            _, port, _ = self._endpoint(config)
            raise LocalModelRuntimeError(
                f"本地模型服务未运行：127.0.0.1:{port}",
                code="external_not_running",
                hints=[
                    "先手动启动 OpenAI 兼容服务（如 llama-server / ollama / vLLM）",
                    "或把 local_runtime 改为 llama_cpp 交给 AetherChat 托管启动",
                    f"确认服务监听 http://127.0.0.1:{port}/v1/models",
                ],
            )

        key = config.base_url or config.name
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if await self._probe(config):
                process = self._processes.get(config.name)
                return {
                    "ready": True,
                    "managed": bool(process and process.returncode is None),
                    "started": False,
                }
            existing = self._processes.get(config.name)
            started = False
            log_path = self._log_path_for(config.name)
            if existing and existing.returncode is None:
                process = existing
            else:
                if existing:
                    self._processes.pop(config.name, None)
                host, port, base = self._endpoint(config)
                if self._port_is_bound(config):
                    adoption_timeout = max(
                        3.0,
                        min(float(config.startup_timeout or 300), 10.0),
                    )
                    if await self._wait_for_endpoint(config, adoption_timeout):
                        return {
                            "ready": True,
                            "managed": False,
                            "started": False,
                            "adopted": True,
                        }
                    raise LocalModelRuntimeError(
                        "本地模型端口已被占用，但 OpenAI 兼容接口未就绪："
                        f"{base}/models（{host}:{port}）",
                        code="port_in_use_incompatible",
                        hints=[
                            f"用 ss -ltnp | grep {port} 或 lsof -i :{port} 查看占用进程",
                            "停掉占用进程，或把模型 Base URL 改到空闲端口",
                            "若占用进程就是 llama-server，确认它已暴露 /v1/models",
                        ],
                    )
                command = self._command(config)
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_handle = log_path.open("ab", buffering=0)
                try:
                    process = await asyncio.create_subprocess_exec(
                        *command,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=log_handle,
                        stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                    )
                finally:
                    log_handle.close()
                self._processes[config.name] = process
                started = True

            timeout = max(5.0, min(float(config.startup_timeout or 300), 900.0))
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                if await self._probe(config):
                    return {
                        "ready": True,
                        "managed": True,
                        "started": started,
                        "pid": process.pid,
                        "log_path": str(log_path),
                    }
                if process.returncode is not None:
                    self._processes.pop(config.name, None)
                    if self._port_is_bound(config) and await self._wait_for_endpoint(config, 3.0):
                        return {
                            "ready": True,
                            "managed": False,
                            "started": False,
                            "adopted": True,
                        }
                    tail = self._read_log_tail(log_path)
                    raise LocalModelRuntimeError(
                        f"本地模型进程启动失败（退出码 {process.returncode}）",
                        code="process_exited",
                        log_path=str(log_path),
                        log_tail=tail or None,
                        hints=[
                            "核对 GGUF 路径与 llama-server 是否匹配当前架构/CUDA",
                            "增大可用显存或降低 --n-gpu-layers / 上下文",
                            f"打开日志文件查看完整输出：{log_path}",
                        ],
                    )
                await asyncio.sleep(0.5)
            if process.returncode is None:
                process.terminate()
            tail = self._read_log_tail(log_path)
            raise LocalModelRuntimeError(
                f"本地模型在 {int(timeout)} 秒内未就绪",
                code="startup_timeout",
                log_path=str(log_path),
                log_tail=tail or None,
                hints=[
                    "大模型首次加载可能较慢，可提高 startup_timeout",
                    "确认 GPU/驱动可用，或减少 --n-gpu-layers 做 CPU 回退试跑",
                    f"查看启动日志：{log_path}",
                ],
            )

    async def status(self, config: ProviderConfig) -> dict[str, Any]:
        if config.type != "local":
            return {
                "ready": True,
                "type": config.type,
                "base_url": config.base_url,
                "runtime": config.local_runtime,
                "managed": False,
                "started": False,
                "pid": None,
            }

        ready = await self._probe(config)
        process = self._processes.get(config.name)
        managed = bool(process and process.returncode is None)
        pid = process.pid if managed else None
        log_path = self._log_path_for(config.name)
        log_path_str = str(log_path) if log_path.is_file() else None
        message: str | None = None
        hints: list[str] = []
        code: str | None = None

        if not ready:
            config_issues = self.diagnose_config(config)
            if config_issues:
                message = "；".join(config_issues)
                code = "config_invalid"
                hints = ["打开「模型」面板修正路径后重试「启动并验证」"]
            else:
                try:
                    _, port, base = self._endpoint(config)
                except LocalModelRuntimeError as exc:
                    message = str(exc)
                    code = exc.code
                    hints = list(exc.hints)
                else:
                    if config.local_runtime == "external":
                        message = f"外部本地服务未响应：{base}/models"
                        code = "external_not_running"
                        hints = [
                            f"请先启动监听 127.0.0.1:{port} 的兼容服务",
                            "或改为 llama.cpp 托管模式",
                        ]
                    elif managed:
                        message = f"托管进程仍在启动（pid {pid}），接口尚未就绪"
                        code = "starting"
                        hints = ["稍候再点「重新验证」；若长时间无响应请查看启动日志"]
                    else:
                        message = f"本地服务未运行（端口 {port}）"
                        code = "not_running"
                        hints = ["点「启动并验证」由 AetherChat 拉起 llama-server"]

        return {
            "ready": ready,
            "type": config.type,
            "base_url": config.base_url,
            "runtime": config.local_runtime,
            "managed": managed,
            "started": False,
            "pid": pid,
            "message": message,
            "code": code,
            "hints": hints,
            "log_path": log_path_str,
            "log_tail": self._read_log_tail(log_path) if (not ready and log_path_str) else None,
        }

    async def shutdown(self):
        running = [process for process in self._processes.values() if process.returncode is None]
        for process in running:
            process.terminate()
        if running:
            await asyncio.gather(*(process.wait() for process in running), return_exceptions=True)
        self._processes.clear()


local_model_runtime = LocalModelRuntimeService()
