from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.models.schemas import ChatMessage, ProviderConfig, ToolCall, ToolDefinition  # noqa: E402
from app.services.tool_loop import (  # noqa: E402
    COMFYUI_IMAGE_TOOL,
    COMFYUI_STATUS_TOOL,
    DESKTOP_WRITE_DOCUMENT_TOOL,
    MAX_TOOL_RESULT_CHARS,
    ToolLoop,
)


def test_tool_loop_normalizes_arguments_and_repairs_call_ids():
    repaired_ids = iter(["repaired-duplicate", "repaired-missing"])
    loop = ToolLoop(call_id_factory=lambda: next(repaired_ids))

    calls = loop.normalize_tool_calls([
        {"id": "stable-call", "name": "first", "arguments": {}},
        {"id": "stable-call", "name": "second", "arguments": {"value": 2}},
        {"id": "", "name": "", "arguments": "raw"},
        {
            "id": "json-string",
            "name": "write_artifact",
            "arguments": '{"path":"parsed.md","content":"ok"}',
        },
    ])

    assert [call.id for call in calls] == [
        "stable-call",
        "repaired-duplicate",
        "repaired-missing",
        "json-string",
    ]
    assert calls[2].name == "unknown_tool"
    assert calls[2].arguments == {"_raw": "raw"}
    assert calls[3].arguments == {"path": "parsed.md", "content": "ok"}


def test_tool_loop_restores_required_progress_and_enforces_bounded_state():
    loop = ToolLoop(
        max_tool_rounds=1,
        max_pseudo_tool_retries=1,
        max_invalid_file_tool_calls=2,
    )
    state = loop.start(
        required_tool_names=[COMFYUI_STATUS_TOOL, COMFYUI_IMAGE_TOOL],
        context=[ChatMessage(
            role="tool",
            name=COMFYUI_STATUS_TOOL,
            tool_call_id="status-call",
            content="ok",
        )],
    )

    assert state.current_required_tool == COMFYUI_IMAGE_TOOL
    state.mark_required_succeeded(COMFYUI_IMAGE_TOOL)
    assert state.required_tools_complete is True
    assert state.record_pseudo_tool_retry() is False
    assert state.record_pseudo_tool_retry() is True

    assert state.record_invalid_file_arguments(DESKTOP_WRITE_DOCUMENT_TOOL) == 1
    assert DESKTOP_WRITE_DOCUMENT_TOOL in state.disabled_tool_names
    assert state.record_invalid_file_arguments("write_artifact") == 2
    assert state.invalid_file_limit_reached is True
    state.record_tool_result("write_artifact", "success")
    assert state.invalid_file_tool_calls == 0

    state.record_tool_result(COMFYUI_STATUS_TOOL, "success")
    definitions = [
        ToolDefinition(name=COMFYUI_STATUS_TOOL, description="status"),
        ToolDefinition(name="list_tasks", description="tasks"),
    ]
    assert [tool.name for tool in state.enabled_tools(definitions)] == [
        "list_tasks"
    ]
    state.tool_round = 1
    assert state.tool_round_limit_reached is True

    failed = loop.start(
        required_tool_names=[COMFYUI_STATUS_TOOL],
        context=[ChatMessage(
            role="tool",
            name=COMFYUI_STATUS_TOOL,
            tool_call_id="failed-status",
            tool_error=True,
            content="failed",
        )],
    )
    assert failed.required_tool_failed is True
    assert failed.current_required_tool is None


def test_tool_loop_validates_and_redacts_arguments_without_invoking_tools():
    loop = ToolLoop()

    def validate(schema, arguments):
        for name in schema.get("required") or []:
            if not arguments.get(name):
                raise ValueError(f"Missing required argument: {name}")

    write_definition = ToolDefinition(
        name="write_artifact",
        description="write",
        parameters={
            "type": "object",
            "required": ["path", "content"],
            "properties": {},
        },
    )
    truncated = loop.argument_error(
        "write_artifact",
        {"_raw": "truncated"},
        write_definition,
        validate_arguments=validate,
    )
    assert "工具未执行" in str(truncated)
    assert "write_artifact 分块" in str(truncated)

    missing = loop.argument_error(
        "write_artifact",
        {"path": "notes.md"},
        write_definition,
        validate_arguments=validate,
    )
    assert missing == (
        "Missing required argument: content；"
        "文件写入未执行，请补齐参数且不要重复发送完整长文"
    )
    assert "绝对路径" in str(loop.argument_error(
        DESKTOP_WRITE_DOCUMENT_TOOL,
        {"path": "relative.md"},
        None,
        validate_arguments=validate,
    ))
    assert loop.argument_error(
        "write_artifact",
        {"path": "a.md", "content": "x", "mode": "append", "sequence": 0},
        write_definition,
        validate_arguments=validate,
    ) == "write_artifact append 模式要求 sequence>=1"

    malformed = loop.sanitize_arguments("write_artifact", {"_raw": "secret"})
    assert malformed == {"_raw": "[malformed tool arguments omitted chars=6]"}
    desktop = loop.sanitize_arguments(DESKTOP_WRITE_DOCUMENT_TOOL, {
        "path": "/tmp/a.md",
        "text": "secret text",
        "value": "typed value",
        "data": {"text": "document"},
    })
    assert desktop["text"] == "[redacted 11 characters]"
    assert desktop["value"] == "[redacted 11 characters]"
    assert desktop["data"] == "[document payload redacted]"


