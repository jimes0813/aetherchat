from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

ApprovalDecision = Literal["allow_once", "allow_session", "deny"]


@dataclass
class PendingApproval:
    id: str
    session_id: str
    model: str
    tool_name: str
    risk: str
    title: str
    detail: str
    arguments: dict[str, Any]
    allow_session: bool
    created_at: str
    future: asyncio.Future[str]

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "model": self.model,
            "tool_name": self.tool_name,
            "risk": self.risk,
            "title": self.title,
            "detail": self.detail,
            "arguments": self.arguments,
            "allow_session": self.allow_session,
            "created_at": self.created_at,
        }


class ApprovalService:
    """In-memory, fail-closed approvals for model-initiated side effects.

    Session grants deliberately disappear on backend restart. Approval IDs are
    random and decisions must also match the originating session.
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self._session_grants: set[tuple[str, str]] = set()
        self._lock = RLock()

    def is_session_granted(self, session_id: str, risk: str) -> bool:
        with self._lock:
            return (str(session_id), str(risk)) in self._session_grants

    def create(
        self,
        *,
        session_id: str,
        model: str,
        tool_name: str,
        risk: str,
        title: str,
        detail: str,
        arguments: dict[str, Any],
        allow_session: bool = True,
    ) -> PendingApproval | None:
        if allow_session and self.is_session_granted(session_id, risk):
            return None
        loop = asyncio.get_running_loop()
        pending = PendingApproval(
            id=str(uuid4()),
            session_id=str(session_id),
            model=str(model)[:120],
            tool_name=str(tool_name)[:200],
            risk=str(risk)[:80],
            title=str(title)[:240],
            detail=str(detail)[:2000],
            arguments=arguments,
            allow_session=bool(allow_session),
            created_at=datetime.now(UTC).isoformat(),
            future=loop.create_future(),
        )
        with self._lock:
            self._pending[pending.id] = pending
        return pending

    async def wait(self, approval_id: str, timeout: float = 180.0) -> str:
        with self._lock:
            pending = self._pending.get(str(approval_id))
        if pending is None:
            return "deny"
        try:
            return await asyncio.wait_for(pending.future, timeout=timeout)
        except TimeoutError:
            if not pending.future.done():
                pending.future.cancel()
            return "deny"
        except asyncio.CancelledError:
            if not pending.future.done():
                pending.future.cancel()
            raise
        finally:
            with self._lock:
                self._pending.pop(pending.id, None)

    def decide(self, approval_id: str, session_id: str, decision: ApprovalDecision) -> dict[str, Any]:
        with self._lock:
            pending = self._pending.get(str(approval_id))
            if pending is None:
                raise KeyError("授权请求不存在、已处理或已过期")
            if pending.session_id != str(session_id):
                raise PermissionError("授权请求不属于当前会话")
            if decision == "allow_session" and not pending.allow_session:
                raise ValueError("此高风险操作只能逐次授权")
            if decision == "allow_session":
                self._session_grants.add((pending.session_id, pending.risk))
            if not pending.future.done():
                pending.future.get_loop().call_soon_threadsafe(
                    lambda: None if pending.future.done() else pending.future.set_result(decision)
                )
        return {"id": pending.id, "decision": decision, "resolved": True}

    def clear_session(self, session_id: str) -> None:
        value = str(session_id)
        with self._lock:
            self._session_grants = {grant for grant in self._session_grants if grant[0] != value}
            pending = [item for item in self._pending.values() if item.session_id == value]
        for item in pending:
            if not item.future.done():
                item.future.get_loop().call_soon_threadsafe(
                    lambda future=item.future: None if future.done() else future.set_result("deny")
                )


approval_service = ApprovalService()


def approval_policy(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    name = str(tool_name)
    if name.startswith("mcp__"):
        return {
            "risk": f"mcp_tool:{name.split('__', 2)[1] if '__' in name else 'external'}",
            "title": "允许模型调用外部 MCP 工具？",
            "detail": f"工具：{name}",
            "allow_session": True,
        }
    if name == "run_command":
        command = str(arguments.get("cmd") or "")[:800]
        return {
            "risk": "command_execution",
            "title": "允许模型运行沙箱命令？",
            "detail": command or "模型请求在隔离工作区执行命令。",
            "allow_session": True,
        }
    if name == "write_artifact":
        return {
            "risk": "workspace_write",
            "title": "允许模型修改项目文件？",
            "detail": f"目标文件：{str(arguments.get('path') or '未指定')[:500]}",
            "allow_session": True,
        }
    if name in {"create_merge_proposal", "propose_merge", "vote_on_proposal"}:
        return {
            "risk": "project_change",
            "title": "允许模型变更项目协作状态？",
            "detail": "模型请求创建合并提案或参与项目决策。",
            "allow_session": False,
        }
    if name.startswith("plugin__desktop_control__"):
        action = name.rsplit("__", 1)[-1]
        if action == "capabilities":
            return None
        observing = action in {"list_apps", "list_windows", "get_state", "read_document"}
        return {
            "risk": "computer_observation" if observing else "computer_control",
            "title": "允许模型观察电脑？" if observing else "允许模型操作电脑？",
            "detail": f"电脑操作：{action}",
            "allow_session": observing,
        }
    if name == "plugin__documents__export_to_local" or name.endswith("__write_document"):
        return {
            "risk": "local_file_write",
            "title": "允许模型写入本机文件？",
            "detail": str(arguments.get("destination_path") or arguments.get("path") or "模型请求写入本机文件")[:800],
            "allow_session": False,
        }
    if name == "plugin__browser_links__open_url":
        return {
            "risk": "external_navigation",
            "title": "允许模型打开外部链接？",
            "detail": str(arguments.get("url") or "")[:1000],
            "allow_session": True,
        }
    if name.startswith("plugin__comfyui__") and not name.endswith("__status"):
        return {
            "risk": "local_model_execution",
            "title": "允许模型启动本地图像或视频生成？",
            "detail": f"生成动作：{name.rsplit('__', 1)[-1]}",
            "allow_session": True,
        }
    return None
