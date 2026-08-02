# backend/app/routers/ws.py
import asyncio
import json
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ..services.agent_run_service import agent_run_service
from ..services.local_auth import token_is_valid, websocket_origin_is_allowed, websocket_token
from ..services.orchestrator import SessionOrchestrator
from ..services.tool_registry import ToolCreate, ToolInvoke, tool_registry

router = APIRouter()
DESKTOP_ACTION_TIMEOUT_SECONDS = 30.0

@router.websocket("/sessions/{session_id}")
async def ws_endpoint(
    websocket: WebSocket,
    session_id: str,
):
    token, selected_protocol = websocket_token(websocket)
    if not websocket_origin_is_allowed(websocket) or not token_is_valid(token):
        await websocket.close(code=1008, reason="Unauthorized WebSocket connection")
        return
    await websocket.accept(subprotocol=selected_protocol)
    tenant_id = "demo-tenant"
    send_lock = asyncio.Lock()
    pending_desktop_actions: dict[
        str,
        tuple[asyncio.Future[dict], dict],
    ] = {}
    active_run_task: asyncio.Task | None = None
    active_run_id: str | None = None
    active_run: dict | None = None

    def expected_revision(action: dict) -> int:
        value = action.get("expected_revision")
        if value is None:
            raise ValueError("expected_revision is required")
        return int(value)

    async def send_payload(payload: dict):
        async with send_lock:
            await websocket.send_text(json.dumps(payload, ensure_ascii=False))

    async def request_desktop_action(payload: dict) -> dict:
        action_id = str(uuid4())
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        pending_desktop_actions[action_id] = (future, dict(payload))
        await send_payload({
            "type": "desktop_action_required",
            "action_id": action_id,
            "desktop_action": payload,
        })
        try:
            result = await asyncio.wait_for(
                future,
                timeout=DESKTOP_ACTION_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise RuntimeError(
                "外部导航未执行：桌面客户端在 30 秒内没有返回启动确认"
            ) from exc
        finally:
            pending_desktop_actions.pop(action_id, None)
        if result.get("success") is not True:
            raise RuntimeError(
                str(result.get("error") or "桌面客户端未能打开外部链接")
            )
        return result

    def resolve_desktop_action(action: dict) -> None:
        action_id = str(action.get("action_id") or "")
        pending = pending_desktop_actions.get(action_id)
        if pending is None:
            raise ValueError("未知或已过期的桌面动作")
        future, expected = pending
        if future.done():
            raise ValueError("桌面动作已经完成")
        if action.get("success") is not True:
            future.set_result({
                "success": False,
                "error": str(action.get("error") or "桌面客户端执行失败")[:1000],
            })
            return
        receipt = action.get("receipt")
        expected_browser = str(expected.get("browser") or "default")
        expected_launcher = (
            "firefox" if expected_browser == "firefox" else "system_default"
        )
        if (
            not isinstance(receipt, dict)
            or receipt.get("success") is not True
            or receipt.get("verification") != "launcher_accepted"
            or receipt.get("launcher") != expected_launcher
            or receipt.get("browser") != expected_browser
        ):
            future.set_result({
                "success": False,
                "error": "桌面客户端返回了无效或不匹配的启动凭证",
            })
            return
        future.set_result({
            "success": True,
            "kind": "external_navigation",
            "url": str(expected.get("url") or "")[:4000],
            "browser": expected_browser,
            "launcher": expected_launcher,
            "verification": "launcher_accepted",
            "accepted_at": str(receipt.get("accepted_at") or "")[:100],
        })

    orchestrator = SessionOrchestrator(
        session_id,
        tenant_id=tenant_id,
        mode="project",
        desktop_action_handler=request_desktop_action,
    )  # default to project for collab features

    async def execute_agent_run(action: dict, run: dict):
        action["_agent_run_id"] = run["id"]
        await send_payload({"type": "agent_run_update", "agent_run": run})
        try:
            async for event in orchestrator.stream_user_message(action):
                agent_run_service.observe(run["id"], event)
                await send_payload(event)
        except asyncio.CancelledError:
            agent_run_service.finish(run["id"], disconnected=True)
            raise
        except WebSocketDisconnect:
            agent_run_service.finish(run["id"], disconnected=True)
            raise
        except Exception as exc:
            agent_run_service.database.update_agent_run(run["id"], "failed", error=str(exc)[:2000])
            agent_run_service.database.append_agent_event(
                run["id"], "run_failed", {"error": str(exc)[:2000]}
            )
            raise
        else:
            completed = agent_run_service.finish(run["id"])
            await send_payload({
                "type": "agent_run_update", "agent_run": completed,
            })

    async def start_agent_run(
        action: dict,
        run_id: str | None = None,
        *,
        resume_mode: str = "retry",
    ):
        nonlocal active_run_task, active_run_id, active_run
        if (
            resume_mode == "confirm"
            and active_run_task is not None
            and active_run_id == str(run_id or "")
        ):
            await asyncio.gather(active_run_task, return_exceptions=True)
        if active_run_task is not None and active_run_task.done():
            await asyncio.gather(active_run_task, return_exceptions=True)
            active_run_task = None
            active_run_id = None
            active_run = None
        if active_run_task is not None:
            await send_payload({
                "type": "error",
                "code": "run_in_progress",
                "message": "当前会话已有运行中的任务；请先中断或等待完成。",
            })
            return

        if run_id is None:
            action = {key: value for key, value in action.items() if not str(key).startswith("_")}
            run = agent_run_service.create(session_id, action)
        elif resume_mode == "confirm":
            action, run = agent_run_service.prepare_confirmed_plan(
                run_id=run_id,
                session_id=session_id,
                expected_revision=expected_revision(action),
            )
        else:
            action, run = agent_run_service.prepare_retry(run_id, session_id)
        active_run_id = str(run["id"])
        active_run = run
        orchestrator.begin_agent_run(
            active_run_id,
            action.get("_pending_steers") or [],
        )
        await send_payload({"type": "ack"})

        async def runner():
            nonlocal active_run_id, active_run
            try:
                await execute_agent_run(action, run)
            except asyncio.CancelledError:
                raise
            except WebSocketDisconnect:
                return
            except Exception as exc:
                try:
                    await send_payload({
                        "type": "error",
                        "code": "action_failed",
                        "message": str(exc),
                    })
                except Exception:
                    return
            finally:
                orchestrator.end_agent_run(run["id"])
                active_run_id = None
                active_run = None

        active_run_task = asyncio.create_task(
            runner(),
            name=f"aetherchat-run:{session_id}",
        )

    async def steer_agent_run(action: dict):
        if active_run_task is None or active_run_task.done() or not active_run_id:
            await send_payload({
                "type": "error",
                "code": "no_active_run",
                "message": "当前会话没有可转向的运行。",
            })
            return
        requested_run_id = str(action.get("run_id") or active_run_id)
        if requested_run_id != active_run_id:
            await send_payload({
                "type": "error",
                "code": "stale_run",
                "message": "转向消息指定的运行不是当前活动运行。",
            })
            return
        if action.get("attachment_ids"):
            await send_payload({
                "type": "error",
                "code": "steer_attachments_unsupported",
                "message": "运行中转向暂不支持新增附件；请发送纯文本指令。",
            })
            return
        raw_targets = action.get("targets") or []
        targets = [
            str(value) for value in raw_targets
            if str(value).strip() and str(value) != "all"
        ] if isinstance(raw_targets, list) else []
        steer = agent_run_service.queue_steer(
            run_id=active_run_id,
            session_id=session_id,
            content=str(action.get("content") or ""),
            targets=targets,
        )
        orchestrator.queue_steer(steer)
        user_message = orchestrator.record_steer_message(steer["content"])
        user_event = {"type": "message_complete", "message": user_message}
        agent_run_service.observe(active_run_id, user_event)
        await send_payload({
            "type": "steer_ack",
            "run_id": active_run_id,
            "steer_id": steer["id"],
            "targets": steer["targets"],
        })
        await send_payload(user_event)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                action = json.loads(data)
                if not isinstance(action, dict):
                    raise ValueError("WebSocket action must be a JSON object")

                if action.get("type") == "desktop_action_result":
                    resolve_desktop_action(action)
                elif action.get("type") == "post_message":
                    if active_run_task is not None and not active_run_task.done():
                        await steer_agent_run(action)
                    else:
                        await start_agent_run(action)
                elif action.get("type") == "steer_agent_run":
                    await steer_agent_run(action)
                elif action.get("type") == "retry_agent_run":
                    await start_agent_run(action, str(action.get("run_id") or ""))
                elif action.get("type") == "update_agent_plan":
                    updated = agent_run_service.edit_plan(
                        run_id=str(action.get("run_id") or ""),
                        session_id=session_id,
                        expected_revision=expected_revision(action),
                        goal_spec=action.get("goal_spec"),
                        plan_steps=action.get("plan_steps"),
                        change_summary=str(
                            action.get("change_summary") or ""
                        ),
                    )
                    await send_payload({
                        "type": "plan_updated",
                        "run_id": updated["id"],
                        "revision": updated.get("plan_revision"),
                        "goal": updated.get("goal"),
                        "plan_steps": updated.get("plan_steps") or [],
                        "agent_run": updated,
                    })
                elif action.get("type") == "confirm_agent_plan":
                    await start_agent_run(
                        action,
                        str(action.get("run_id") or ""),
                        resume_mode="confirm",
                    )
                elif action.get("type") == "reject_agent_plan":
                    rejected = agent_run_service.reject_plan(
                        run_id=str(action.get("run_id") or ""),
                        session_id=session_id,
                        expected_revision=expected_revision(action),
                    )
                    await send_payload({
                        "type": "plan_rejected",
                        "run_id": rejected["id"],
                        "revision": rejected.get("plan_revision"),
                        "agent_run": rejected,
                    })
                elif action.get("type") == "interrupt":
                    scope = action.get("scope", "all")
                    requested_run_id = str(action.get("run_id") or "")
                    if requested_run_id and requested_run_id != str(active_run_id or ""):
                        await send_payload({
                            "type": "error",
                            "code": "stale_run",
                            "message": "中断请求指定的运行不是当前活动运行。",
                        })
                        continue
                    requested_step_id = str(action.get("step_id") or "")
                    if requested_step_id:
                        current_run = (
                            agent_run_service.database.get_agent_run(
                                active_run_id,
                                include_events=False,
                            )
                            if active_run_id else active_run
                        ) or {}
                        step = next(
                            (
                                item for item in current_run.get("steps", [])
                                if item.get("id") == requested_step_id
                            ),
                            None,
                        )
                        if step is None:
                            step = next(
                                (
                                    item for item in current_run.get("plan_steps", [])
                                    if item.get("id") == requested_step_id
                                ),
                                None,
                            )
                        if step is None:
                            await send_payload({
                                "type": "error",
                                "code": "unknown_step",
                                "message": "中断请求指定的步骤不存在。",
                            })
                            continue
                        scope = str(
                            step.get("model")
                            or step.get("assigned_model")
                            or ""
                        )
                        if not scope:
                            await send_payload({
                                "type": "error",
                                "code": "step_not_interruptible",
                                "message": "该计划步骤没有可中断的模型执行者。",
                            })
                            continue
                    orchestrator.interrupt(scope)
                    if active_run_id:
                        agent_run_service.database.append_agent_event(
                            active_run_id,
                            "interrupt_requested",
                            {
                                "scope": scope,
                                "step_id": requested_step_id or None,
                            },
                        )
                    await send_payload({"type": "interrupt_ack", "scope": scope})
                elif action.get("type") == "create_tool":
                    tool = tool_registry.create(ToolCreate(**(action.get("tool") or {})))
                    await send_payload({"type": "tool_update", "tool": tool})
                elif action.get("type") == "invoke_tool":
                    run = tool_registry.invoke(
                        action.get("tool_id", ""),
                        ToolInvoke(**(action.get("payload") or {})),
                    )
                    await send_payload({"type": "tool_run", "run": run})
                else:
                    await send_payload({
                        "type": "error",
                        "code": "unsupported_action",
                        "message": "Unsupported action",
                    })
            except WebSocketDisconnect:
                raise
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                await send_payload({
                    "type": "error",
                    "code": "invalid_action",
                    "message": str(exc),
                })
            except RuntimeError as exc:
                await send_payload({
                    "type": "error",
                    "code": "plan_revision_conflict",
                    "message": str(exc),
                })
            except (KeyError, PermissionError) as exc:
                await send_payload({
                    "type": "error",
                    "code": "action_failed",
                    "message": str(exc),
                })
            except Exception as exc:
                await send_payload({
                    "type": "error",
                    "code": "action_failed",
                    "message": str(exc),
                })
    except WebSocketDisconnect:
        pass
    finally:
        for future, _expected in pending_desktop_actions.values():
            if not future.done():
                future.set_result({
                    "success": False,
                    "error": "桌面客户端连接已断开",
                })
        pending_desktop_actions.clear()
        if active_run_task is not None and not active_run_task.done():
            orchestrator.interrupt("all")
            active_run_task.cancel()
        if active_run_task is not None:
            await asyncio.gather(active_run_task, return_exceptions=True)
