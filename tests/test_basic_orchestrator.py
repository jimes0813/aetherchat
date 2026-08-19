import asyncio
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.models.schemas import ChatMessage, ProviderConfig, ToolCall, ToolDefinition  # noqa: E402
from app.providers import base as provider_module  # noqa: E402
from app.services import git_workspace_service as git_workspace_module  # noqa: E402
from app.services import orchestrator as orchestrator_module  # noqa: E402
from app.services.agent_run_service import AgentRunService  # noqa: E402
from app.services.approval_service import approval_service  # noqa: E402
from app.services.chat_database import (  # noqa: E402
    ChatDatabase,
)
from app.services.git_workspace_service import (  # noqa: E402
    GitWorkspaceService,
)
from app.services.knowledge_service import knowledge_service  # noqa: E402
from app.services.orchestrator import SessionOrchestrator  # noqa: E402
from app.services.plugin_registry import plugin_registry  # noqa: E402
from app.services.tool_registry import tool_registry  # noqa: E402


# Split from tests/test_basic.py for domain isolation.
# Shared env/client fixtures live in tests/conftest.py.

def _initialize_test_git_repository(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(root), "init", "-q"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "tests@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "AetherChat Tests"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "core.autocrlf", "false"],
        check=True,
    )
    (root / "app.py").write_bytes(b"VALUE = 1\n")
    subprocess.run(["git", "-C", str(root), "add", "app.py"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "initial"],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()


class _AsyncChunkStream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def test_openai_native_tool_results_remain_contiguous_before_visual_observation():
    converted = provider_module._openai_messages([
        ChatMessage(
            role="assistant",
            tool_calls=[
                ToolCall(id="call-a", name="observe", arguments={}),
                ToolCall(id="call-b", name="inspect", arguments={"target": "window"}),
            ],
        ),
        ChatMessage(
            role="tool",
            name="observe",
            tool_call_id="call-a",
            content='{"ok": true}',
            images=["data:image/png;base64,aW1hZ2U="],
        ),
        ChatMessage(
            role="tool",
            name="inspect",
            tool_call_id="call-b",
            content='{"window": "editor"}',
        ),
    ], include_images=True)

    assert [message["role"] for message in converted] == [
        "assistant",
        "tool",
        "tool",
        "user",
    ]
    assert [message["tool_call_id"] for message in converted[1:3]] == [
        "call-a",
        "call-b",
    ]
    assert converted[3]["content"][1] == {
        "type": "image_url",
        "image_url": {
            "url": "data:image/png;base64,aW1hZ2U=",
            "detail": "auto",
        },
    }


@pytest.mark.asyncio
async def test_openai_provider_streams_tools_and_allows_local_without_key(monkeypatch):
    captured = {}

    def fake_http_client(**kwargs):
        captured["http_client"] = kwargs
        return "local-http-client"

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kwargs):
            captured["request"] = kwargs
            return _AsyncChunkStream([
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="first "))],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(
                        content="second",
                        tool_calls=[SimpleNamespace(
                            index=0,
                            id="call-1",
                            function=SimpleNamespace(name="list_tasks", arguments="{}"),
                        )],
                    ))],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[],
                    usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4),
                ),
            ])

    monkeypatch.setattr(provider_module, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr(provider_module, "DefaultAsyncHttpxClient", fake_http_client)
    async def fake_runtime_ready(_config):
        return {"ready": True, "managed": False, "started": False}
    monkeypatch.setattr(provider_module.local_model_runtime, "ensure_ready", fake_runtime_ready)
    monkeypatch.delenv("UNSET_LOCAL_TEST_KEY", raising=False)
    provider = provider_module.OpenAICompatProvider(ProviderConfig(
        name="local-test",
        type="local",
        model="qwen-local-model",
        base_url="http://127.0.0.1:9999/v1",
        api_key_ref="UNSET_LOCAL_TEST_KEY",
    ))
    tools = [ToolDefinition(name="list_tasks", description="List tasks")]

    chunks = [
        chunk
        async for chunk in provider.stream_chat(
            [
                ChatMessage(role="system", content="first instruction"),
                ChatMessage(role="user", content="hello"),
                ChatMessage(
                    role="assistant",
                    content="checking",
                    tool_calls=[ToolCall(
                        id="historic-call",
                        name="list_tasks",
                        arguments={"status": "open"},
                    )],
                ),
                ChatMessage(
                    role="tool",
                    name="list_tasks",
                    tool_call_id="historic-call",
                    content='{"tasks": []}',
                ),
                ChatMessage(role="system", content="second instruction"),
            ],
            tools=tools,
            tool_choice="required",
        )
    ]

    assert captured["client"]["api_key"] == "local-model"
    assert captured["client"]["http_client"] == "local-http-client"
    assert captured["http_client"]["trust_env"] is False
    assert captured["request"]["stream"] is True
    assert captured["request"]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert captured["request"]["messages"] == [
        {"role": "system", "content": "first instruction\n\nsecond instruction"},
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "checking",
            "tool_calls": [{
                "id": "historic-call",
                "type": "function",
                "function": {
                    "name": "list_tasks",
                    "arguments": '{"status": "open"}',
                },
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "historic-call",
            "content": '{"tasks": []}',
        },
    ]
    assert captured["request"]["tools"][0]["function"]["name"] == "list_tasks"
    assert captured["request"]["tool_choice"] == "required"
    assert [chunk["delta"] for chunk in chunks if chunk["delta"]] == ["first ", "second"]
    assert sum(bool(chunk.get("tool_activity")) for chunk in chunks) == 1
    assert next(chunk["tool_calls"] for chunk in chunks if chunk.get("tool_calls")) == [{
        "id": "call-1",
        "name": "list_tasks",
        "arguments": {},
    }]
    assert chunks[-1]["usage"] == {"prompt_tokens": 12, "completion_tokens": 4}


@pytest.mark.asyncio
async def test_anthropic_provider_accepts_shared_tool_definitions(monkeypatch):
    captured = {}

    class FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.messages = SimpleNamespace(create=self.create)

        async def create(self, **kwargs):
            captured["request"] = kwargs
            return _AsyncChunkStream([
                SimpleNamespace(
                    type="message_start",
                    message=SimpleNamespace(usage=SimpleNamespace(input_tokens=20)),
                ),
                SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(text="ok"),
                ),
                SimpleNamespace(
                    type="content_block_start",
                    index=1,
                    content_block=SimpleNamespace(
                        type="tool_use",
                        id="toolu-new",
                        name="read_artifact",
                        input={},
                    ),
                ),
                SimpleNamespace(
                    type="content_block_delta",
                    index=1,
                    delta=SimpleNamespace(partial_json='{"path":"next.md"}'),
                ),
                SimpleNamespace(
                    type="message_delta",
                    usage=SimpleNamespace(output_tokens=5),
                ),
            ])

    monkeypatch.setattr(provider_module, "AsyncAnthropic", FakeAsyncAnthropic)
    monkeypatch.setenv("ANTHROPIC_OFFLINE_TEST_KEY", "fake-offline-key")
    provider = provider_module.AnthropicProvider(ProviderConfig(
        name="claude-test",
        type="anthropic",
        model="claude-offline",
        api_key_ref="ANTHROPIC_OFFLINE_TEST_KEY",
    ))

    chunks = [
        chunk
        async for chunk in provider.stream_chat(
            [
                ChatMessage(role="system", content="first instruction"),
                ChatMessage(role="user", content="hello"),
                ChatMessage(
                    role="assistant",
                    content="checking",
                    tool_calls=[
                        ToolCall(
                            id="toolu-success",
                            name="read_artifact",
                            arguments={"path": "notes.md"},
                        ),
                        ToolCall(
                            id="toolu-error",
                            name="read_artifact",
                            arguments={"path": "missing.md"},
                        ),
                    ],
                ),
                ChatMessage(
                    role="tool",
                    name="read_artifact",
                    tool_call_id="toolu-success",
                    content='{"text": "done"}',
                ),
                ChatMessage(
                    role="tool",
                    name="read_artifact",
                    tool_call_id="toolu-error",
                    tool_error=True,
                    content="not found",
                    images=["data:image/png;base64,aW1hZ2U="],
                ),
                ChatMessage(role="system", content="second instruction"),
            ],
            tools=[ToolDefinition(name="read_artifact", description="Read")],
            tool_choice="required",
        )
    ]

    assert captured["request"]["system"] == "first instruction\n\nsecond instruction"
    assert captured["request"]["tools"] == [{
        "name": "read_artifact",
        "description": "Read",
        "input_schema": {"type": "object", "properties": {}},
    }]
    assert captured["request"]["tool_choice"] == {"type": "any"}
    assert captured["request"]["messages"] == [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "checking"},
                {
                    "type": "tool_use",
                    "id": "toolu-success",
                    "name": "read_artifact",
                    "input": {"path": "notes.md"},
                },
                {
                    "type": "tool_use",
                    "id": "toolu-error",
                    "name": "read_artifact",
                    "input": {"path": "missing.md"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu-success",
                    "content": '{"text": "done"}',
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu-error",
                    "content": [
                        {"type": "text", "text": "not found"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "aW1hZ2U=",
                            },
                        },
                    ],
                    "is_error": True,
                },
            ],
        },
    ]
    assert chunks[0]["delta"] == "ok"
    assert sum(bool(chunk.get("tool_activity")) for chunk in chunks) == 1
    assert next(chunk["tool_calls"] for chunk in chunks if chunk.get("tool_calls")) == [{
        "id": "toolu-new",
        "name": "read_artifact",
        "arguments": {"path": "next.md"},
    }]
    assert chunks[-1]["usage"] == {"prompt_tokens": 20, "completion_tokens": 5}


