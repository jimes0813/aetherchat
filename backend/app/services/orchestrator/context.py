from __future__ import annotations

import json
import os
import re

from ...models.schemas import ToolDefinition
from ...providers.base import LLMProvider
from ..chat_database import chat_database
from ..context_builder import estimate_tokens


class ContextMixin:
    def _resolve_project_id(self) -> str | None:
        try:
            from ..platform_governance import platform_governance

            return platform_governance.resolve_project_id(session_id=self.session_id)
        except Exception:
            row = chat_database.get_session(self.session_id) or {}
            return str(row.get("project_id") or "").strip() or None

    @staticmethod
    def _context_limits(
        provider: LLMProvider,
        tools: list[ToolDefinition],
    ) -> tuple[int, int, int]:
        output_reserve = max(256, int(provider.config.max_tokens or 2048))
        provider_limit = max(2048, int(provider.config.token_limit or 8192))
        if provider.config.type != "local":
            return provider_limit, output_reserve, 0
        try:
            active_limit = int(
                os.getenv("AETHERCHAT_LOCAL_ACTIVE_CONTEXT_TOKENS", "8192")
            )
        except ValueError:
            active_limit = 8192
        try:
            local_output = int(
                os.getenv("AETHERCHAT_LOCAL_MAX_OUTPUT_TOKENS", "2048")
            )
        except ValueError:
            local_output = 2048
        tool_tokens = estimate_tokens(json.dumps(
            [tool.model_dump(mode="json") for tool in tools],
            ensure_ascii=False,
            default=str,
        )) if tools else 0
        output_reserve = min(output_reserve, max(256, local_output))
        message_limit = max(
            3072,
            min(provider_limit, max(4096, active_limit)) - tool_tokens,
        )
        return message_limit, output_reserve, tool_tokens

    @staticmethod
    def _history_message_limit(provider: LLMProvider) -> int | None:
        if provider.config.type != "local":
            return None
        try:
            configured = int(
                os.getenv("AETHERCHAT_LOCAL_MAX_HISTORY_MESSAGES", "8")
            )
        except ValueError:
            configured = 8
        return max(4, min(64, configured))

    def _calculate_cost(self, provider: LLMProvider, usage: dict) -> float:
        prompt_tokens = max(0, int(usage.get("prompt_tokens", 0) or 0))
        completion_tokens = max(0, int(usage.get("completion_tokens", 0) or 0))
        input_cost = prompt_tokens * max(0.0, provider.config.cost_per_1k_input) / 1000
        output_cost = completion_tokens * max(0.0, provider.config.cost_per_1k_output) / 1000
        return round(input_cost + output_cost, 6)

    @staticmethod
    def _provider_system_prompt(name: str, provider: LLMProvider) -> str:
        agent_name = " ".join(str(name or "agent").split())[:120]
        persona = " ".join(str(provider.config.persona or "").split())[:1200]
        parts = [
            "你是 AetherChat 多模型团队中的一个独立 Agent。",
            f"你的稳定标识是：{agent_name}。",
        ]
        if persona:
            parts.append(f"你的当前职责是：{persona}。请优先完成与该职责直接相关的工作。")
        parts.append(
            "只报告你实际完成或验证过的工作；不要声称其他 Agent 已经评审、执行或同意，"
            "除非上下文中有对应的可核对结果。"
        )
        return "\n".join(parts)

    def _parse_targets(self, content: str, default_targets: list[str]) -> list[str]:
        """PR6: Parse @mentions for targeting"""
        mentions = re.findall(r'@(\w+)', content)
        if not mentions:
            if "all" in default_targets:
                return list(self.providers.keys())
            return default_targets
        if "all" in mentions:
            return list(self.providers.keys())
        targets = [m for m in mentions if m in self.providers]
        return targets or default_targets

    def _should_participate(self, name: str, content: str, targets: list[str], explicit_targets: set[str] | None = None) -> bool:
        """Respect participation_policy and mode"""
        provider = self.providers.get(name)
        if not provider:
            return False
        policy = provider.config.participation_policy
        if name not in targets:
            return False
        if self.mode == "casual":
            return True
        if policy == "always":
            return True
        if policy == "mentioned":
            return name in (explicit_targets or set()) or "@" + name in content or "all" in content.lower()
        if policy == "project_autonomous" and self.mode == "project":
            if name in (explicit_targets or set()):
                return True
            # In project mode, autonomous for task-related
            return any(kw in content.lower() for kw in ["task", "claim", "write", "merge", "propose"])
        return False
