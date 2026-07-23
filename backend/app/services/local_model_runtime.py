import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..models.schemas import ProviderConfig


class LocalModelRuntimeError(RuntimeError):
    pass


class LocalModelRuntimeService:
    """Starts explicitly configured local model servers without invoking a shell."""

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    @staticmethod
    def _endpoint(config: ProviderConfig) -> tuple[str, int, str]:
        parsed = urlparse(config.base_url or "")
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise LocalModelRuntimeError("自动启动仅支持 http 回环地址")
        port = parsed.port
        if not port:
            raise LocalModelRuntimeError("本地模型接口地址必须包含端口")
        base = f"http://127.0.0.1:{port}{parsed.path.rstrip('/')}"
        return "127.0.0.1", port, base

    async def _probe(self, config: ProviderConfig) -> bool:
        try:
            _, _, base = self._endpoint(config)
            async with httpx.AsyncClient(trust_env=False, timeout=2.0) as client:
                response = await client.get(f"{base}/models")
            return response.status_code < 500
        except Exception:
            return False

    @staticmethod
    def _validate_file(value: str | None, label: str, *, executable: bool = False) -> Path:
        if not value:
            raise LocalModelRuntimeError(f"{label}未配置")
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise LocalModelRuntimeError(f"{label}不存在：{path}")
        if executable and not os.access(path, os.X_OK):
            raise LocalModelRuntimeError(f"{label}不可执行：{path}")
        return path

    def _command(self, config: ProviderConfig) -> list[str]:
        if config.local_runtime != "llama_cpp":
            raise LocalModelRuntimeError("本地端点未运行，且未配置受控启动方式")
        host, port, _ = self._endpoint(config)
        executable = self._validate_file(config.runtime_executable, "llama-server", executable=True)
        if executable.name != "llama-server":
            raise LocalModelRuntimeError("本地运行程序必须是 llama-server")
        model_path = self._validate_file(config.model_path, "模型文件")
        extra = list(config.runtime_args or [])
        blocked = {"--host", "--port", "--model", "-m"}
        if any(arg in blocked for arg in extra):
            raise LocalModelRuntimeError("附加参数不能覆盖 model、host 或 port")
        return [
            str(executable), "--model", str(model_path), "--host", host, "--port", str(port),
            *extra,
        ]

    async def ensure_ready(self, config: ProviderConfig) -> dict[str, Any]:
        if config.type != "local":
            return {"ready": True, "managed": False, "started": False}
        if await self._probe(config):
            return {"ready": True, "managed": config.name in self._processes, "started": False}
        if config.local_runtime == "external":
            _, port, _ = self._endpoint(config)
            raise LocalModelRuntimeError(
                f"本地模型服务未运行：127.0.0.1:{port}；请启动外部服务或配置 llama.cpp 自动启动"
            )

        key = config.base_url or config.name
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if await self._probe(config):
                return {"ready": True, "managed": config.name in self._processes, "started": False}
            existing = self._processes.get(config.name)
            if existing and existing.returncode is None:
                process = existing
            else:
                command = self._command(config)
                log_root = Path(os.environ.get("AETHERCHAT_DATA_DIR", Path.home() / ".aetherchat")) / "model-logs"
                log_root.mkdir(parents=True, exist_ok=True)
                log_path = log_root / f"{config.name}.log"
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

            timeout = max(5.0, min(float(config.startup_timeout or 300), 900.0))
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                if await self._probe(config):
                    return {
                        "ready": True,
                        "managed": True,
                        "started": True,
                        "pid": process.pid,
                    }
                if process.returncode is not None:
                    raise LocalModelRuntimeError(
                        f"本地模型进程启动失败（退出码 {process.returncode}），请查看 model-logs/{config.name}.log"
                    )
                await asyncio.sleep(0.5)
            if process.returncode is None:
                process.terminate()
            raise LocalModelRuntimeError(f"本地模型在 {int(timeout)} 秒内未就绪")

    async def status(self, config: ProviderConfig) -> dict[str, Any]:
        ready = await self._probe(config) if config.type == "local" else True
        process = self._processes.get(config.name)
        return {
            "ready": ready,
            "type": config.type,
            "base_url": config.base_url,
            "runtime": config.local_runtime,
            "managed": bool(process and process.returncode is None),
            "pid": process.pid if process and process.returncode is None else None,
        }

    async def shutdown(self):
        running = [process for process in self._processes.values() if process.returncode is None]
        for process in running:
            process.terminate()
        if running:
            await asyncio.gather(*(process.wait() for process in running), return_exceptions=True)
        self._processes.clear()


local_model_runtime = LocalModelRuntimeService()
