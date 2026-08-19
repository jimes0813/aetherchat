from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ...models.schemas import ChatMessage, Message
from ...providers.base import get_provider
from ..chat_database import chat_database
from ..chat_references import resolve_chat_references
from ..git_workspace_service import (
    git_workspace_service,
)
from ..knowledge_service import KnowledgeError, knowledge_service
from ..memory_service import memory_service
from ..model_registry import model_registry


class RunMixin:
    session_id: str
    project_id: str | None
    mode: str
    _active_run_id: str | None

    def begin_agent_run(self, run_id: str, steers: list[dict] | None = None) -> None:
        self._active_run_id = str(run_id)
        self._run_steers = [dict(item) for item in (steers or []) if isinstance(item, dict)]
        self._plan_cancelled = False

    def end_agent_run(self, run_id: str) -> None:
        if self._active_run_id == str(run_id):
            self._active_run_id = None
            self._run_steers = []
            self._plan_cancelled = False

    def queue_steer(self, steer: dict) -> None:
        if self._active_run_id != str(steer.get("run_id") or ""):
            raise ValueError("转向消息不属于当前运行")
        if any(item.get("id") == steer.get("id") for item in self._run_steers):
            return
        self._run_steers.append(dict(steer))

    def record_steer_message(self, content: str) -> tuple[Message, dict]:
        message = Message(
            id=uuid4(),
            session_id=UUID(self.session_id),
            author_model="user",
            role="user",
            content=str(content).strip(),
            created_at=datetime.now(UTC),
        )
        self.conversation_history.append(ChatMessage(role="user", content=message.content))
        return message, message.model_dump(mode="json")

    async def persist_steer_message(self, message: Message) -> None:
        await chat_database.run_in_thread(chat_database.save_message, message, mode=self.mode)
        memory_service.refresh(self.session_id, user_content=message.content)

    def _consume_steers(self, model: str) -> list[dict]:
        selected: list[dict] = []
        for steer in self._run_steers:
            targets = {str(value) for value in steer.get("targets") or []}
            applied = {str(value) for value in steer.get("applied_models") or []}
            if targets and "all" not in targets and model not in targets:
                continue
            if model in applied:
                continue
            applied.add(model)
            steer["applied_models"] = sorted(applied)
            selected.append(steer)
        return selected

    def _has_pending_steers(self, model: str) -> bool:
        for steer in self._run_steers:
            targets = {str(value) for value in steer.get("targets") or []}
            applied = {str(value) for value in steer.get("applied_models") or []}
            if targets and "all" not in targets and model not in targets:
                continue
            if model not in applied:
                return True
        return False

    async def stream_user_message(self, action: dict) -> AsyncIterator[dict]:
        self.refresh_providers()
        workspace_service = (
            self.run_service.workspace_service or git_workspace_service
        )
        agent_run_id = str(action.get("_agent_run_id") or self._active_run_id or "").strip()
        background_job_id = str(action.get("_background_job_id") or "").strip()
        background_allowed_tools = {
            str(value)
            for value in action.get("_background_allowed_tools") or []
            if str(value).strip()
        }
        pending_steers = action.get("_pending_steers") or []
        if agent_run_id and self._active_run_id is None:
            self.begin_agent_run(agent_run_id, pending_steers)
        elif agent_run_id and self._active_run_id == agent_run_id:
            for steer in pending_steers:
                if isinstance(steer, dict):
                    self.queue_steer(steer)
        requested_mode = action.get("mode")
        if isinstance(requested_mode, str):
            self.mode = requested_mode
            self.facilitator.mode = self.mode
        raw_attachment_ids = action.get("attachment_ids") or []
        if not isinstance(raw_attachment_ids, list) or len(raw_attachment_ids) > 10:
            yield {"type": "error", "code": "invalid_attachments", "message": "单条消息最多附带 10 个有效附件。"}
            return
        attachment_ids = [str(value) for value in raw_attachment_ids]
        referenced = resolve_chat_references(
            session_id=self.session_id,
            user_content=str(action.get("content") or "").strip(),
            references=action.get("references"),
            attachment_ids=attachment_ids,
        )
        attachment_ids = referenced["attachment_ids"]
        reference_context = str(referenced.get("context") or "")
        content = str(action.get("content") or "").strip()
        if not content and attachment_ids:
            content = "请阅读并分析我附带的资料。"
        if not content and reference_context:
            content = "请根据我引用的内容理解意图并继续。"
        if not content:
            yield {"type": "error", "code": "empty_message", "message": "消息内容不能为空。"}
            return
        try:
            attachment_refs = knowledge_service.attachment_refs(self.session_id, attachment_ids)
            attachment_context = knowledge_service.context_for_attachments(
                self.session_id, attachment_ids, content
            )
            project_knowledge_context = (
                ""
                if attachment_ids
                else knowledge_service.context_for_query(self.session_id, content)
            )
            attachment_images = knowledge_service.image_data_urls(self.session_id, attachment_ids)
            attachment_image_paths = knowledge_service.image_paths(
                self.session_id,
                attachment_ids,
            )
            for extra_path in referenced.get("image_paths") or []:
                if extra_path and extra_path not in attachment_image_paths:
                    attachment_image_paths.append(str(extra_path))
        except (KeyError, KnowledgeError, ValueError) as exc:
            message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
            yield {"type": "error", "code": "invalid_attachments", "message": str(message)}
            return
        if not self._history_loaded:
            self.conversation_history = [
                ChatMessage(
                    role=row["role"],
                    content=row["content"],
                    name=row["author_model"] if row["role"] == "assistant" else None,
                )
                for row in chat_database.chat_history(self.session_id)
                if row["role"] in {"system", "user", "assistant", "tool"}
            ]
            self._history_loaded = True
        raw_targets = action.get("targets") or list(self.providers.keys())
        explicit_targets = {target for target in raw_targets if target != "all"}
        targets = self._parse_targets(content, raw_targets)

        # Mode-aware filtering
        if self.mode == "roundtable" and targets:
            index = self._roundtable_cursor % len(targets)
            targets = [targets[index]]
            self._roundtable_cursor += 1

        if not action.get("_aether_retry"):
            user_message = Message(
                id=uuid4(),
                session_id=UUID(self.session_id),
                author_model="user",
                role="user",
                content=content,
                attachments=attachment_refs,
                created_at=datetime.now(UTC),
            )
            self.conversation_history.append(ChatMessage(role="user", content=content, images=attachment_images))
            await chat_database.run_in_thread(chat_database.save_message, user_message, mode=self.mode)
            memory_service.refresh(self.session_id, user_content=content)
            yield {"type": "message_complete", "message": user_message.model_dump(mode="json")}

        # Facilitator check
        if self.facilitator.check_loop(content):
            yield self.facilitator.inject(self.facilitator.get_summary())

        async for event in self._execute_selected_providers(
            action=action,
            content=content,
            targets=targets,
            explicit_targets=explicit_targets,
            agent_run_id=agent_run_id,
            background_job_id=background_job_id,
            background_allowed_tools=background_allowed_tools,
            workspace_service=workspace_service,
            attachment_images=attachment_images,
            attachment_image_paths=attachment_image_paths,
            attachment_context=attachment_context,
            reference_context=reference_context,
            project_knowledge_context=project_knowledge_context,
        ):
            yield event

    async def broadcast_user_message(self, action: dict):
        async for _ in self.stream_user_message(action):
            pass

    def interrupt(self, scope: str = "all"):
        """PR6: Interrupt support"""
        if scope == "all":
            self._plan_cancelled = True
            self._interrupt_all = True
            for task in list(self._running_tasks.values()):
                task.cancel()
        else:
            self._interrupted_models.add(scope)
            if scope in self._running_tasks:
                self._running_tasks[scope].cancel()
        asyncio.create_task(self._reset_interrupt(scope))

    async def _reset_interrupt(self, scope: str):
        await asyncio.sleep(0.1)
        if scope == "all":
            self._interrupt_all = False
        else:
            self._interrupted_models.discard(scope)

    def _is_interrupted(self, name: str) -> bool:
        return self._interrupt_all or name in self._interrupted_models

    def _cancel_running_providers(self, exclude: str | None = None):
        for name, task in list(self._running_tasks.items()):
            if name != exclude and not task.done():
                task.cancel()

    def refresh_providers(self):
        self.providers = {
            config.name: get_provider(config)
            for config in model_registry.configs(available_only=True)
        }

    async def start_model_streams(self, prompt: str):
        # 并行启动多个模型
        tasks = []
        for prov in self.providers.values():
            tasks.append(self._run_one(prov, prompt))
        await asyncio.gather(*tasks)

    async def _run_one(self, provider, prompt):
        async for _ in provider.stream_chat([ChatMessage(role="user", content=prompt)]):
            pass