def test_tool_loop_sanitizes_results_and_appends_correlated_context():
    loop = ToolLoop()
    image_urls = [f"data:image/png;base64,image-{index}" for index in range(6)]
    safe, extracted_images = loop.sanitize_result({
        "snapshot": {"image_data_url": image_urls[0]},
        "items": [image_urls[1], "plain"],
    })
    assert extracted_images == image_urls[:2]
    assert safe == {
        "snapshot": {"image_data_url": "[image attached separately]"},
        "items": ["[image attached separately]", "plain"],
    }

    files = loop.generated_files_from_result("write_artifact", {
        "artifact": {"path": "notes/a.md", "version": 1},
        "nested": {
            "generated_files": [
                {"path": "notes/a.md", "name": "new-name.md", "size_bytes": 12},
                {"url": "http://localhost/file.png", "mime_type": "image/png"},
            ],
        },
    })
    assert [item["path"] or item["url"] for item in files] == [
        "notes/a.md",
        "http://localhost/file.png",
    ]
    assert files[0]["name"] == "new-name.md"

    state = loop.start(initial_tool_round=2)
    context = [ChatMessage(
        role="assistant",
        content="tool calls",
        images=["data:image/png;base64,old"],
    )]
    loop.append_results_to_context(
        state,
        context,
        [
            (
                ToolCall(id="call-a", name="first", arguments={}),
                {"status": "success", "result": {"ok": True}},
                image_urls[:3],
            ),
            (
                ToolCall(id="call-b", name="second", arguments={}),
                {"status": "error", "error": "failed"},
                image_urls[3:],
            ),
        ],
    )

    assert context[0].images == []
    assert context[1].tool_call_id == "call-a"
    assert context[1].tool_error is False
    assert context[1].images == image_urls[:3]
    assert context[2].tool_call_id == "call-b"
    assert context[2].tool_error is True
    assert context[2].images == image_urls[3:4]
    assert context[1].content.startswith("第 3 轮工具 first 的结果。")
    assert state.tool_round == 3


def test_tool_loop_bounds_aggregate_result_context():
    loop = ToolLoop()
    state = loop.start()
    context: list[ChatMessage] = []

    loop.append_results_to_context(
        state,
        context,
        [
            (
                ToolCall(id="large", name="large_result", arguments={}),
                {"status": "success", "data": "x" * (MAX_TOOL_RESULT_CHARS + 100)},
                [],
            ),
            (
                ToolCall(id="after-limit", name="small_result", arguments={}),
                {"status": "success", "data": "must-not-expand-context"},
                [],
            ),
        ],
    )

    assert "[工具结果已截断]" in context[0].content
    assert "[工具结果因本轮总长度上限已省略]" in context[1].content
    assert "must-not-expand-context" not in context[1].content


@pytest.mark.asyncio
async def test_orchestrator_uses_an_injected_tool_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AETHERCHAT_ALLOW_UNAUTHENTICATED", "1")
    from app.services.orchestrator import SessionOrchestrator

    class RecordingToolLoop(ToolLoop):
        def __init__(self):
            super().__init__()
            self.starts = []

        def start(self, **kwargs):
            self.starts.append(kwargs)
            return super().start(**kwargs)

    class FinalProvider:
        def __init__(self):
            self.config = ProviderConfig(
                name="tool-loop-boundary",
                type="openai_compat",
                model="offline-model",
                api_key_ref="OFFLINE_TOOL_LOOP_KEY",
            )

        async def stream_chat(self, messages, tools=None, **kwargs):
            yield {"delta": "boundary complete", "usage": None}
            yield {
                "delta": "",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    service = RecordingToolLoop()
    provider = FinalProvider()
    orchestrator = SessionOrchestrator(
        str(uuid4()),
        tool_loop_service=service,
    )
    orchestrator.providers = {provider.config.name: provider}
    orchestrator.refresh_providers = lambda: None

    events = [
        event
        async for event in orchestrator.stream_user_message({
            "content": "offline tool loop boundary",
            "targets": [provider.config.name],
            "mode": "casual",
        })
    ]

    assert len(service.starts) == 1
    assert service.starts[0]["initial_tool_round"] == 0
    assert any(
        event.get("type") == "message_complete"
        and event["message"]["content"] == "boundary complete"
        for event in events
    )
