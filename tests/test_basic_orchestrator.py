import asyncio
import io
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
from app.services import orchestrator as orchestrator_module  # noqa: E402
from app.services.knowledge_service import knowledge_service  # noqa: E402
from app.services.orchestrator import SessionOrchestrator  # noqa: E402

# Split from tests/test_basic.py for domain isolation.
# Shared env/client fixtures live in tests/conftest.py.

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
