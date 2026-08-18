from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.models.schemas import ChatMessage, ProviderConfig, ToolCall  # noqa: E402
from app.services.context_assembler import (  # noqa: E402
    ARTIFACT_WRITE_GUIDANCE,
    BROWSER_GUIDANCE,
    COMFYUI_GUIDANCE,
    DESKTOP_CONTROL_GUIDANCE,
    MEMORY_WRITE_GUIDANCE,
    ContextAssembler,
)


def test_context_assembler_preserves_the_full_instruction_order():
    history = [
        ChatMessage(role="system", content="已有系统约束"),
        ChatMessage(role="user", content="原始用户消息"),
    ]

    assembled = ContextAssembler.assemble(
        history,
        memory_context="共享记忆",
        tool_names={
            "upsert_memory_entity",
            "plugin__browser_links__open_url",
            "write_artifact",
            "plugin__desktop_control__get_state",
            "plugin__comfyui__generate_image",
        },
        attachment_context="附件上下文",
        project_knowledge_context="项目知识",
        language="中英双语",
        background_job_id="job-1",
        background_allowed_tools={"write_artifact", "create_task"},
    )

    assert [message.content for message in assembled] == [
        (
            "本次请求由 AetherChat 本机后台调度器触发。"
            "无人值守执行必须遵守创建任务时固化的权限快照；"
            "允许的副作用工具白名单：create_task、write_artifact。"
            "未列入白名单的副作用不得请求授权、不得尝试绕过，"
            "只能输出分析结果并明确需要人工处理的后续动作。"
        ),
        COMFYUI_GUIDANCE,
        DESKTOP_CONTROL_GUIDANCE,
        ARTIFACT_WRITE_GUIDANCE,
        "请使用中英双语回复，先中文后英文。",
        "项目知识",
        "附件上下文",
        BROWSER_GUIDANCE,
        MEMORY_WRITE_GUIDANCE,
        "共享记忆",
        "已有系统约束",
        "原始用户消息",
    ]


def test_context_assembler_puts_explicit_references_above_history():
    history = [ChatMessage(role="user", content="按这个改")]
    assembled = ContextAssembler.assemble(
        history,
        memory_context="共享记忆",
        reference_context="用户明确引用了文件",
    )
    contents = [message.content for message in assembled]
    assert contents.index("用户明确引用了文件") == contents.index("共享记忆") + 1
    assert contents[-1] == "按这个改"


def test_context_assembler_injects_instruction_context_above_memory():
    history = [ChatMessage(role="user", content="请评审这段代码")]
    assembled = ContextAssembler.assemble(
        history,
        memory_context="共享记忆",
        instruction_context="### 规则：默认\n保持简洁",
    )
    contents = [message.content for message in assembled]
    assert contents.index("### 规则：默认\n保持简洁") < contents.index("共享记忆")
    assert contents[-1] == "请评审这段代码"


def test_context_assembler_returns_a_deep_copy_without_optional_guidance():
    history = [ChatMessage(
        role="assistant",
        content="原始回复",
        images=["data:image/png;base64,AAAA"],
        tool_calls=[ToolCall(id="call-1", name="read_artifact", arguments={"path": "a.md"})],
    )]

    assembled = ContextAssembler.assemble(
        history,
        language="unsupported-language",
        background_allowed_tools={"ignored_without_job"},
    )

    assert assembled == history
    assert assembled[0] is not history[0]
    assembled[0].images.append("data:image/png;base64,BBBB")
    assembled[0].tool_calls[0].arguments["path"] = "changed.md"
    assert history[0].images == ["data:image/png;base64,AAAA"]
    assert history[0].tool_calls[0].arguments == {"path": "a.md"}


def test_context_assembler_only_adds_guidance_for_selected_tool_families():
    assembled = ContextAssembler.assemble(
        [ChatMessage(role="user", content="你好")],
        tool_names={"plugin__unrelated__status"},
        language="中文优先",
    )

    assert [message.content for message in assembled] == [
        "请优先使用清晰、准确的中文回复。",
        "你好",
    ]


@pytest.mark.asyncio
async def test_orchestrator_uses_an_injected_context_assembler(monkeypatch, tmp_path):
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AETHERCHAT_ALLOW_UNAUTHENTICATED", "1")
    from app.services.orchestrator import SessionOrchestrator

    class RecordingContextAssembler(ContextAssembler):
        def __init__(self):
            self.calls = []

        def assemble(self, conversation_history, **kwargs):
            snapshot = [message.model_copy(deep=True) for message in conversation_history]
            self.calls.append((snapshot, kwargs))
            return [ChatMessage(role="user", content="injected assembled context")]

    class RecordingProvider:
        def __init__(self):
            self.config = ProviderConfig(
                name="context-boundary",
                type="openai_compat",
                model="offline-model",
                api_key_ref="OFFLINE_CONTEXT_KEY",
            )
            self.received_messages = []

        async def stream_chat(self, messages, tools=None, **kwargs):
            self.received_messages = messages
            yield {"delta": "assembled", "usage": None}
            yield {
                "delta": "",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    provider = RecordingProvider()
    assembler = RecordingContextAssembler()
    orchestrator = SessionOrchestrator(
        str(uuid4()),
        context_assembler_service=assembler,
    )
    orchestrator.providers = {provider.config.name: provider}
    orchestrator.refresh_providers = lambda: None

    events = [
        event
        async for event in orchestrator.stream_user_message({
            "content": "offline orchestration",
            "targets": [provider.config.name],
            "mode": "casual",
        })
    ]

    assert len(assembler.calls) == 1
    history, inputs = assembler.calls[0]
    assert history[-1].content == "offline orchestration"
    assert inputs["tool_names"] == set()
    assert any(
        message.content == "injected assembled context"
        for message in provider.received_messages
    )
    assert any(event.get("type") == "message_complete" for event in events)
