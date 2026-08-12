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
    monkeypatch.setattr(runtime, "_port_is_bound", lambda _config: False)
    monkeypatch.setattr(runtime, "_command", lambda _config: ["/usr/local/bin/llama-server"])
    monkeypatch.setattr("asyncio.create_subprocess_exec", launch)
    result = await runtime.ensure_ready(config())
    assert result["ready"] is True
    assert result["started"] is True
    assert result["pid"] == 321
    assert len(launches) == 1
    assert launches[0][1]["start_new_session"] is True


@pytest.mark.asyncio
async def test_managed_runtime_adopts_an_existing_server_after_transient_probe_failure(monkeypatch):
    runtime = LocalModelRuntimeService()
    probes = iter([False, False])
    launches = []

    async def probe(_config):
        return next(probes)

    async def wait_for_endpoint(_config, timeout):
        assert timeout == 5
        return True

    async def launch(*args, **kwargs):
        launches.append((args, kwargs))

    monkeypatch.setattr(runtime, "_probe", probe)
    monkeypatch.setattr(runtime, "_port_is_bound", lambda _config: True)
    monkeypatch.setattr(runtime, "_wait_for_endpoint", wait_for_endpoint)
    monkeypatch.setattr("asyncio.create_subprocess_exec", launch)

    result = await runtime.ensure_ready(config())

    assert result == {
        "ready": True,
        "managed": False,
        "started": False,
        "adopted": True,
    }
    assert launches == []


@pytest.mark.asyncio
async def test_managed_runtime_does_not_launch_into_an_occupied_incompatible_port(monkeypatch):
    runtime = LocalModelRuntimeService()
    launches = []

    async def unavailable(_config):
        return False

    async def still_unavailable(_config, _timeout):
        return False

    async def launch(*args, **kwargs):
        launches.append((args, kwargs))

    monkeypatch.setattr(runtime, "_probe", unavailable)
    monkeypatch.setattr(runtime, "_port_is_bound", lambda _config: True)
    monkeypatch.setattr(runtime, "_wait_for_endpoint", still_unavailable)
    monkeypatch.setattr("asyncio.create_subprocess_exec", launch)

    with pytest.raises(LocalModelRuntimeError, match="端口已被占用"):
        await runtime.ensure_ready(config())

    assert launches == []


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


def test_diagnose_config_reports_missing_model_file(tmp_path):
    runtime = LocalModelRuntimeService()
    missing = tmp_path / "nope.gguf"
    issues = runtime.diagnose_config(config(
        model_path=str(missing),
        runtime_executable=str(tmp_path / "llama-server"),
    ))
    assert any("模型文件不存在" in item or "不存在" in item for item in issues)


def test_error_as_detail_includes_hints_and_log(tmp_path):
    log_path = tmp_path / "qwen.log"
    log_path.write_text("line1\nCUDA OOM\n", encoding="utf-8")
    err = LocalModelRuntimeError(
        "本地模型进程启动失败（退出码 1）",
        code="process_exited",
        hints=["降低 n-gpu-layers"],
        log_path=str(log_path),
        log_tail="CUDA OOM",
    )
    detail = err.as_detail()
    assert "退出码 1" in detail
    assert "CUDA OOM" in detail
    assert "降低 n-gpu-layers" in detail
    assert str(log_path) in detail


@pytest.mark.asyncio
async def test_status_reports_actionable_message_when_not_ready(monkeypatch, tmp_path):
    runtime = LocalModelRuntimeService()

    async def unavailable(_config):
        return False

    monkeypatch.setattr(runtime, "_probe", unavailable)
    cfg = config(
        model_path=str(tmp_path / "missing.gguf"),
        runtime_executable=str(tmp_path / "llama-server"),
    )
    status = await runtime.status(cfg)
    assert status["ready"] is False
    assert status.get("message")
    assert status.get("code") in {"config_invalid", "path_not_found", None} or "不存在" in (status.get("message") or "")
    assert status.get("hints")


@pytest.mark.asyncio
async def test_process_exit_includes_log_tail(monkeypatch, tmp_path):
    runtime = LocalModelRuntimeService()
    log_path = tmp_path / "local-qwen.log"
    monkeypatch.setattr(runtime, "_log_path_for", lambda name: log_path)

    async def probe(_config):
        return False

    class FakeProcess:
        pid = 9
        returncode = 1

        def terminate(self):
            pass

    async def launch(*_args, **_kwargs):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("gguf load failed\n", encoding="utf-8")
        return FakeProcess()

    monkeypatch.setattr(runtime, "_probe", probe)
    monkeypatch.setattr(runtime, "_port_is_bound", lambda _config: False)
    monkeypatch.setattr(runtime, "_command", lambda _config: ["/bin/llama-server"])
    monkeypatch.setattr("asyncio.create_subprocess_exec", launch)

    with pytest.raises(LocalModelRuntimeError) as raised:
        await runtime.ensure_ready(config(startup_timeout=5))
    err = raised.value
    assert err.code == "process_exited"
    assert err.log_tail and "gguf" in err.log_tail.lower()
    assert "日志摘录" in err.as_detail() or "gguf" in err.as_detail().lower()
