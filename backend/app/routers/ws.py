# backend/app/routers/ws.py
import asyncio
import json
import logging
from contextlib import suppress
from threading import RLock
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from ..models.schemas import PostMessageActionV1
from ..request_context import (
    current_request_id,
    normalize_request_id,
    reset_request_id,
    set_request_id,
)
from ..services.agent_run_service import agent_run_service
from ..services.event_stream import (
    CURSOR_RESET_CLOSE_CODE,
    WebSocketEventStream,
    normalize_stream_id,
    parse_event_cursor,
)
from ..services.local_auth import token_is_valid, websocket_origin_is_allowed, websocket_token
from ..services.orchestrator import SessionOrchestrator
from ..services.outbound_events import (
    DEFAULT_OUTBOUND_SEND_TIMEOUT_SECONDS,
    SLOW_CONSUMER_CLOSE_CODE,
    OutboundDeliveryError,
    OutboundEventQueue,
    OutboundEventWriter,
    OutboundQueueClosed,
    QueuedOutboundEvent,
    SlowConsumerError,
)
from ..services.run_admission import admission_resources_for_action
from ..services.run_dispatcher import (
    RunDispatchCancelled,
    RunDispatcherStopped,
    RunDispatchHandle,
    RunOwnershipConflict,
    RunQueueFull,
    run_dispatcher,
)
from ..services.tool_registry import ToolCreate, ToolInvoke, tool_registry

router = APIRouter()
logger = logging.getLogger(__name__)
DESKTOP_ACTION_TIMEOUT_SECONDS = 30.0
V1_POST_MESSAGE_ADAPTER: TypeAdapter[PostMessageActionV1] = TypeAdapter(
    PostMessageActionV1
)
_ACTIVE_STREAM_LOCK = RLock()
_ACTIVE_STREAMS: dict[tuple[str, str], WebSocket] = {}


def _claim_event_stream(
    session_id: str,
    stream_id: str,
    websocket: WebSocket,
) -> WebSocket | None:
    key = (str(session_id), str(stream_id))
    with _ACTIVE_STREAM_LOCK:
        previous = _ACTIVE_STREAMS.get(key)
        _ACTIVE_STREAMS[key] = websocket
    return previous


def _release_event_stream(
    session_id: str,
    stream_id: str,
    websocket: WebSocket,
) -> None:
    key = (str(session_id), str(stream_id))
    with _ACTIVE_STREAM_LOCK:
        if _ACTIVE_STREAMS.get(key) is websocket:
            _ACTIVE_STREAMS.pop(key, None)

