from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from ...models.schemas import ChatMessage
from ...providers.base import LLMProvider
from ..agent_run_service import agent_run_service
from ..context_assembler import ContextAssembler, context_assembler
from ..plugin_registry import plugin_registry as plugin_registry
from ..tool_loop import ToolLoop, tool_loop
from ._constants import (
    BROWSER_OPEN_TOOL as BROWSER_OPEN_TOOL,
)
from ._constants import (
    MAX_MODEL_TURNS as MAX_MODEL_TURNS,
)
from ._constants import (
    PSEUDO_TOOL_CALL_RE as PSEUDO_TOOL_CALL_RE,
)
from .context import ContextMixin
from .plan import PlanMixin
from .receipts import ReceiptsMixin
from .run import RunMixin
from .stream_exec import StreamExecMixin
from .stream_invoke import StreamInvokeMixin
from .stream_plan import StreamPlanMixin
from .stream_provider import StreamProviderMixin
from .stream_tools import StreamToolsMixin
from .tools import ToolsMixin


class Facilitator:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.mode = "project"
        self.history: list[str] = []
        self.loop_count = 0

    def check_loop(self, content: str) -> bool:
        self.history.append(content[:100])
        if len(self.history) > 5:
            self.history.pop(0)
        # Simple repetition detection
        if len(set(self.history[-3:])) == 1:
            self.loop_count += 1
            return self.loop_count >= 2
        return False

    def get_summary(self) -> str:
        mode_note = f"当前模式: {self.mode if hasattr(self, 'mode') else 'project'}。"
        return f"{mode_note} 当前进度摘要（Facilitator）: 已处理 {len(self.history)} 条消息。建议检查任务状态并推进合并提案（需2票或人类确认）。"

    def inject(self, content: str) -> dict:
        return {
            "type": "facilitator_msg",
            "content": content,
            "ts": datetime.now(UTC).isoformat()
        }


class SessionOrchestrator(RunMixin, StreamExecMixin, StreamProviderMixin, StreamToolsMixin, StreamInvokeMixin, StreamPlanMixin, PlanMixin, ToolsMixin, ContextMixin, ReceiptsMixin):
    def __init__(
        self,
        session_id: str,
        tenant_id: str = "demo",
        mode: str = "casual",
        desktop_action_handler: (
            Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None,
        context_assembler_service: ContextAssembler | None = None,
        tool_loop_service: ToolLoop | None = None,
    ):
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.mode: str = mode  # casual | roundtable | project
        self.providers: dict[str, LLMProvider] = {}
        self.refresh_providers()
        self.budget_remaining = 10.0
        self.facilitator = Facilitator(session_id)
        self.conversation_history: list[ChatMessage] = []
        self._history_loaded = False
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._interrupt_all = False
        self._interrupted_models: set[str] = set()
        self._budget_lock = asyncio.Lock()
        self._budget_exhausted = False
        self._roundtable_cursor = 0
        self.run_service = agent_run_service
        self._active_run_id: str | None = None
        self._run_steers: list[dict] = []
        self._plan_cancelled = False
        self.desktop_action_handler = desktop_action_handler
        self.context_assembler = context_assembler_service or context_assembler
        self.tool_loop = tool_loop_service or tool_loop
        self.project_id = self._resolve_project_id()