class _FakeProvider:
    def __init__(self, name, *, cost_in=0.0, cost_out=0.0, behavior="normal", started=None, persona=None):
        self.config = ProviderConfig(
            name=name,
            type="openai_compat",
            model=f"{name}-model",
            api_key_ref=f"{name.upper()}_OFFLINE_KEY",
            cost_per_1k_input=cost_in,
            cost_per_1k_output=cost_out,
            persona=persona,
        )
        self.behavior = behavior
        self.started = started
        self.called = False
        self.received_messages = []

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.called = True
        self.received_messages = messages
        if self.started:
            self.started.set()
        if self.behavior == "error":
            if False:
                yield {}
            raise RuntimeError("offline provider failure")
        if self.behavior == "slow":
            for index in range(8):
                await asyncio.sleep(0.025)
                yield {"delta": f"{self.config.name}-{index}", "usage": None}
        else:
            await asyncio.sleep(0.01)
            yield {"delta": f"{self.config.name}-done", "usage": None}
        yield {"delta": "", "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}


async def _collect_events(orchestrator, targets):
    return [
        event
        async for event in orchestrator.stream_user_message({
            "content": "offline orchestration",
            "targets": targets,
            "mode": "casual",
        })
    ]


def _orchestrator_with(providers):
    orchestrator = SessionOrchestrator(str(uuid4()))
    orchestrator.providers = {provider.config.name: provider for provider in providers}
    orchestrator.refresh_providers = lambda: None
    return orchestrator


def test_orchestrator_routes_tools_and_caps_local_active_context(monkeypatch):
    orchestrator = SessionOrchestrator(str(uuid4()))
    definitions = orchestrator._available_tools()
    assert len(definitions) > 10

    plain = orchestrator._route_tools("请解释这段概念", definitions)
    comfy = orchestrator._route_tools("请用 ComfyUI 生成一张图片", definitions)
    bigasp = orchestrator._route_tools(
        "现在使用comfyui 用bigaspv2.safetensors模型来进行图片生成",
        definitions,
    )
    status_only = orchestrator._route_tools(
        "请检查 ComfyUI 模型和 LTX 状态，不要生成任何图片或视频",
        definitions,
    )
    planned_status_only = orchestrator._route_tools(
        "请检查 ComfyUI 模型和 LTX 状态，不要生成任何图片或视频",
        definitions,
        {
            "plan_step_id": "synthesizer",
            "instruction": "生成单一最终交付，并核验图像模型数量",
        },
    )
    workflow = orchestrator._route_tools(
        "请运行 ComfyUI 工作流 demo",
        definitions,
    )
    negated_workflow = orchestrator._route_tools(
        "不要运行 ComfyUI 工作流，只检查状态",
        definitions,
    )
    all_tools = orchestrator._route_tools("列出全部工具", definitions)
    assert plain == []
    assert comfy
    assert all(
        tool.name.startswith("plugin__comfyui__")
        for tool in comfy
    )
    assert [tool.name for tool in bigasp] == [
        "plugin__comfyui__generate_image"
    ]
    assert [tool.name for tool in status_only] == [
        "plugin__comfyui__status"
    ]
    assert not {
        "plugin__comfyui__generate_image",
        "plugin__comfyui__generate_video",
        "plugin__comfyui__run_workflow",
    } & {tool.name for tool in planned_status_only}
    assert "plugin__comfyui__run_workflow" in {
        tool.name for tool in workflow
    }
    assert "plugin__comfyui__run_workflow" not in {
        tool.name for tool in negated_workflow
    }
    assert len(comfy) < len(all_tools) == len(definitions)

    monkeypatch.setenv("AETHERCHAT_LOCAL_ACTIVE_CONTEXT_TOKENS", "8192")
    monkeypatch.setenv("AETHERCHAT_LOCAL_MAX_OUTPUT_TOKENS", "2048")
    local = SimpleNamespace(config=ProviderConfig(
        name="local-performance",
        type="local",
        model="qwen-35b",
        api_key_ref="LOCAL_PERFORMANCE_KEY",
        max_tokens=8192,
        token_limit=253952,
    ))
    provider_limit, output_reserve, tool_tokens = orchestrator._context_limits(
        local,
        [],
    )
    assert provider_limit == 8192
    assert output_reserve == 2048
    assert tool_tokens == 0
    monkeypatch.delenv("AETHERCHAT_LOCAL_MAX_HISTORY_MESSAGES", raising=False)
    assert orchestrator._history_message_limit(local) == 8
    monkeypatch.setenv("AETHERCHAT_LOCAL_MAX_HISTORY_MESSAGES", "20")
    assert orchestrator._history_message_limit(local) == 20

    remote = SimpleNamespace(config=ProviderConfig(
        name="remote",
        type="openai_compat",
        model="remote-model",
        api_key_ref="REMOTE_KEY",
    ))
    assert orchestrator._history_message_limit(remote) is None


@pytest.mark.asyncio
async def test_orchestrator_requires_real_comfyui_generation_without_status_round(monkeypatch):
    class RequiredComfyProvider(_FakeProvider):
        def __init__(self):
            super().__init__("required-comfy")
            self.requests = []

        async def stream_chat(self, messages, tools=None, **kwargs):
            tool_names = [tool.name for tool in (tools or [])]
            self.requests.append({
                "tools": tool_names,
                "tool_choice": kwargs.get("tool_choice"),
            })
            if len(self.requests) == 1:
                yield {
                    "delta": "",
                    "usage": None,
                    "tool_calls": [{
                        "id": "comfy-generate",
                        "name": "plugin__comfyui__generate_image",
                        "arguments": {
                            "prompt": "a blue ceramic mug, studio light",
                        },
                    }],
                }
            else:
                raise AssertionError("successful required generation must not request a summary round")
            yield {
                "delta": "",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }

    async def fake_plugin_invoke(tool_name, _session_id, _caller, _arguments, **_kwargs):
        return {
            "plugin_id": "comfyui",
            "action": "generate_image",
            "status": "success",
            "result": {
                "generated_files": [{
                    "path": "generated/comfyui/prompt/image.png",
                    "name": "image.png",
                    "mime_type": "image/png",
                    "size_bytes": 256,
                    "source": "comfyui",
                }],
            },
        }

    provider = RequiredComfyProvider()
    orchestrator = _orchestrator_with([provider])
    monkeypatch.setattr(
        orchestrator_module.plugin_registry,
        "invoke_tool",
        fake_plugin_invoke,
    )

    async def allow_tool(_queue, _model, _tool_name, _arguments):
        return True, ""

    orchestrator._authorize_model_tool = allow_tool
    events = [
        event
        async for event in orchestrator.stream_user_message({
            "content": "请用 ComfyUI 生成一张蓝色陶瓷杯图片",
            "targets": ["required-comfy"],
            "mode": "casual",
        })
    ]

    assert provider.requests[0] == {
        "tools": ["plugin__comfyui__generate_image"],
        "tool_choice": "required",
    }
    assert len(provider.requests) == 1
    assert any(
        event["type"] == "tool_run"
        and event["run"]["tool_name"] == "plugin__comfyui__generate_image"
        and event["run"]["status"] == "success"
        for event in events
    )
    completed = next(
        event["message"]
        for event in events
        if event["type"] == "message_complete"
        and event["message"]["author_model"] == "required-comfy"
    )
    assert completed["generated_files"][0]["path"] == (
        "generated/comfyui/prompt/image.png"
    )
    assert "可直接预览或打开" in completed["content"]
    assert "generated/comfyui/prompt/image.png" in completed["content"]
    assert [step["phase"] for step in completed["reasoning_summary"]] == [
        "analysis",
        "tool",
        "result",
        "final",
    ]
    assert all(
        "a blue ceramic mug" not in step["detail"]
        for step in completed["reasoning_summary"]
    )
    assert any(
        event["type"] == "reasoning_step"
        and event["model"] == "required-comfy"
        for event in events
    )
    assert SessionOrchestrator._required_comfyui_tools("图片呢") == []
    assert SessionOrchestrator._required_comfyui_tools(
        "请生成一张图片"
    ) == ["plugin__comfyui__generate_image"]
    assert SessionOrchestrator._required_comfyui_tools(
        "请用 ComfyUI 生成一张蓝色陶瓷杯的产品摄影图"
    ) == ["plugin__comfyui__generate_image"]
    assert SessionOrchestrator._required_comfyui_tools(
        "现在使用comfyui 用bigaspv2.safetensors模型来进行图片生成"
    ) == ["plugin__comfyui__generate_image"]
    assert SessionOrchestrator._required_comfyui_tools(
        "使用 bigaspv2模型对这个文章所描述的场景来进行多张连贯的图片。"
    ) == ["plugin__comfyui__generate_image"]
    assert SessionOrchestrator._required_comfyui_tools(
        "只检查 ComfyUI 状态；不要生成任何图片或视频"
    ) == []
    assert SessionOrchestrator._required_comfyui_tools(
        "Check ComfyUI status; do not generate images or videos"
    ) == []
    assert SessionOrchestrator._required_comfyui_tools(
        "只报告可用于图像生成的模型数量"
    ) == []
    assert SessionOrchestrator._required_comfyui_tools(
        "检测本地图片生成模型都有什么"
    ) == []
    assert SessionOrchestrator._requested_comfyui_image_model(
        "使用 bigaspv2.safetensors 来生成图片"
    ) == "bigaspv2.safetensors"
    assert SessionOrchestrator._requested_comfyui_image_model(
        "使用 bigaspv2模型来进行多张连贯的图片"
    ) == "bigaspv2"
    assert SessionOrchestrator._requested_comfyui_video_model(
        "使用 Wan2.2_Remix_NSFW_i2v_14b_high_lighting_fp8_e4m3fn_v3.0.safetensors 生成视频"
    ) == "Wan2.2_Remix_NSFW_i2v_14b_high_lighting_fp8_e4m3fn_v3.0.safetensors"
    assert SessionOrchestrator._required_comfyui_tools(
        "只检查 ComfyUI 图像模型状态；不要生成任何图片或视频",
        {"instruction": "生成单一最终交付，并核验图像模型数量"},
    ) == []


@pytest.mark.asyncio
async def test_orchestrator_injects_attached_image_and_requested_video_model(monkeypatch):
    from app.services.comfyui_service import WAN22_REMIX_HIGH_MODEL
    from PIL import Image

    class AttachedVideoProvider(_FakeProvider):
        async def stream_chat(self, messages, tools=None, **kwargs):
            assert [tool.name for tool in (tools or [])] == [
                "plugin__comfyui__generate_video"
            ]
            yield {
                "delta": "",
                "usage": None,
                "tool_calls": [{
                    "id": "attached-video",
                    "name": "plugin__comfyui__generate_video",
                    "arguments": {
                        "prompt": "slow cinematic movement",
                        "pipeline": "wan-i2v",
                    },
                }],
            }
            yield {
                "delta": "",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }

    session_id = str(uuid4())
    source = io.BytesIO()
    Image.new("RGB", (32, 24), (30, 60, 90)).save(source, format="PNG")
    attachment = knowledge_service.create_attachment(
        session_id,
        "start.png",
        "image/png",
        source.getvalue(),
    )
    expected_path = knowledge_service.image_path(session_id, attachment["id"])
    captured = {}

    async def fake_plugin_invoke(tool_name, received_session, _caller, arguments, **_kwargs):
        captured.update(arguments)
        assert tool_name == "plugin__comfyui__generate_video"
        assert received_session == session_id
        return {
            "plugin_id": "comfyui",
            "action": "generate_video",
            "status": "success",
            "result": {
                "generated_files": [{
                    "path": "generated/comfyui/video/output.webm",
                    "name": "output.webm",
                    "mime_type": "video/webm",
                    "size_bytes": 512,
                    "source": "comfyui",
                }],
            },
        }

    provider = AttachedVideoProvider("attached-video-provider")
    orchestrator = SessionOrchestrator(session_id)
    orchestrator.providers = {provider.config.name: provider}
    orchestrator.refresh_providers = lambda: None
    monkeypatch.setattr(
        orchestrator_module.plugin_registry,
        "invoke_tool",
        fake_plugin_invoke,
    )

    async def allow_tool(_queue, _model, _tool_name, _arguments):
        return True, ""

    orchestrator._authorize_model_tool = allow_tool
    events = [
        event
        async for event in orchestrator.stream_user_message({
            "content": f"使用 {WAN22_REMIX_HIGH_MODEL} 生成视频",
            "targets": [provider.config.name],
            "mode": "casual",
            "attachment_ids": [attachment["id"]],
        })
    ]

    assert captured["input_image"] == expected_path
    assert captured["_user_requested_model"] == WAN22_REMIX_HIGH_MODEL
    assert any(
        event["type"] == "tool_run" and event["run"]["status"] == "success"
        for event in events
    )


@pytest.mark.asyncio
async def test_orchestrator_exposes_comfyui_status_only_once_after_success(monkeypatch):
    class StatusOnceProvider(_FakeProvider):
        def __init__(self):
            super().__init__("status-once")
            self.requested_tools = []

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.requested_tools.append([tool.name for tool in (tools or [])])
            if len(self.requested_tools) == 1:
                yield {
                    "delta": "",
                    "usage": None,
                    "tool_calls": [{
                        "id": "comfy-status",
                        "name": "plugin__comfyui__status",
                        "arguments": {},
                    }],
                }
            else:
                yield {"delta": "ComfyUI 已就绪。", "usage": None}
            yield {
                "delta": "",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }

    async def fake_plugin_invoke(tool_name, _session_id, _caller, _arguments, **_kwargs):
        assert tool_name == "plugin__comfyui__status"
        return {
            "ready": True,
            "generation": {"image_models": [{"name": "bigaspv2.safetensors"}]},
        }

    provider = StatusOnceProvider()
    orchestrator = _orchestrator_with([provider])
    monkeypatch.setattr(
        orchestrator_module.plugin_registry,
        "invoke_tool",
        fake_plugin_invoke,
    )

    async def allow_tool(_queue, _model, _tool_name, _arguments):
        return True, ""

    orchestrator._authorize_model_tool = allow_tool
    events = [
        event
        async for event in orchestrator.stream_user_message({
            "content": "检测本地图片生成模型都有什么",
            "targets": ["status-once"],
            "mode": "casual",
        })
    ]

    assert provider.requested_tools == [
        ["plugin__comfyui__status"],
        [],
    ]
    assert sum(
        event["type"] == "tool_run"
        and event["run"]["tool_name"] == "plugin__comfyui__status"
        for event in events
    ) == 1


@pytest.mark.asyncio
async def test_orchestrator_emits_factual_provider_status_telemetry():
    provider = _FakeProvider("telemetry")
    events = await _collect_events(
        _orchestrator_with([provider]),
        ["telemetry"],
    )
    statuses = [
        event for event in events
        if event["type"] == "provider_status"
    ]
    assert [event["phase"] for event in statuses] == [
        "requesting",
        "streaming",
        "finalizing",
    ]
    assert {event["provider_type"] for event in statuses} == {"openai_compat"}
    assert statuses[0]["context_messages"] >= 2
    assert statuses[0]["context_tokens"] > 0
    assert statuses[0]["tool_count"] == 0
    assert statuses[1]["first_token_ms"] >= 0
    completed = next(
        event["message"]
        for event in events
        if event["type"] == "message_complete"
        and event["message"]["author_model"] == "telemetry"
    )
    assert [step["phase"] for step in completed["reasoning_summary"]] == [
        "analysis",
        "final",
    ]


@pytest.mark.asyncio
async def test_orchestrator_reports_tool_activity_before_arguments_finish():
    class ToolActivityProvider(_FakeProvider):
        async def stream_chat(self, messages, tools=None, **kwargs):
            yield {"delta": "", "usage": None, "tool_activity": True}
            yield {"delta": "done", "usage": None}
            yield {
                "delta": "",
                "usage": {"prompt_tokens": 10, "completion_tokens": 1},
            }

    events = await _collect_events(
        _orchestrator_with([ToolActivityProvider("tool-activity")]),
        ["tool-activity"],
    )
    statuses = [
        event for event in events
        if event["type"] == "provider_status"
    ]

    assert [event["phase"] for event in statuses] == [
        "requesting",
        "tool",
        "finalizing",
    ]
    assert statuses[1]["detail"] == "模型已开始构造工具调用"
    assert statuses[1]["first_token_ms"] >= 0


@pytest.mark.asyncio
async def test_orchestrator_caps_only_local_provider_history():
    provider = _FakeProvider("local-window")
    provider.config.type = "local"
    orchestrator = _orchestrator_with([provider])
    orchestrator._history_loaded = True
    orchestrator.conversation_history = [
        ChatMessage(
            role="user" if index % 2 == 0 else "assistant",
            content=f"history-{index}",
        )
        for index in range(20)
    ]

    await _collect_events(orchestrator, ["local-window"])
    conversation = [
        message
        for message in provider.received_messages
        if message.role != "system"
    ]

    assert len(conversation) <= 8
    assert conversation[0].role == "user"
    assert conversation[-1].content == "offline orchestration"
    assert all(message.content != "history-0" for message in conversation)


@pytest.mark.asyncio
async def test_reasoning_summaries_are_emitted_for_every_selected_model():
    providers = [_FakeProvider("model-a"), _FakeProvider("model-b")]
    events = await _collect_events(
        _orchestrator_with(providers),
        ["model-a", "model-b"],
    )
    completed = {
        event["message"]["author_model"]: event["message"]
        for event in events
        if event["type"] == "message_complete"
        and event["message"]["role"] == "assistant"
    }
    assert set(completed) == {"model-a", "model-b"}
    assert all(
        [step["phase"] for step in message["reasoning_summary"]]
        == ["analysis", "final"]
        for message in completed.values()
    )
    live_models = {
        event["model"]
        for event in events
        if event["type"] == "reasoning_step"
    }
    assert live_models == {"model-a", "model-b"}


@pytest.mark.asyncio
async def test_project_plan_waits_for_confirmation_before_model_calls(tmp_path):
    database = ChatDatabase(tmp_path / "planned-confirmation.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "先展示计划，确认后再执行",
        "targets": ["planner-model"],
        "mode": "project",
        "execution_strategy": "planned",
        "plan_confirmation_required": True,
        "max_revision_cycles": 1,
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class ConfirmationProvider(_FakeProvider):
        def __init__(self):
            super().__init__("planner-model", persona="Architect/Reviewer")
            self.calls = []

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            if "类型：verifier" in system_text:
                self.calls.append("verifier")
                content = json.dumps({
                    "verdict": "pass",
                    "summary": "候选满足目标",
                    "evidence": ["候选"],
                    "issues": [],
                }, ensure_ascii=False)
            elif "类型：synthesizer" in system_text:
                self.calls.append("synthesizer")
                content = "确认后生成的最终交付"
            else:
                self.calls.append("worker")
                content = "确认后执行的候选"
            yield {"delta": content, "usage": None}

    provider = ConfirmationProvider()
    draft_orchestrator = SessionOrchestrator(session_id, mode="project")
    draft_orchestrator.providers = {"planner-model": provider}
    draft_orchestrator.refresh_providers = lambda: None
    draft_orchestrator.run_service = run_service
    draft_orchestrator.begin_agent_run(run["id"])

    draft_events = []
    async for event in draft_orchestrator.stream_user_message(action):
        draft_events.append(event)
        run_service.observe(run["id"], event)
    waiting = run_service.finish(run["id"])

    assert waiting["status"] == "waiting_plan_confirmation"
    assert waiting["plan_status"] == "draft"
    assert provider.calls == []
    assert any(
        event["type"] == "plan_confirmation_required"
        for event in draft_events
    )
    assert not any(
        event["type"] == "plan_step_update"
        for event in draft_events
    )

    confirmed_action, _ = run_service.prepare_confirmed_plan(
        run_id=run["id"],
        session_id=session_id,
        expected_revision=1,
    )
    confirmed_action["_agent_run_id"] = run["id"]
    executing = SessionOrchestrator(session_id, mode="project")
    executing.providers = {"planner-model": provider}
    executing.refresh_providers = lambda: None
    executing.run_service = run_service
    executing.begin_agent_run(run["id"])

    execution_events = []
    async for event in executing.stream_user_message(confirmed_action):
        execution_events.append(event)
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "completed_with_warnings"
    assert completed["plan_status"] == "completed"
    assert provider.calls == ["worker", "verifier", "synthesizer"]
    assert len([
        event for event in execution_events
        if event["type"] == "message_complete"
        and event["message"]["role"] == "assistant"
    ]) == 1


@pytest.mark.asyncio
async def test_project_mode_executes_worker_verifier_synthesizer_dag(tmp_path):
    database = ChatDatabase(tmp_path / "planned-orchestrator.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "形成一份经过双模型核验的迁移方案",
        "targets": ["researcher", "reviewer"],
        "mode": "project",
        "execution_strategy": "planned",
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class PlannedProvider(_FakeProvider):
        def __init__(self, name, persona):
            super().__init__(name, persona=persona)
            self.calls = []
            self.saw_dependency_bundle = False
            self.tool_policies = {}

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            if "类型：verifier" in system_text:
                phase = "verifier"
                self.saw_dependency_bundle = all(
                    value in "\n".join(message.content for message in messages)
                    for value in ("候选-researcher", "候选-reviewer")
                )
                content = json.dumps({
                    "verdict": "pass",
                    "summary": "两份候选互相补充且未发现冲突",
                    "evidence": ["候选-researcher", "候选-reviewer"],
                    "issues": [],
                }, ensure_ascii=False)
            elif "类型：synthesizer" in system_text:
                phase = "synthesizer"
                combined = "\n".join(message.content for message in messages)
                self.saw_dependency_bundle = (
                    "候选-researcher" in combined
                    and "候选-reviewer" in combined
                    and '"verdict": "pass"' in combined
                )
                content = "最终迁移方案：先验证兼容性，再分阶段迁移，并保留回滚证据。"
            else:
                phase = "worker"
                content = f"候选-{self.config.name}"
            self.calls.append(phase)
            tool_names = {tool.name for tool in (tools or [])}
            self.tool_policies[phase] = {
                "has_list_tasks": "list_tasks" in tool_names,
                "has_write_artifact": "write_artifact" in tool_names,
            }
            yield {"delta": content, "usage": None}
            yield {
                "delta": "",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }

    researcher = PlannedProvider("researcher", "Researcher")
    reviewer = PlannedProvider("reviewer", "Architect/Reviewer")
    orchestrator = SessionOrchestrator(session_id, mode="project")
    orchestrator.providers = {
        "researcher": researcher,
        "reviewer": reviewer,
    }
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])

    events = []
    async for event in orchestrator.stream_user_message(action):
        events.append(event)
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "completed_with_warnings"
    assert researcher.calls == ["worker"]
    assert reviewer.calls == ["worker", "verifier", "synthesizer"]
    assert reviewer.saw_dependency_bundle is True
    assert researcher.tool_policies["worker"] == {
        "has_list_tasks": True,
        "has_write_artifact": False,
    }
    assert reviewer.tool_policies["verifier"]["has_write_artifact"] is False
    assert reviewer.tool_policies["synthesizer"]["has_write_artifact"] is True
    assert len([
        event for event in events
        if event["type"] == "message_complete"
        and event["message"]["role"] == "assistant"
    ]) == 1
    assert len([
        event for event in events if event["type"] == "plan_step_output"
    ]) == 3
    final_message = next(
        event["message"]
        for event in events
        if event["type"] == "message_complete"
        and event["message"]["role"] == "assistant"
    )
    assert final_message["content"].startswith("最终迁移方案")

    detail = database.get_agent_run(run["id"])
    assert {step["status"] for step in detail["plan_steps"]} == {"completed"}
    assert detail["review_count"] == 1
    assert detail["reviews"][0]["verdict"] == "pass"
    assert detail["reviews"][0]["evidence"] == [
        "候选-researcher", "候选-reviewer",
    ]
    assert any(event["type"] == "plan_created" for event in events)
    assert any(event["type"] == "review_recorded" for event in events)


@pytest.mark.asyncio
async def test_planned_code_run_uses_scoped_worktree_tools_and_real_evidence(
    tmp_path,
    monkeypatch,
):
    repository_root = tmp_path / "orchestrated-repository"
    _initialize_test_git_repository(repository_root)
    database = ChatDatabase(tmp_path / "planned-code.sqlite3")
    workspace_service = GitWorkspaceService(
        database,
        worktree_root=tmp_path / "managed-worktrees",
    )
    run_service = AgentRunService(database, workspace_service)
    monkeypatch.setattr(
        git_workspace_module,
        "git_workspace_service",
        workspace_service,
    )
    session_id = str(uuid4())
    registered = workspace_service.register_repository(
        session_id=session_id,
        selected_path=str(repository_root),
    )
    action = {
        "content": "在隔离 worktree 中把 VALUE 改为 3",
        "targets": ["code-agent"],
        "mode": "project",
        "execution_strategy": "planned",
        "repository_id": registered["id"],
        "verification_commands": ["python -m pytest -q"],
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    async def passing_sandbox(*args, **kwargs):
        return {
            "stdout": "1 passed\n",
            "exit_code": 0,
            "timed_out": False,
            "network": "none",
        }

    monkeypatch.setattr(
        git_workspace_module,
        "execute_in_sandbox",
        passing_sandbox,
    )

    class CodeProvider(_FakeProvider):
        def __init__(self):
            super().__init__("code-agent", persona="Implementer/Reviewer")
            self.worker_turns = 0
            self.saw_deterministic_evidence = False

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            combined = "\n".join(message.content for message in messages)
            tool_names = {tool.name for tool in (tools or [])}
            if "类型：worker" in system_text:
                self.worker_turns += 1
                assert {
                    "workspace_list_files",
                    "workspace_read_file",
                    "workspace_write_file",
                    "workspace_run_command",
                    "workspace_status",
                } <= tool_names
                assert "write_artifact" not in tool_names
                if self.worker_turns == 1:
                    yield {
                        "delta": "",
                        "usage": None,
                        "tool_calls": [{
                            "id": "write-isolated-worktree",
                            "name": "workspace_write_file",
                            "arguments": {
                                "path": "app.py",
                                "content": "VALUE = 3\n",
                            },
                        }],
                    }
                else:
                    assert any(
                        message.role == "tool"
                        and message.name == "workspace_write_file"
                        and not message.tool_error
                        for message in messages
                    )
                    yield {"delta": "已在隔离 worktree 修改 app.py", "usage": None}
                return
            if "类型：verifier" in system_text:
                self.saw_deterministic_evidence = (
                    "deterministic_workspace_verification" not in combined
                    and '"verification_status": "passed"' in combined
                    and '"exit_code": 0' in combined
                )
                yield {
                    "delta": json.dumps({
                        "verdict": "pass",
                        "summary": "diff 与测试证据通过",
                        "evidence": ["AetherChat deterministic verifier"],
                        "issues": [],
                    }, ensure_ascii=False),
                    "usage": None,
                }
                return
            assert "类型：synthesizer" in system_text
            yield {"delta": "候选已核验，等待用户审核并应用 diff。", "usage": None}

    provider = CodeProvider()
    orchestrator = SessionOrchestrator(session_id, mode="project")
    orchestrator.providers = {"code-agent": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])

    events = []
    async for event in orchestrator.stream_user_message(action):
        events.append(event)
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "completed_verified"
    assert provider.worker_turns == 2
    assert provider.saw_deterministic_evidence is True
    assert any(event["type"] == "workspace_verified" for event in events)
    candidates = workspace_service.list_run_workspaces(
        run_id=run["id"],
        session_id=session_id,
    )
    assert len(candidates) == 1
    assert candidates[0]["verification_status"] == "passed"
    assert candidates[0]["changed_files"] == [{
        "status": " M",
        "path": "app.py",
    }]
    assert (repository_root / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


@pytest.mark.asyncio
async def test_verifier_needs_revision_triggers_one_bounded_rework_cycle(tmp_path):
    database = ChatDatabase(tmp_path / "planned-review-revision.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "形成经过返工核验的发布方案",
        "targets": ["lead"],
        "mode": "project",
        "execution_strategy": "planned",
        "max_revision_cycles": 1,
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class RevisionProvider(_FakeProvider):
        def __init__(self):
            super().__init__("lead", persona="Architect/Reviewer")
            self.calls = []
            self.worker_count = 0
            self.verifier_count = 0
            self.saw_revision_context = False

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            combined = "\n".join(message.content for message in messages)
            if "类型：verifier" in system_text:
                self.calls.append("verifier")
                self.verifier_count += 1
                if self.verifier_count == 1:
                    content = json.dumps({
                        "verdict": "needs_revision",
                        "summary": "缺少可验证的回滚条件",
                        "evidence": ["候选-v1"],
                        "issues": ["补充回滚触发条件"],
                    }, ensure_ascii=False)
                else:
                    assert "候选-v2" in combined
                    content = json.dumps({
                        "verdict": "pass",
                        "summary": "返工版本补齐回滚条件",
                        "evidence": ["候选-v2"],
                        "issues": [],
                    }, ensure_ascii=False)
            elif "类型：synthesizer" in system_text:
                self.calls.append("synthesizer")
                assert '"verdict": "pass"' in combined
                content = "最终发布方案：包含回滚触发条件。"
            else:
                self.calls.append("worker")
                self.worker_count += 1
                if self.worker_count == 2:
                    self.saw_revision_context = (
                        "候选-v1" in combined
                        and "补充回滚触发条件" in combined
                        and "受控返工" in combined
                    )
                content = f"候选-v{self.worker_count}"
            yield {"delta": content, "usage": None}

    provider = RevisionProvider()
    orchestrator = SessionOrchestrator(session_id, mode="project")
    orchestrator.providers = {"lead": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])

    events = []
    async for event in orchestrator.stream_user_message(action):
        events.append(event)
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "completed_with_warnings"
    assert completed["plan_revision"] == 2
    assert completed["automatic_revision_count"] == 1
    assert provider.calls == [
        "worker", "verifier", "worker", "verifier", "synthesizer",
    ]
    assert provider.saw_revision_context is True
    detail = database.get_agent_run(run["id"])
    assert [review["verdict"] for review in detail["reviews"]] == [
        "needs_revision", "pass",
    ]
    assert [
        revision["source"] for revision in detail["plan_revisions"]
    ] == ["verifier_replan", "planner"]
    assert any(event["type"] == "plan_replanned" for event in events)
    assert len([
        event for event in events
        if event["type"] == "message_complete"
        and event["message"]["role"] == "assistant"
    ]) == 1


@pytest.mark.asyncio
async def test_repeated_verifier_failure_blocks_unverified_synthesis(tmp_path):
    database = ChatDatabase(tmp_path / "planned-review-fail.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "核验失败时不得伪造最终交付",
        "targets": ["lead"],
        "mode": "project",
        "execution_strategy": "planned",
        "max_revision_cycles": 1,
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class FailingVerifierProvider(_FakeProvider):
        def __init__(self):
            super().__init__("lead", persona="Architect/Reviewer")
            self.calls = []

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            if "类型：verifier" in system_text:
                self.calls.append("verifier")
                content = json.dumps({
                    "verdict": "fail",
                    "summary": "关键证据仍不可验证",
                    "evidence": [],
                    "issues": ["缺少真实测试证据"],
                }, ensure_ascii=False)
            elif "类型：synthesizer" in system_text:
                self.calls.append("synthesizer")
                content = "此内容不应生成"
            else:
                self.calls.append("worker")
                content = "缺少证据的候选"
            yield {"delta": content, "usage": None}

    provider = FailingVerifierProvider()
    orchestrator = SessionOrchestrator(session_id, mode="project")
    orchestrator.providers = {"lead": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])

    events = []
    async for event in orchestrator.stream_user_message(action):
        events.append(event)
        run_service.observe(run["id"], event)
    failed = run_service.finish(run["id"])

    assert failed["status"] == "failed"
    assert failed["automatic_revision_count"] == 1
    assert provider.calls == ["worker", "verifier", "worker", "verifier"]
    synthesizer = next(
        step for step in failed["plan_steps"]
        if step["kind"] == "synthesizer"
    )
    assert synthesizer["status"] == "blocked"
    assert any(
        event.get("code") == "plan_verification_failed"
        for event in events
    )
    assert not any(
        event["type"] == "message_complete"
        and event["message"]["role"] == "assistant"
        for event in events
    )


@pytest.mark.asyncio
async def test_project_plan_retry_reuses_completed_worker_outputs(tmp_path):
    database = ChatDatabase(tmp_path / "planned-retry.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "先独立分析，再核验并综合",
        "targets": ["worker-a", "reviewer"],
        "mode": "project",
        "execution_strategy": "planned",
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class FirstAttemptProvider(_FakeProvider):
        def __init__(self, name, persona):
            super().__init__(name, persona=persona)
            self.calls = []

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            if "类型：verifier" in system_text:
                self.calls.append("verifier")
                if False:
                    yield {}
                raise RuntimeError("verifier offline")
            if "类型：synthesizer" in system_text:
                self.calls.append("synthesizer")
                if False:
                    yield {}
                raise RuntimeError("synthesizer offline")
            self.calls.append("worker")
            yield {"delta": f"已完成-{self.config.name}", "usage": None}

    worker = FirstAttemptProvider("worker-a", "Researcher")
    reviewer = FirstAttemptProvider("reviewer", "Architect/Reviewer")
    first = SessionOrchestrator(session_id, mode="project")
    first.providers = {"worker-a": worker, "reviewer": reviewer}
    first.refresh_providers = lambda: None
    first.run_service = run_service
    first.begin_agent_run(run["id"])

    first_events = []
    async for event in first.stream_user_message(action):
        first_events.append(event)
        run_service.observe(run["id"], event)
    failed = run_service.finish(run["id"])

    assert failed["status"] == "failed"
    assert worker.calls == ["worker"]
    assert reviewer.calls == ["worker", "verifier", "synthesizer"]
    before_retry = database.list_agent_plan_steps(run["id"])
    assert [
        step["status"] for step in before_retry if step["kind"] == "worker"
    ] == ["completed", "completed"]

    retry_action, _ = run_service.prepare_retry(run["id"], session_id)
    retry_action["_agent_run_id"] = run["id"]

    class RetryReviewer(_FakeProvider):
        def __init__(self):
            super().__init__("reviewer", persona="Architect/Reviewer")
            self.calls = []
            self.saw_reused_workers = False

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            combined = "\n".join(message.content for message in messages)
            self.saw_reused_workers = (
                "已完成-worker-a" in combined
                and "已完成-reviewer" in combined
            )
            if "类型：verifier" in system_text:
                self.calls.append("verifier")
                content = json.dumps({
                    "verdict": "pass",
                    "summary": "复用的两份 Worker 输出可用于综合",
                    "evidence": ["worker-a", "reviewer"],
                    "issues": [],
                }, ensure_ascii=False)
            else:
                assert "类型：synthesizer" in system_text
                self.calls.append("synthesizer")
                content = "重试后最终交付"
            yield {"delta": content, "usage": None}

    retry_reviewer = RetryReviewer()
    resumed = SessionOrchestrator(session_id, mode="project")
    resumed.providers = {"reviewer": retry_reviewer}
    resumed.refresh_providers = lambda: None
    resumed.run_service = run_service
    resumed.begin_agent_run(run["id"], retry_action.get("_pending_steers"))

    retry_events = []
    async for event in resumed.stream_user_message(retry_action):
        retry_events.append(event)
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "completed_with_warnings"
    assert retry_reviewer.calls == ["verifier", "synthesizer"]
    assert retry_reviewer.saw_reused_workers is True
    assert all(
        step["status"] == "completed"
        for step in database.list_agent_plan_steps(run["id"])
    )
    assert any(
        event["type"] == "message_complete"
        and event["message"]["content"] == "重试后最终交付"
        for event in retry_events
    )


@pytest.mark.asyncio
async def test_project_worker_cannot_execute_side_effect_tool(tmp_path):
    database = ChatDatabase(tmp_path / "planned-read-only-worker.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "先分析，最终阶段再决定是否写入",
        "targets": ["solo"],
        "mode": "project",
        "execution_strategy": "planned",
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class ReadOnlyWorkerProvider(_FakeProvider):
        def __init__(self):
            super().__init__("solo", persona="Architect/Reviewer")
            self.worker_calls = 0

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            if "类型：worker" in system_text:
                self.worker_calls += 1
                assert "write_artifact" not in {tool.name for tool in (tools or [])}
                if self.worker_calls == 1:
                    yield {
                        "delta": "",
                        "usage": None,
                        "tool_calls": [{
                            "id": "forbidden-worker-write",
                            "name": "write_artifact",
                            "arguments": {
                                "path": "must-not-write.md",
                                "content": "forbidden",
                            },
                        }],
                    }
                    return
                observation = next(
                    message for message in reversed(messages)
                    if message.role == "tool"
                )
                assert observation.tool_error is True
                assert "只读 Worker/Verifier" in observation.content
                yield {"delta": "只读分析已完成", "usage": None}
                return
            if "类型：verifier" in system_text:
                yield {
                    "delta": json.dumps({
                        "verdict": "pass",
                        "summary": "Worker 未执行副作用",
                        "evidence": [],
                        "issues": [],
                    }, ensure_ascii=False),
                    "usage": None,
                }
                return
            assert "类型：synthesizer" in system_text
            yield {"delta": "最终未写入任何工件。", "usage": None}

    provider = ReadOnlyWorkerProvider()
    orchestrator = SessionOrchestrator(session_id, mode="project")
    orchestrator.providers = {"solo": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])
    tool_runs_before = len(tool_registry.list_runs(session_id=session_id, limit=None))

    async for event in orchestrator.stream_user_message(action):
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "completed_with_warnings"
    assert provider.worker_calls == 2
    assert len(tool_registry.list_runs(session_id=session_id, limit=None)) == tool_runs_before
    calls = database.list_agent_tool_calls(run["id"])
    assert len(calls) == 1
    assert calls[0]["status"] == "denied"
    assert calls[0]["plan_step_id"]


@pytest.mark.asyncio
async def test_interrupt_all_stops_remaining_project_plan_phases(tmp_path):
    database = ChatDatabase(tmp_path / "planned-interrupt.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "执行一个可中断的项目计划",
        "targets": ["slow-plan"],
        "mode": "project",
        "execution_strategy": "planned",
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]
    started = asyncio.Event()

    class SlowPlanProvider(_FakeProvider):
        def __init__(self):
            super().__init__("slow-plan", persona="Architect/Reviewer")
            self.phases = []

        async def stream_chat(self, messages, tools=None, **kwargs):
            system_text = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            phase = (
                "worker" if "类型：worker" in system_text
                else "verifier" if "类型：verifier" in system_text
                else "synthesizer"
            )
            self.phases.append(phase)
            started.set()
            await asyncio.sleep(10)
            yield {"delta": "不应完成", "usage": None}

    provider = SlowPlanProvider()
    orchestrator = SessionOrchestrator(session_id, mode="project")
    orchestrator.providers = {"slow-plan": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])

    async def collect():
        events = []
        async for event in orchestrator.stream_user_message(action):
            events.append(event)
            run_service.observe(run["id"], event)
        return events

    collecting = asyncio.create_task(collect())
    await asyncio.wait_for(started.wait(), timeout=1)
    orchestrator.interrupt("all")
    events = await asyncio.wait_for(collecting, timeout=2)
    interrupted = run_service.finish(run["id"])

    assert interrupted["status"] == "interrupted"
    assert provider.phases == ["worker"]
    assert not any(event.get("code") == "plan_incomplete" for event in events)
    plan_steps = database.list_agent_plan_steps(run["id"])
    assert next(
        step for step in plan_steps if step["kind"] == "synthesizer"
    )["status"] == "interrupted"


@pytest.mark.asyncio
async def test_project_plan_does_not_synthesize_when_all_workers_fail(tmp_path):
    database = ChatDatabase(tmp_path / "planned-all-workers-fail.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    action = {
        "content": "不能在没有候选结果时伪造最终交付",
        "targets": ["offline-worker"],
        "mode": "project",
        "execution_strategy": "planned",
    }
    run = run_service.create(session_id, action)
    action["_agent_run_id"] = run["id"]

    class OfflineWorker(_FakeProvider):
        def __init__(self):
            super().__init__("offline-worker", persona="Architect/Reviewer")
            self.calls = 0

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            if False:
                yield {}
            raise RuntimeError("offline")

    provider = OfflineWorker()
    orchestrator = SessionOrchestrator(session_id, mode="project")
    orchestrator.providers = {"offline-worker": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])

    events = []
    async for event in orchestrator.stream_user_message(action):
        events.append(event)
        run_service.observe(run["id"], event)
    failed = run_service.finish(run["id"])

    assert failed["status"] == "failed"
    assert provider.calls == 1
    plan_steps = database.list_agent_plan_steps(run["id"])
    assert next(
        step for step in plan_steps if step["kind"] == "verifier"
    )["status"] == "blocked"
    assert next(
        step for step in plan_steps if step["kind"] == "synthesizer"
    )["status"] == "blocked"
    assert not any(
        event["type"] == "message_complete"
        and event["message"]["role"] == "assistant"
        for event in events
    )


@pytest.mark.asyncio
async def test_orchestrator_injects_configured_agent_persona():
    provider = _FakeProvider(
        "architect",
        persona="架构评审；优先识别边界、风险和验收条件",
    )
    orchestrator = _orchestrator_with([provider])

    await _collect_events(orchestrator, ["architect"])

    system_text = "\n".join(
        message.content for message in provider.received_messages if message.role == "system"
    )
    assert "稳定标识是：architect" in system_text
    assert "架构评审；优先识别边界、风险和验收条件" in system_text


class _ToolLoopProvider(_FakeProvider):
    def __init__(self):
        super().__init__("tool-loop")
        self.calls = 0
        self.received_observation = False

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield {
                "delta": "让我先检查历史请求；这段工具轮草稿不应显示。",
                "usage": None,
                "tool_calls": [{
                    "id": "call-1",
                    "name": "plugin__desktop_control__capabilities",
                    "arguments": {},
                }],
            }
            yield {"delta": "", "usage": {"prompt_tokens": 10, "completion_tokens": 2}}
            return
        observation = next(message for message in reversed(messages) if message.role == "tool")
        assistant_call = next(
            message for message in reversed(messages)
            if message.role == "assistant" and message.tool_calls
        )
        assert [call.id for call in assistant_call.tool_calls] == ["call-1"]
        assert assistant_call.tool_calls[0].name == "plugin__desktop_control__capabilities"
        assert observation.tool_call_id == "call-1"
        assert observation.name == "plugin__desktop_control__capabilities"
        assert observation.tool_error is False
        self.received_observation = "desktop-control" in observation.content
        assert observation.images and observation.images[0].startswith("data:image/png;base64,")
        yield {"delta": "闭环完成", "usage": None}
        yield {"delta": "", "usage": {"prompt_tokens": 12, "completion_tokens": 3}}


class _PseudoToolProvider(_FakeProvider):
    def __init__(self):
        super().__init__("pseudo-tool")
        self.calls = 0

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield {
                "delta": "让我先检查。\n\n请求执行工具：plugin__documents__inventory",
                "usage": None,
            }
        elif self.calls == 2:
            assert "立即发起结构化工具调用" in messages[-1].content
            yield {
                "delta": "",
                "usage": None,
                "tool_calls": [{
                    "id": "recovered-call",
                    "name": "plugin__documents__inventory",
                    "arguments": {},
                }],
            }
        else:
            assert any(message.role == "tool" for message in messages)
            yield {"delta": "已完成真实工具调用。", "usage": None}
        yield {"delta": "", "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


class _ApprovalToolProvider(_FakeProvider):
    def __init__(self):
        super().__init__("approval-model")
        self.calls = 0

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield {
                "delta": "",
                "usage": None,
                "tool_calls": [{
                    "id": "approval-call",
                    "name": "write_artifact",
                    "arguments": {"path": "approved.md", "content": "sensitive body"},
                }],
            }
        else:
            observation = next(message for message in reversed(messages) if message.role == "tool")
            assert '"status": "success"' in observation.content
            yield {"delta": "授权后已完成", "usage": None}
        yield {"delta": "", "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


class _RejectedApprovalToolProvider(_FakeProvider):
    def __init__(self):
        super().__init__("rejected-approval-model")
        self.calls = 0
        self.received_feedback = False

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield {
                "delta": "",
                "usage": None,
                "tool_calls": [{
                    "id": "rejected-approval-call",
                    "name": "write_artifact",
                    "arguments": {"path": "denied.md", "content": "must not be written"},
                }],
            }
        else:
            observation = next(
                message for message in reversed(messages)
                if message.role == "tool"
            )
            self.received_feedback = (
                '"status": "denied"' in observation.content
                and "不要写文件，改为只读总结" in observation.content
            )
            yield {"delta": "已按反馈改为只读总结", "usage": None}
        yield {"delta": "", "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


class _ParallelToolProvider(_FakeProvider):
    def __init__(self):
        super().__init__("parallel-tools")
        self.calls = 0
        self.received_native_results = False

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield {
                "delta": "",
                "usage": None,
                "tool_calls": [
                    {
                        "id": "call-documents",
                        "name": "plugin__documents__inventory",
                        "arguments": {},
                    },
                    {
                        "id": "call-desktop",
                        "name": "plugin__desktop_control__capabilities",
                        "arguments": {},
                    },
                ],
            }
        else:
            assistant_call = next(
                message for message in reversed(messages)
                if message.role == "assistant" and message.tool_calls
            )
            assert [call.id for call in assistant_call.tool_calls] == [
                "call-documents",
                "call-desktop",
            ]
            results = {
                message.tool_call_id: message
                for message in messages
                if message.role == "tool" and message.tool_call_id
            }
            assert results["call-documents"].name == "plugin__documents__inventory"
            assert '"plugin_id": "documents"' in results["call-documents"].content
            assert results["call-desktop"].name == "plugin__desktop_control__capabilities"
            assert '"plugin_id": "desktop-control"' in results["call-desktop"].content
            self.received_native_results = True
            yield {"delta": "并行结果已正确关联", "usage": None}
        yield {"delta": "", "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


@pytest.mark.asyncio
async def test_orchestrator_reenters_model_after_tool_and_attaches_observation_image(monkeypatch):
    provider = _ToolLoopProvider()
    orchestrator = _orchestrator_with([provider])

    async def fake_invoke(tool_name, session_id, caller, arguments, **_kwargs):
        return {
            "plugin_id": "desktop-control",
            "status": "success",
            "result": {
                "snapshot_id": "snapshot-1",
                "screenshot": {
                    "image_data_url": "data:image/png;base64,iVBORw0KGgo=",
                    "width": 1,
                    "height": 1,
                },
            },
        }

    monkeypatch.setattr(plugin_registry, "invoke_tool", fake_invoke)
    events = await _collect_events(orchestrator, ["tool-loop"])
    assert provider.calls == 2
    assert provider.received_observation is True
    assert any(event["type"] == "tool_run" for event in events)
    completed = next(
        event["message"]
        for event in events
        if event["type"] == "message_complete"
        and event["message"]["author_model"] == "tool-loop"
    )
    assert completed["content"] == "闭环完成"
    assert "让我先检查历史请求" not in completed["content"]
    assert not any(
        "让我先检查历史请求" in str(event.get("delta") or "")
        for event in events
    )
    assert any(
        event["type"] == "stream_reset"
        for event in events
    )
    assert any(
        event["type"] == "message_complete"
        and event["message"]["author_model"] == "tool-loop"
        and "闭环完成" in event["message"]["content"]
        for event in events
    )


@pytest.mark.asyncio
async def test_orchestrator_correlates_parallel_tool_results_by_call_id(monkeypatch):
    provider = _ParallelToolProvider()
    orchestrator = _orchestrator_with([provider])

    async def fake_invoke(tool_name, session_id, caller, arguments, **_kwargs):
        plugin_id = "documents" if "documents" in tool_name else "desktop-control"
        return {
            "plugin_id": plugin_id,
            "status": "success",
            "result": {"tool_name": tool_name},
        }

    monkeypatch.setattr(plugin_registry, "invoke_tool", fake_invoke)
    events = await _collect_events(orchestrator, ["parallel-tools"])

    assert provider.calls == 2
    assert provider.received_native_results is True
    assert len([event for event in events if event["type"] == "tool_run"]) == 2
    assert any(
        event["type"] == "message_complete"
        and event["message"]["content"] == "并行结果已正确关联"
        for event in events
    )


def test_orchestrator_repairs_missing_and_duplicate_tool_call_ids():
    calls = SessionOrchestrator._normalize_tool_calls([
        {"id": "stable-call", "name": "first", "arguments": {}},
        {"id": "stable-call", "name": "second", "arguments": {"value": 2}},
        {"id": "", "name": "third", "arguments": "raw"},
        {
            "id": "json-string",
            "name": "write_artifact",
            "arguments": '{"path":"parsed.md","content":"ok"}',
        },
    ])

    assert calls[0].id == "stable-call"
    assert len({call.id for call in calls}) == 4
    assert all(call.id for call in calls)
    assert calls[2].arguments == {"_raw": "raw"}
    assert calls[3].arguments == {"path": "parsed.md", "content": "ok"}


@pytest.mark.asyncio
async def test_orchestrator_stops_repeated_truncated_file_writes_before_execution():
    class TruncatedWriteProvider(_FakeProvider):
        def __init__(self):
            super().__init__("truncated-writer")
            self.calls = 0
            self.tool_names: list[set[str]] = []

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            self.tool_names.append({tool.name for tool in tools or []})
            yield {
                "delta": "",
                "usage": None,
                "tool_calls": [{
                    "id": f"truncated-{self.calls}",
                    "name": "plugin__desktop_control__write_document",
                    "arguments": {
                        "_raw": '{"path":"/tmp/long.md","data":{"text":"truncated'
                    },
                }],
            }
            yield {
                "delta": "",
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            }

    provider = TruncatedWriteProvider()
    orchestrator = _orchestrator_with([provider])

    async def unexpected_approval(*_args, **_kwargs):
        raise AssertionError("invalid arguments must be rejected before approval")

    orchestrator._authorize_model_tool = unexpected_approval
    events = [
        event
        async for event in orchestrator.stream_user_message({
            "content": "把这篇长文保存到桌面文件",
            "targets": [provider.config.name],
            "mode": "casual",
        })
    ]

    assert provider.calls == 3
    assert "plugin__desktop_control__write_document" in provider.tool_names[0]
    assert "plugin__desktop_control__write_document" not in provider.tool_names[1]
    assert sum(event.get("type") == "approval_required" for event in events) == 0
    assert any(
        event.get("code") == "invalid_file_tool_arguments"
        for event in events
    )
    assert any(
        event.get("type") == "message_complete"
        and "避免反复覆盖或产生空文件" in event["message"]["content"]
        for event in events
    )


@pytest.mark.asyncio
async def test_orchestrator_resumes_from_checkpoint_without_repeating_side_effect(tmp_path):
    database = ChatDatabase(tmp_path / "orchestrator-checkpoint.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    run = run_service.create(session_id, {
        "content": "只写入一次 checkpoint.md",
        "targets": ["checkpoint-model"],
        "mode": "project",
    })

    class FailingAfterToolProvider(_FakeProvider):
        def __init__(self):
            super().__init__("checkpoint-model")
            self.calls = 0

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield {
                    "delta": "",
                    "usage": None,
                    "tool_calls": [{
                        "id": "checkpoint-call",
                        "name": "write_artifact",
                        "arguments": {
                            "path": "checkpoint.md",
                            "content": "written once",
                        },
                    }],
                }
                yield {"delta": "", "usage": {"prompt_tokens": 4, "completion_tokens": 2}}
                return
            assert any(message.tool_call_id == "checkpoint-call" for message in messages)
            if False:
                yield {}
            raise RuntimeError("offline after completed tool round")

    first_provider = FailingAfterToolProvider()
    first = SessionOrchestrator(session_id)
    first.providers = {"checkpoint-model": first_provider}
    first.refresh_providers = lambda: None
    first.run_service = run_service
    first.begin_agent_run(run["id"])

    async def allow_tool(_queue, _model, _tool_name, _arguments):
        return True, ""

    first._authorize_model_tool = allow_tool
    tool_runs_before = len(tool_registry.list_runs(session_id=session_id, limit=None))
    first_events = []
    first_action = {
        "content": "只写入一次 checkpoint.md",
        "targets": ["checkpoint-model"],
        "mode": "project",
        "_agent_run_id": run["id"],
    }
    async for event in first.stream_user_message(first_action):
        first_events.append(event)
        run_service.observe(run["id"], event)
    failed = run_service.finish(run["id"])

    assert failed["status"] == "failed"
    assert any(event.get("code") == "provider_error" for event in first_events)
    assert len(tool_registry.list_runs(session_id=session_id, limit=None)) == tool_runs_before + 1
    assert database.latest_agent_checkpoint(run["id"], "checkpoint-model")["kind"] == "tool_round"

    class ResumeProvider(_FakeProvider):
        def __init__(self):
            super().__init__("checkpoint-model")
            self.calls = 0
            self.saw_checkpoint = False

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            self.saw_checkpoint = (
                any(message.tool_calls for message in messages)
                and any(message.tool_call_id == "checkpoint-call" for message in messages)
            )
            yield {"delta": "从检查点完成", "usage": None}
            yield {"delta": "", "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

    retry_action, _ = run_service.prepare_retry(run["id"], session_id)
    retry_action["_agent_run_id"] = run["id"]
    resumed_provider = ResumeProvider()
    resumed = SessionOrchestrator(session_id)
    resumed.providers = {"checkpoint-model": resumed_provider}
    resumed.refresh_providers = lambda: None
    resumed.run_service = run_service
    resumed.begin_agent_run(run["id"], retry_action.get("_pending_steers"))

    async for event in resumed.stream_user_message(retry_action):
        run_service.observe(run["id"], event)
    completed = run_service.finish(run["id"])

    assert completed["status"] == "uncertain"
    assert resumed_provider.calls == 1
    assert resumed_provider.saw_checkpoint is True
    assert len(tool_registry.list_runs(session_id=session_id, limit=None)) == tool_runs_before + 1


@pytest.mark.asyncio
async def test_orchestrator_applies_steer_before_stale_tool_side_effect(tmp_path):
    database = ChatDatabase(tmp_path / "orchestrator-steer.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    run = run_service.create(session_id, {
        "content": "先准备旧方案",
        "targets": ["steer-model"],
    })
    started = asyncio.Event()
    release = asyncio.Event()

    class SteerProvider(_FakeProvider):
        def __init__(self):
            super().__init__("steer-model")
            self.calls = 0
            self.saw_steer = False

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield {"delta": "旧方案", "usage": None}
                started.set()
                await release.wait()
                yield {
                    "delta": "",
                    "usage": None,
                    "tool_calls": [{
                        "id": "stale-write",
                        "name": "write_artifact",
                        "arguments": {
                            "path": "must-not-exist.md",
                            "content": "stale",
                        },
                    }],
                }
            else:
                self.saw_steer = any(
                    message.role == "user" and "改为只读分析" in message.content
                    for message in messages
                )
                yield {"delta": "已改为只读分析", "usage": None}
            yield {"delta": "", "usage": {"prompt_tokens": 2, "completion_tokens": 1}}

    provider = SteerProvider()
    orchestrator = SessionOrchestrator(session_id)
    orchestrator.providers = {"steer-model": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])
    action = {
        "content": "先准备旧方案",
        "targets": ["steer-model"],
        "_agent_run_id": run["id"],
    }

    async def collect():
        events = []
        async for event in orchestrator.stream_user_message(action):
            events.append(event)
            run_service.observe(run["id"], event)
        run_service.finish(run["id"])
        return events

    collecting = asyncio.create_task(collect())
    await asyncio.wait_for(started.wait(), timeout=1)
    steer = run_service.queue_steer(
        run_id=run["id"],
        session_id=session_id,
        content="改为只读分析，不要写文件",
        targets=["steer-model"],
    )
    orchestrator.queue_steer(steer)
    release.set()
    events = await asyncio.wait_for(collecting, timeout=2)

    assert provider.calls == 2
    assert provider.saw_steer is True
    assert any(event["type"] == "stream_reset" for event in events)
    assert any(event["type"] == "steer_applied" for event in events)
    assert not any(event["type"] == "tool_run" for event in events)
    assert database.list_agent_tool_calls(run["id"]) == []
    checkpoint = database.latest_agent_checkpoint(run["id"], "steer-model")
    assert checkpoint["kind"] == "completed"
    assert any(
        "改为只读分析" in message["content"]
        for message in checkpoint["state"]["messages"]
        if message["role"] == "user"
    )


@pytest.mark.asyncio
async def test_agent_run_drops_turn_limit_tool_draft_from_chat(tmp_path, monkeypatch):
    database = ChatDatabase(tmp_path / "orchestrator-turn-limit.sqlite3")
    run_service = AgentRunService(database)
    session_id = str(uuid4())
    run = run_service.create(session_id, {
        "content": "验证模型调用上限",
        "targets": ["turn-limit-model"],
    })

    class TurnLimitProvider(_FakeProvider):
        def __init__(self):
            super().__init__("turn-limit-model")

        async def stream_chat(self, messages, tools=None, **kwargs):
            yield {
                "delta": "尚未完成",
                "usage": None,
                "tool_calls": [{
                    "id": "turn-limit-call",
                    "name": "list_tasks",
                    "arguments": {},
                }],
            }
            yield {"delta": "", "usage": {"prompt_tokens": 2, "completion_tokens": 1}}

    monkeypatch.setattr(orchestrator_module, "MAX_MODEL_TURNS", 1)
    monkeypatch.setattr("app.services.orchestrator.stream_exec.MAX_MODEL_TURNS", 1)
    provider = TurnLimitProvider()
    orchestrator = SessionOrchestrator(session_id)
    orchestrator.providers = {"turn-limit-model": provider}
    orchestrator.refresh_providers = lambda: None
    orchestrator.run_service = run_service
    orchestrator.begin_agent_run(run["id"])

    events = []
    async for event in orchestrator.stream_user_message({
        "content": "验证模型调用上限",
        "targets": ["turn-limit-model"],
        "_agent_run_id": run["id"],
    }):
        events.append(event)
        run_service.observe(run["id"], event)
    failed = run_service.finish(run["id"])

    assert failed["status"] == "failed"
    assert database.list_agent_steps(run["id"])[0]["status"] == "failed"
    assert any(event.get("code") == "model_turn_limit" for event in events)
    assert not any(
        event["type"] == "message_complete"
        and event["message"].get("role") == "assistant"
        for event in events
    )
    assert not any(event.get("delta") == "尚未完成" for event in events)


@pytest.mark.asyncio
async def test_orchestrator_recovers_textual_pseudo_tool_call():
    provider = _PseudoToolProvider()
    orchestrator = _orchestrator_with([provider])
    events = await _collect_events(orchestrator, ["pseudo-tool"])

    assert provider.calls == 3
    assert any(event["type"] == "stream_reset" for event in events)
    assert any(event["type"] == "tool_run" for event in events)
    completed = [
        event["message"]
        for event in events
        if event["type"] == "message_complete"
        and event["message"]["author_model"] == "pseudo-tool"
    ]
    assert completed[0]["content"] == "已完成真实工具调用。"
    assert "请求执行工具" not in completed[0]["content"]


@pytest.mark.asyncio
async def test_orchestrator_pauses_for_structured_chat_approval():
    provider = _ApprovalToolProvider()
    orchestrator = _orchestrator_with([provider])
    stream = orchestrator.stream_user_message({
        "content": "请更新项目说明",
        "targets": ["approval-model"],
        "mode": "project",
    })
    events = []
    async for event in stream:
        events.append(event)
        if event["type"] == "approval_required":
            approval = event["approval"]
            assert approval["tool_name"] == "write_artifact"
            assert approval["arguments"]["content"].startswith("[已隐藏")
            approval_service.decide(approval["id"], orchestrator.session_id, "allow_once")

    assert any(event["type"] == "approval_resolved" and event["decision"] == "allow_once" for event in events)
    assert any(event["type"] == "tool_run" and event["run"]["status"] == "success" for event in events)
    artifact_update = next(
        event for event in events
        if event["type"] == "artifact_update"
    )
    assert artifact_update["generated_files"][0]["path"] == "approved.md"
    assert any(
        event["type"] == "message_complete"
        and event["message"]["content"] == "授权后已完成"
        and event["message"]["generated_files"][0]["path"] == "approved.md"
        for event in events
    )


@pytest.mark.asyncio
async def test_orchestrator_returns_rejection_feedback_to_the_model():
    provider = _RejectedApprovalToolProvider()
    orchestrator = _orchestrator_with([provider])
    stream = orchestrator.stream_user_message({
        "content": "请更新项目说明",
        "targets": ["rejected-approval-model"],
        "mode": "project",
    })
    events = []
    async for event in stream:
        events.append(event)
        if event["type"] == "approval_required":
            approval_service.decide(
                event["approval"]["id"],
                orchestrator.session_id,
                "deny",
                "不要写文件，改为只读总结",
            )

    resolved = next(event for event in events if event["type"] == "approval_resolved")
    assert resolved["decision"] == "deny"
    assert resolved["feedback"] == "不要写文件，改为只读总结"
    assert provider.received_feedback is True
    assert any(
        event["type"] == "tool_run"
        and event["run"]["status"] == "error"
        and "不要写文件，改为只读总结" in event["run"]["error"]
        for event in events
    )
    assert any(
        event["type"] == "message_complete"
        and event["message"]["content"] == "已按反馈改为只读总结"
        for event in events
    )


@pytest.mark.asyncio
async def test_browser_navigation_requires_a_matching_desktop_receipt():
    requested_actions = []

    async def execute_desktop_action(action):
        requested_actions.append(action)
        return {
            "success": True,
            "kind": "external_navigation",
            "url": action["url"],
            "browser": action["browser"],
            "launcher": "firefox",
            "verification": "launcher_accepted",
            "accepted_at": "2026-08-01T10:05:52Z",
        }

    plugin_result = {
        "plugin_id": "browser-links",
        "plugin_name": "浏览器链接",
        "action": "open_url",
        "status": "success",
        "result": {
            "kind": "open_external",
            "url": "https://www.youtube.com",
            "browser": "firefox",
        },
    }
    orchestrator = SessionOrchestrator(
        str(uuid4()),
        desktop_action_handler=execute_desktop_action,
    )
    completed = await orchestrator._complete_desktop_action(
        "plugin__browser_links__open_url",
        plugin_result,
    )

    assert requested_actions == [{
        "kind": "open_external",
        "url": "https://www.youtube.com",
        "browser": "firefox",
    }]
    assert completed["result"]["execution_receipt"]["launcher"] == "firefox"
    assert SessionOrchestrator._requested_browser("请用 fierfox 打开") == "firefox"
    assert SessionOrchestrator._browser_execution_receipt({
        "status": "success",
        "result": plugin_result,
    }) is None

    without_desktop = SessionOrchestrator(str(uuid4()))
    with pytest.raises(RuntimeError, match="没有活动的 AetherChat 桌面客户端"):
        await without_desktop._complete_desktop_action(
            "plugin__browser_links__open_url",
            plugin_result,
        )


@pytest.mark.asyncio
async def test_orchestrator_uses_configured_cost_and_hard_stops_empty_budget():
    priced = _FakeProvider("priced", cost_in=1.0, cost_out=2.0)
    orchestrator = _orchestrator_with([priced])
    events = await _collect_events(orchestrator, ["priced"])

    complete = next(
        event["message"]
        for event in events
        if event["type"] == "message_complete"
        and event["message"]["author_model"] == "priced"
    )
    assert complete["cost_usd"] == 2.0
    assert orchestrator.budget_remaining == 8.0

    blocked = _FakeProvider("blocked", cost_in=1.0, cost_out=1.0)
    exhausted = _orchestrator_with([blocked])
    exhausted.budget_remaining = 0
    blocked_events = await _collect_events(exhausted, ["blocked"])
    assert blocked.called is False
    assert any(event.get("code") == "budget_exceeded" for event in blocked_events)


@pytest.mark.asyncio
async def test_provider_exception_finishes_and_single_interrupt_keeps_other_model_running():
    broken = _FakeProvider("broken", behavior="error")
    broken_orchestrator = _orchestrator_with([broken])
    broken_events = await asyncio.wait_for(
        _collect_events(broken_orchestrator, ["broken"]),
        timeout=1,
    )
    assert any(event.get("code") == "provider_error" for event in broken_events)

    slow_started = asyncio.Event()
    slow = _FakeProvider("slow", behavior="slow", started=slow_started)
    survivor = _FakeProvider("survivor", behavior="slow")
    orchestrator = _orchestrator_with([slow, survivor])
    collecting = asyncio.create_task(_collect_events(orchestrator, ["slow", "survivor"]))
    await asyncio.wait_for(slow_started.wait(), timeout=1)
    await asyncio.sleep(0.04)
    orchestrator.interrupt("slow")
    events = await asyncio.wait_for(collecting, timeout=2)

    assert any(event["type"] == "interrupted" and event["model"] == "slow" for event in events)
    assert not any(event["type"] == "interrupted" and event["model"] == "survivor" for event in events)
    assert any(
        event["type"] == "message_complete"
        and event["message"]["author_model"] == "survivor"
        for event in events
    )