@router.websocket("/v1/sessions/{session_id}")
@router.websocket("/sessions/{session_id}")
async def ws_endpoint(
    websocket: WebSocket,
    session_id: str,
):
    strict_v1_contract = "/v1/sessions/" in websocket.url.path
    token, selected_protocol = websocket_token(websocket)
    if not websocket_origin_is_allowed(websocket) or not token_is_valid(token):
        await websocket.close(code=1008, reason="Unauthorized WebSocket connection")
        return
    stream_id = ""
    requested_cursor = 0
    if strict_v1_contract:
        stream_id = normalize_stream_id(websocket.query_params.get("stream_id"))
        try:
            requested_cursor = parse_event_cursor(websocket.query_params.get("cursor"))
        except ValueError:
            await websocket.close(code=1008, reason="Invalid event cursor")
            return
    await websocket.accept(subprotocol=selected_protocol)
    tenant_id = "demo-tenant"
    send_lock = asyncio.Lock()
    pending_desktop_actions: dict[
        str,
        tuple[asyncio.Future[dict], dict],
    ] = {}
    connection_owner_id = str(uuid4())
    active_run_task: asyncio.Task | None = None
    active_run_id: str | None = None
    active_run: dict | None = None
    active_dispatch: RunDispatchHandle | None = None
    outbound_queue: OutboundEventQueue | None = None
    outbound_writer_task: asyncio.Task[None] | None = None
    event_stream = (
        WebSocketEventStream(
            agent_run_service.database,
            session_id,
            stream_id,
        )
        if strict_v1_contract
        else None
    )

    def expected_revision(action: dict) -> int:
        value = action.get("expected_revision")
        if value is None:
            raise ValueError("expected_revision is required")
        return int(value)

    async def send_payload(payload: dict):
        if payload.get("type") == "error":
            payload = {
                **payload,
                "request_id": payload.get("request_id") or current_request_id(),
            }
        if event_stream is not None:
            queue = outbound_queue
            if queue is None:
                raise WebSocketDisconnect(
                    code=1011,
                    reason="outbound_stream_unavailable",
                )
            try:
                await queue.put(
                    payload,
                    request_id=payload.get("request_id") or current_request_id(),
                    run_id=active_run_id,
                    wait_for_delivery=payload.get("type") != "delta",
                )
            except SlowConsumerError as exc:
                raise WebSocketDisconnect(
                    code=SLOW_CONSUMER_CLOSE_CODE,
                    reason="slow_consumer",
                ) from exc
            except OutboundDeliveryError as exc:
                raise WebSocketDisconnect(
                    code=1011,
                    reason="outbound_delivery_failed",
                ) from exc
            except OutboundQueueClosed as exc:
                raise WebSocketDisconnect(
                    code=1001,
                    reason="outbound_stream_closed",
                ) from exc
            return
        async with send_lock:
            await websocket.send_text(json.dumps(payload, ensure_ascii=False))

    if event_stream is not None:
        if not event_stream.cursor_is_replayable(requested_cursor):
            await websocket.close(
                code=CURSOR_RESET_CLOSE_CODE,
                reason="event_cursor_reset",
            )
            return
        previous = _claim_event_stream(session_id, stream_id, websocket)
        if previous is not None and previous is not websocket:
            with suppress(RuntimeError):
                await previous.close(
                    code=CURSOR_RESET_CLOSE_CODE,
                    reason="event_stream_replaced",
                )
        try:
            async with send_lock:
                for replayed in event_stream.replay_after(requested_cursor):
                    await asyncio.wait_for(
                        websocket.send_text(json.dumps(replayed, ensure_ascii=False)),
                        timeout=DEFAULT_OUTBOUND_SEND_TIMEOUT_SECONDS,
                    )
        except TimeoutError:
            _release_event_stream(session_id, stream_id, websocket)
            with suppress(RuntimeError):
                await websocket.close(
                    code=SLOW_CONSUMER_CLOSE_CODE,
                    reason="slow_consumer",
                )
            return
        except WebSocketDisconnect:
            _release_event_stream(session_id, stream_id, websocket)
            return
        except Exception:
            _release_event_stream(session_id, stream_id, websocket)
            raise

        def prepare_outbound(item: QueuedOutboundEvent) -> dict:
            with _ACTIVE_STREAM_LOCK:
                if _ACTIVE_STREAMS.get((session_id, stream_id)) is not websocket:
                    raise WebSocketDisconnect(
                        code=CURSOR_RESET_CLOSE_CODE,
                        reason="event_stream_replaced",
                    )
                return event_stream.append(
                    item.payload,
                    request_id=item.request_id,
                    run_id=item.run_id,
                )

        async def transmit_outbound(outbound: dict) -> None:
            await websocket.send_text(json.dumps(outbound, ensure_ascii=False))

        async def handle_outbound_failure(error: Exception) -> None:
            if isinstance(error, SlowConsumerError):
                code = SLOW_CONSUMER_CLOSE_CODE
                reason = "slow_consumer"
            elif isinstance(error, WebSocketDisconnect):
                code = error.code
                reason = error.reason or "websocket_disconnected"
            else:
                code = 1011
                reason = "outbound_delivery_failed"
            with suppress(RuntimeError):
                await websocket.close(code=code, reason=reason)

        outbound_queue = OutboundEventQueue()
        outbound_writer = OutboundEventWriter(
            outbound_queue,
            prepare=prepare_outbound,
            send=transmit_outbound,
            on_failure=handle_outbound_failure,
        )
        outbound_writer_task = asyncio.create_task(
            outbound_writer.run(),
            name=f"aetherchat-ws-writer:{session_id}:{stream_id}",
        )

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
        await send_payload({
            "type": "agent_run_update",
            "agent_run": run,
            "request_id": action.get("_request_id"),
        })
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
                "request_id": action.get("_request_id"),
            })

    def prepare_agent_run(
        action: dict,
        run_id: str | None,
        resume_mode: str,
        request_id: str,
    ) -> tuple[dict, dict]:
        if run_id is None:
            prepared_action = {
                key: value
                for key, value in action.items()
                if not str(key).startswith("_")
            }
            prepared_action["_request_id"] = request_id
            run = agent_run_service.create(session_id, prepared_action)
        elif resume_mode == "confirm":
            prepared_action, run = agent_run_service.prepare_confirmed_plan(
                run_id=run_id,
                session_id=session_id,
                expected_revision=expected_revision(action),
            )
        else:
            prepared_action, run = agent_run_service.prepare_retry(
                run_id,
                session_id,
            )
        prepared_action["_request_id"] = request_id
        if run_id is not None:
            agent_run_service.record_trace_context(run["id"], request_id)
        return prepared_action, run

    def activate_agent_run(action: dict, run: dict) -> None:
        nonlocal active_run_id, active_run
        active_run_id = str(run["id"])
        active_run = run
        orchestrator.begin_agent_run(
            active_run_id,
            action.get("_pending_steers") or [],
        )

    def mark_agent_run_disconnected(run: dict) -> None:
        current = agent_run_service.database.get_agent_run(
            str(run["id"]),
            include_events=False,
        )
        if current and current.get("status") not in {
            "completed",
            "failed",
            "interrupted",
            "recoverable",
            "waiting_plan_confirmation",
        }:
            agent_run_service.finish(str(run["id"]), disconnected=True)

    async def run_with_dispatch(
        handle: RunDispatchHandle,
        request_id: str,
        original_action: dict,
        run_id: str | None,
        resume_mode: str,
        prepared_action: dict | None = None,
        prepared_run: dict | None = None,
    ) -> None:
        nonlocal active_run_task, active_run_id, active_run, active_dispatch
        execution_cancelled = False
        run_action = prepared_action
        run_record = prepared_run
        try:
            if run_action is None or run_record is None:
                status = await handle.wait_until_started()
                await send_payload({
                    "type": "run_dispatch_started",
                    "dispatch_id": handle.dispatch_id,
                    "started_at": status.started_at,
                    "request_id": request_id,
                })
                try:
                    run_action, run_record = prepare_agent_run(
                        original_action,
                        run_id,
                        resume_mode,
                        request_id,
                    )
                except (ValidationError, TypeError, ValueError) as exc:
                    await send_payload({
                        "type": "error",
                        "code": "invalid_action",
                        "message": str(exc),
                        "request_id": request_id,
                    })
                    return
                except RuntimeError as exc:
                    await send_payload({
                        "type": "error",
                        "code": "plan_revision_conflict",
                        "message": str(exc),
                        "request_id": request_id,
                    })
                    return
                except (KeyError, PermissionError) as exc:
                    await send_payload({
                        "type": "error",
                        "code": "action_failed",
                        "message": str(exc),
                        "request_id": request_id,
                    })
                    return
                activate_agent_run(run_action, run_record)
            await execute_agent_run(run_action, run_record)
        except (RunDispatchCancelled, RunDispatcherStopped) as exc:
            execution_cancelled = True
            try:
                await send_payload({
                    "type": "run_cancelled",
                    "dispatch_id": handle.dispatch_id,
                    "message": str(exc),
                    "detail": str(exc),
                    "request_id": request_id,
                })
            except Exception:
                return
        except asyncio.CancelledError:
            execution_cancelled = True
            if run_record is not None:
                mark_agent_run_disconnected(run_record)
            raise
        except WebSocketDisconnect:
            execution_cancelled = True
            if run_record is not None:
                mark_agent_run_disconnected(run_record)
            return
        except Exception as exc:
            try:
                await send_payload({
                    "type": "error",
                    "code": "action_failed",
                    "message": str(exc),
                    "request_id": request_id,
                })
            except Exception:
                return
        finally:
            if run_record is not None:
                orchestrator.end_agent_run(run_record["id"])
            await handle.release(cancelled=execution_cancelled)
            if active_dispatch is handle:
                active_dispatch = None
                active_run_id = None
                active_run = None
            if active_run_task is asyncio.current_task():
                active_run_task = None

    async def start_agent_run(
        action: dict,
        run_id: str | None = None,
        *,
        resume_mode: str = "retry",
    ):
        nonlocal active_run_task, active_run_id, active_run, active_dispatch
        request_id = str(
            action.get("_request_id")
            or action.get("request_id")
            or current_request_id()
            or normalize_request_id()
        )
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
            active_dispatch = None
        if active_run_task is not None:
            await send_payload({
                "type": "error",
                "code": "run_in_progress",
                "message": "当前会话已有运行中的任务；请先中断或等待完成。",
            })
            return

        try:
            handle = await run_dispatcher.submit(
                owner_id=connection_owner_id,
                session_id=session_id,
                kind="interactive",
                resources=admission_resources_for_action(
                    action,
                    (
                        provider.config
                        for provider in orchestrator.providers.values()
                    ),
                ),
            )
        except RunOwnershipConflict as exc:
            await send_payload({
                "type": "error",
                "code": "run_in_progress",
                "message": str(exc),
                "dispatch_id": exc.dispatch_id,
                "dispatch_state": exc.state,
            })
            return
        except RunQueueFull as exc:
            await send_payload({
                "type": "error",
                "code": "run_queue_full",
                "message": str(exc),
                "queue_capacity": exc.capacity,
            })
            return
        except RunDispatcherStopped as exc:
            await send_payload({
                "type": "error",
                "code": "run_dispatcher_unavailable",
                "message": str(exc),
            })
            return

        active_dispatch = handle
        prepared_action: dict | None = None
        prepared_run: dict | None = None
        try:
            if handle.started:
                prepared_action, prepared_run = prepare_agent_run(
                    action,
                    run_id,
                    resume_mode,
                    request_id,
                )
                activate_agent_run(prepared_action, prepared_run)
            await send_payload({"type": "ack"})
            if handle.queued:
                status = handle.status()
                await send_payload({
                    "type": "run_queued",
                    "dispatch_id": handle.dispatch_id,
                    "queue_position": status.queue_position,
                    "queue_capacity": run_dispatcher.queue_capacity,
                    "queued_at": status.enqueued_at,
                    "request_id": request_id,
                })
        except (Exception, asyncio.CancelledError):
            if prepared_run is not None:
                mark_agent_run_disconnected(prepared_run)
                orchestrator.end_agent_run(prepared_run["id"])
            await handle.release(cancelled=True)
            if active_dispatch is handle:
                active_dispatch = None
                active_run_id = None
                active_run = None
            raise

        active_run_task = asyncio.create_task(
            run_with_dispatch(
                handle,
                request_id,
                action,
                run_id,
                resume_mode,
                prepared_action,
                prepared_run,
            ),
            name=f"aetherchat-run:{session_id}:{handle.dispatch_id}",
        )
        await handle.bind_task(active_run_task)

    async def steer_agent_run(action: dict):
        if (
            active_run_task is not None
            and not active_run_task.done()
            and active_dispatch is not None
            and not active_run_id
        ):
            status = active_dispatch.status()
            await send_payload({
                "type": "error",
                "code": "run_queued",
                "message": "当前运行仍在排队，开始执行后才能追加转向指令。",
                "dispatch_id": active_dispatch.dispatch_id,
                "queue_position": status.queue_position,
            })
            return
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
        _steer_record, user_message = orchestrator.record_steer_message(steer["content"])
        user_event = {"type": "message_complete", "message": user_message}
        agent_run_service.observe(active_run_id, user_event)
        await send_payload({
            "type": "steer_ack",
            "run_id": active_run_id,
            "steer_id": steer["id"],
            "targets": steer["targets"],
        })
        await send_payload(user_event)
        await orchestrator.persist_steer_message(_steer_record)

    try:
        while True:
            data = await websocket.receive_text()
            request_token = set_request_id(normalize_request_id())
            try:
                action = json.loads(data)
                if not isinstance(action, dict):
                    raise ValueError("WebSocket action must be a JSON object")
                request_id = normalize_request_id(action.get("request_id"))
                reset_request_id(request_token)
                request_token = set_request_id(request_id)
                action["_request_id"] = request_id

                if strict_v1_contract and action.get("type") == "post_message":
                    V1_POST_MESSAGE_ADAPTER.validate_python(action)

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
                    requested_dispatch_id = str(
                        action.get("dispatch_id") or ""
                    )
                    if (
                        requested_dispatch_id
                        and (
                            active_dispatch is None
                            or requested_dispatch_id
                            != active_dispatch.dispatch_id
                        )
                    ):
                        await send_payload({
                            "type": "error",
                            "code": "stale_dispatch",
                            "message": "中断请求指定的排队运行不是当前活动调度。",
                        })
                        continue
                    requested_step_id = str(action.get("step_id") or "")
                    if (
                        active_dispatch is not None
                        and active_run_task is not None
                        and not active_run_task.done()
                        and not active_run_id
                    ):
                        if requested_step_id:
                            await send_payload({
                                "type": "error",
                                "code": "step_not_interruptible",
                                "message": "排队中的运行尚无可中断步骤。",
                            })
                            continue
                        dispatch_status = active_dispatch.status()
                        await active_dispatch.cancel()
                        await send_payload({
                            "type": "interrupt_ack",
                            "scope": scope,
                            "dispatch_id": active_dispatch.dispatch_id,
                            "queued": dispatch_status.state == "queued",
                        })
                        continue
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
            finally:
                reset_request_id(request_token)
    except WebSocketDisconnect:
        pass
    finally:
        if event_stream is not None:
            _release_event_stream(session_id, stream_id, websocket)
        if outbound_queue is not None:
            await outbound_queue.close(
                OutboundQueueClosed("WebSocket connection closed")
            )
        if outbound_writer_task is not None and not outbound_writer_task.done():
            outbound_writer_task.cancel()
        if outbound_writer_task is not None:
            await asyncio.gather(outbound_writer_task, return_exceptions=True)
        if outbound_queue is not None:
            snapshot = outbound_queue.snapshot()
            logger.info(
                "WebSocket outbound queue session=%s stream=%s "
                "enqueued=%d emitted=%d coalesced=%d high_water=%d "
                "backpressure_waits=%d",
                session_id,
                stream_id,
                snapshot.enqueued,
                snapshot.emitted,
                snapshot.coalesced_fragments,
                snapshot.high_water,
                snapshot.backpressure_waits,
            )
        for future, _expected in pending_desktop_actions.values():
            if not future.done():
                future.set_result({
                    "success": False,
                    "error": "桌面客户端连接已断开",
                })
        pending_desktop_actions.clear()
        if active_dispatch is not None and not active_run_id:
            await active_dispatch.cancel()
        if active_run_task is not None and not active_run_task.done():
            orchestrator.interrupt("all")
            active_run_task.cancel()
        if active_run_task is not None:
            await asyncio.gather(active_run_task, return_exceptions=True)
        if active_dispatch is not None:
            await active_dispatch.release(cancelled=True)
