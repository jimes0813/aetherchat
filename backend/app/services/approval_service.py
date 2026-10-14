from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

ApprovalDecision = Literal["allow_once", "allow_session", "deny"]

logger = logging.getLogger(__name__)

DEFAULT_PENDING_TTL_SECONDS = 6 * 60 * 60
DEFAULT_GRANT_TTL_SECONDS = 12 * 60 * 60


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _resolve_future(future: asyncio.Future[str], decision: str) -> None:
    if not future.done():
        future.set_result(decision)


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
    feedback: str
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
    """Fail-closed approvals for model-initiated side effects.

    Pending approvals and session grants are persisted in SQLite. Futures stay
    in-process only: on backend restart, leftover pending rows are cancelled
    with reason ``backend_restart`` so the UI can prompt for re-authorization.
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self._session_grants: set[tuple[str, str]] = set()
        self._lock = RLock()
        self._pending_ttl = _env_float(
            "AETHERCHAT_APPROVAL_PENDING_TTL_SECONDS",
            DEFAULT_PENDING_TTL_SECONDS,
        )
        self._grant_ttl = _env_float(
            "AETHERCHAT_APPROVAL_GRANT_TTL_SECONDS",
            DEFAULT_GRANT_TTL_SECONDS,
        )
        self._restart_cancellations: list[dict[str, Any]] = []

    def _db(self):
        from .chat_database import chat_database

        return chat_database

    def bootstrap_from_db(self) -> dict[str, int]:
        """Load grants, expire stale rows, cancel orphaned pending on restart."""
        db = self._db()
        # Restart cancels every leftover pending row first (需重新授权), then
        # TTL cleanup can only affect already-terminal rows if extended later.
        cancelled: list[dict[str, Any]] = []
        try:
            cancelled = db.cancel_all_pending_approvals("backend_restart")
        except Exception:
            logger.exception("failed to cancel pending approvals on restart")
        expired = 0
        try:
            expired = db.expire_pending_approvals(self._pending_ttl)
        except Exception:
            logger.exception("failed to expire stale pending approvals")
        grants: set[tuple[str, str]] = set()
        try:
            grants = db.list_session_grants()
        except Exception:
            logger.exception("failed to load session grants")
        with self._lock:
            self._session_grants = set(grants)
            self._pending.clear()
            self._restart_cancellations = list(cancelled)
        if cancelled:
            logger.warning(
                "cancelled %s pending approval(s) after backend restart (需重新授权)",
                len(cancelled),
            )
        return {
            "cancelled_pending": len(cancelled),
            "expired_pending": int(expired),
            "session_grants": len(grants),
        }

    def list_restart_cancellations(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._restart_cancellations)
        if not items:
            try:
                items = self._db().list_cancelled_approvals(
                    reason="backend_restart",
                    limit=limit,
                )
            except Exception:
                items = []
        return items[: max(1, min(500, int(limit)))]

    def is_session_granted(self, session_id: str, risk: str) -> bool:
        key = (str(session_id), str(risk))
        with self._lock:
            if key in self._session_grants:
                return True
        try:
            active = self._db().is_session_grant_active(session_id, risk)
        except Exception:
            return False
        if active:
            with self._lock:
                self._session_grants.add(key)
        return active

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
            feedback="",
            future=loop.create_future(),
        )
        with self._lock:
            self._pending[pending.id] = pending
        try:
            self._db().upsert_pending_approval(
                {
                    **pending.public(),
                    "status": "pending",
                    "decision": None,
                    "feedback": "",
                    "cancel_reason": None,
                    "resolved_at": None,
                }
            )
        except Exception:
            logger.exception("failed to persist pending approval %s", pending.id)
            with self._lock:
                self._pending.pop(pending.id, None)
            if not pending.future.done():
                pending.future.cancel()
            # Fail-closed: refuse to proceed without durable pending record.
            raise RuntimeError("无法持久化授权请求，已拒绝该操作") from None
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
            try:
                self._db().mark_approval_resolved(
                    pending.id,
                    decision="deny",
                    status="cancelled",
                    cancel_reason="timeout",
                )
            except Exception:
                logger.exception("failed to mark approval timeout %s", pending.id)
            return "deny"
        except asyncio.CancelledError:
            if not pending.future.done():
                pending.future.cancel()
            raise
        finally:
            with self._lock:
                self._pending.pop(pending.id, None)

    def decide(
        self,
        approval_id: str,
        session_id: str,
        decision: ApprovalDecision,
        feedback: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            pending = self._pending.get(str(approval_id))
            if pending is None:
                raise KeyError("授权请求不存在、已处理或已过期（后端重启后需重新授权）")
            if pending.session_id != str(session_id):
                raise PermissionError("授权请求不属于当前会话")
            if decision == "allow_session" and not pending.allow_session:
                raise ValueError("此高风险操作只能逐次授权")
            if decision == "allow_session":
                self._session_grants.add((pending.session_id, pending.risk))
            pending.feedback = (
                " ".join(str(feedback or "").split())[:2000]
                if decision == "deny"
                else ""
            )
            if not pending.future.done():
                pending.future.get_loop().call_soon_threadsafe(
                    _resolve_future,
                    pending.future,
                    decision,
                )
        if decision == "allow_session":
            try:
                self._db().upsert_session_grant(
                    pending.session_id,
                    pending.risk,
                    ttl_seconds=self._grant_ttl,
                )
            except Exception:
                logger.exception("failed to persist session grant")
        try:
            self._db().mark_approval_resolved(
                pending.id,
                decision=decision,
                feedback=pending.feedback,
                status="resolved",
            )
        except Exception:
            logger.exception("failed to persist approval decision %s", pending.id)
        return {
            "id": pending.id,
            "decision": decision,
            "feedback": pending.feedback,
            "resolved": True,
        }

    def clear_session(self, session_id: str) -> None:
        value = str(session_id)
        with self._lock:
            self._session_grants = {grant for grant in self._session_grants if grant[0] != value}
            pending = [item for item in self._pending.values() if item.session_id == value]
        try:
            self._db().clear_session_grants(value)
        except Exception:
            logger.exception("failed to clear session grants for %s", value)
        for item in pending:
            if not item.future.done():
                item.future.get_loop().call_soon_threadsafe(
                    _resolve_future,
                    item.future,
                    "deny",
                )
            try:
                self._db().mark_approval_resolved(
                    item.id,
                    decision="deny",
                    status="cancelled",
                    cancel_reason="session_cleared",
                )
            except Exception:
                logger.exception("failed to cancel approval %s", item.id)

    def clear_risks(self, risks: set[str]) -> int:
        """Revoke grants and reject pending prompts for selected risk classes."""
        normalized = {str(value) for value in risks}
        with self._lock:
            previous = len(self._session_grants)
            self._session_grants = {
                grant for grant in self._session_grants if grant[1] not in normalized
            }
            pending = [item for item in self._pending.values() if item.risk in normalized]
            revoked_memory = previous - len(self._session_grants)
        try:
            revoked_db = self._db().clear_session_grants_for_risks(normalized)
        except Exception:
            logger.exception("failed to clear session grants for risks")
            revoked_db = 0
        for item in pending:
            if not item.future.done():
                item.future.get_loop().call_soon_threadsafe(
                    _resolve_future,
                    item.future,
                    "deny",
                )
            try:
                self._db().mark_approval_resolved(
                    item.id,
                    decision="deny",
                    status="cancelled",
                    cancel_reason="risk_revoked",
                )
            except Exception:
                logger.exception("failed to cancel approval %s", item.id)
        return max(revoked_memory, revoked_db)


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
        requested_action = (
            str(arguments.get("action") or "").strip().lower()
            if action == "computer"
            else action
        )
        observing = requested_action in {
            "list_apps", "list_windows", "get_state", "read_document",
            "screenshot", "zoom", "cursor_position", "wait",
        }
        return {
            "risk": "computer_observation" if observing else "computer_control",
            "title": "允许模型观察电脑？" if observing else "允许模型操作电脑？",
            "detail": f"电脑操作：{requested_action}",
            "allow_session": True,
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
