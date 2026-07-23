import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
os.environ.setdefault("AETHERCHAT_DATA_DIR", tempfile.mkdtemp(prefix="aetherchat-runtime-tests-"))

from app.models.schemas import ProviderConfig
from app.services.local_model_runtime import LocalModelRuntimeError, LocalModelRuntimeService


def config(**overrides):
    values = {
        "name": "local-qwen",
        "type": "local",
        "model": "qwen.gguf",
        "base_url": "http://127.0.0.1:8080/v1",
        "api_key_ref": "LOCAL_TEST_KEY",
        "local_runtime": "llama_cpp",
        "model_path": "/models/qwen.gguf",
        "runtime_executable": "/usr/local/bin/llama-server",
        "runtime_args": ["--ctx-size", "8192"],
        "startup_timeout": 5,
    }
    values.update(overrides)
    return ProviderConfig(**values)


@pytest.mark.asyncio
async def test_cloud_provider_never_starts_a_local_process(monkeypatch):
    runtime = LocalModelRuntimeService()
    called = False

    async def create(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("asyncio.create_subprocess_exec", create)
    result = await runtime.ensure_ready(config(type="openai_compat", local_runtime="external"))
    assert result == {"ready": True, "managed": False, "started": False}
    assert called is False


@pytest.mark.asyncio
async def test_external_local_endpoint_reports_actionable_error(monkeypatch):
    runtime = LocalModelRuntimeService()

    async def unavailable(_config):
        return False

    monkeypatch.setattr(runtime, "_probe", unavailable)
    with pytest.raises(LocalModelRuntimeError, match="127.0.0.1:8080"):
        await runtime.ensure_ready(config(local_runtime="external"))


@pytest.mark.asyncio
async def test_managed_runtime_starts_once_and_waits_until_ready(monkeypatch):
    runtime = LocalModelRuntimeService()
    probes = iter([False, False, True])
    launches = []

    async def probe(_config):
        return next(probes)

    class FakeProcess:
        pid = 321
        returncode = None

        def terminate(self):
            self.returncode = 0

        async def wait(self):
            return self.returncode

    async def launch(*command, **kwargs):
        launches.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(runtime, "_probe", probe)
    monkeypatch.setattr(runtime, "_command", lambda _config: ["/usr/local/bin/llama-server"])
    monkeypatch.setattr("asyncio.create_subprocess_exec", launch)
    result = await runtime.ensure_ready(config())
    assert result["ready"] is True
    assert result["started"] is True
    assert result["pid"] == 321
    assert len(launches) == 1
    assert launches[0][1]["start_new_session"] is True


@pytest.mark.asyncio
async def test_managed_runtime_rejects_non_loopback_endpoint(monkeypatch):
    runtime = LocalModelRuntimeService()

    async def unavailable(_config):
        return False

    monkeypatch.setattr(runtime, "_probe", unavailable)
    with pytest.raises(LocalModelRuntimeError, match="回环地址"):
        await runtime.ensure_ready(config(base_url="http://192.168.1.20:8080/v1"))


def test_runtime_args_cannot_override_security_owned_fields(monkeypatch):
    runtime = LocalModelRuntimeService()
    monkeypatch.setattr(runtime, "_validate_file", lambda value, label, executable=False: SimpleNamespace(
        name="llama-server" if executable else "qwen.gguf",
        __str__=lambda self: value,
    ))
    with pytest.raises(LocalModelRuntimeError, match="不能覆盖"):
        runtime._command(config(runtime_args=["--host", "0.0.0.0"]))
