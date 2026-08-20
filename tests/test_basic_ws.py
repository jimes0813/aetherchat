import asyncio
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from starlette.websockets import WebSocketDisconnect

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.models.schemas import Message  # noqa: E402
from app.services.chat_database import (  # noqa: E402
    chat_database,
)
from app.services.orchestrator import SessionOrchestrator  # noqa: E402
from app.services.run_dispatcher import run_dispatcher  # noqa: E402
from app.services.structured_logging import diagnostic_ref  # noqa: E402

# Split from tests/test_basic.py for domain isolation.
# Shared env/client fixtures live in tests/conftest.py.

def test_websocket_auth_uses_subprotocol_and_rejects_missing_token(client, monkeypatch):
    monkeypatch.delenv("AETHERCHAT_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.setenv("AETHERCHAT_API_TOKEN", "test-ws-token")
    session_id = str(uuid4())

    with pytest.raises(WebSocketDisconnect) as rejected, client.websocket_connect(f"/ws/sessions/{session_id}"):
        pass
    assert rejected.value.code == 1008

    with client.websocket_connect(
        f"/ws/sessions/{session_id}",
        subprotocols=["aetherchat", "aetherchat.auth.test-ws-token"],
        headers={"origin": "http://127.0.0.1:5173"},
    ) as websocket:
        assert websocket.accepted_subprotocol == "aetherchat"
        websocket.send_json({"type": "unsupported"})
        assert websocket.receive_json()["code"] == "unsupported_action"


def test_websocket_action_error_does_not_close_connection(client):
    session_id = str(uuid4())
    with client.websocket_connect(f"/ws/sessions/{session_id}") as websocket:
        websocket.send_text("{not-json")
        error = websocket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "invalid_action"
        assert error["request_id"]

        websocket.send_json({
            "type": "not_supported",
            "request_id": "ws-diagnostic-77",
        })
        unsupported = websocket.receive_json()
        assert unsupported["type"] == "error"
        assert unsupported["code"] == "unsupported_action"
        assert unsupported["request_id"] == "ws-diagnostic-77"

        websocket.send_json({"type": "interrupt", "scope": "all"})
        acknowledgement = websocket.receive_json()
        assert acknowledgement == {"type": "interrupt_ack", "scope": "all"}


def test_websocket_dispatcher_enforces_session_owner_across_connections(
    client,
    monkeypatch,
):
    released = threading.Event()

    async def slow_stream(self, action):
        yield {"type": "delta", "model": "owner-model", "delta": "started"}
        while not released.is_set():
            await asyncio.sleep(0.01)
        yield {"type": "interrupted", "model": "owner-model"}

    def record_interrupt(self, scope="all"):
        released.set()

    monkeypatch.setattr(SessionOrchestrator, "stream_user_message", slow_stream)
    monkeypatch.setattr(SessionOrchestrator, "interrupt", record_interrupt)
    session_id = str(uuid4())

    with client.websocket_connect(f"/ws/sessions/{session_id}") as owner:
        owner.send_json({
            "type": "post_message",
            "content": "hold the session owner",
            "targets": ["owner-model"],
        })
        assert owner.receive_json() == {"type": "ack"}
        assert owner.receive_json()["type"] == "agent_run_update"
        assert owner.receive_json()["delta"] == "started"

        with client.websocket_connect(f"/ws/sessions/{session_id}") as contender:
            contender.send_json({
                "type": "post_message",
                "content": "must not create a second run",
                "targets": ["owner-model"],
            })
            conflict = contender.receive_json()
            assert conflict["type"] == "error"
            assert conflict["code"] == "run_in_progress"
            assert conflict["dispatch_state"] == "running"

        assert len(chat_database.list_agent_runs(session_id)) == 1
        owner.send_json({"type": "interrupt", "scope": "all"})
        assert owner.receive_json() == {"type": "interrupt_ack", "scope": "all"}


def test_websocket_dispatcher_queues_other_session_without_false_running_record(
    client,
    monkeypatch,
):
    released = threading.Event()
    first_session = str(uuid4())
    second_session = str(uuid4())

    async def capacity_stream(self, action):
        yield {
            "type": "delta",
            "model": "capacity-model",
            "delta": f"started:{self.session_id}",
        }
        if self.session_id == first_session:
            while not released.is_set():
                await asyncio.sleep(0.01)
        yield {
            "type": "message_complete",
            "message": {
                "id": str(uuid4()),
                "session_id": self.session_id,
                "role": "assistant",
                "author_model": "capacity-model",
                "content": "completed",
            },
        }

    def record_interrupt(self, scope="all"):
        if self.session_id == first_session:
            released.set()

    monkeypatch.setattr(run_dispatcher, "capacity", 1)
    monkeypatch.setattr(SessionOrchestrator, "stream_user_message", capacity_stream)
    monkeypatch.setattr(SessionOrchestrator, "interrupt", record_interrupt)

    with client.websocket_connect(f"/ws/sessions/{first_session}") as first:
        first.send_json({
            "type": "post_message",
            "content": "occupy the only slot",
            "targets": ["capacity-model"],
        })
        assert first.receive_json() == {"type": "ack"}
        assert first.receive_json()["type"] == "agent_run_update"
        assert first.receive_json()["delta"] == f"started:{first_session}"

        with client.websocket_connect(f"/ws/sessions/{second_session}") as second:
            second.send_json({
                "type": "post_message",
                "content": "wait for the slot",
                "targets": ["capacity-model"],
            })
            assert second.receive_json() == {"type": "ack"}
            queued = second.receive_json()
            assert queued["type"] == "run_queued"
            assert queued["queue_position"] == 1
            assert queued["queue_capacity"] == run_dispatcher.queue_capacity
            assert chat_database.list_agent_runs(second_session) == []

            first.send_json({"type": "interrupt", "scope": "all"})
            assert first.receive_json() == {
                "type": "interrupt_ack",
                "scope": "all",
            }

            dispatch_started = second.receive_json()
            assert dispatch_started["type"] == "run_dispatch_started"
            assert dispatch_started["dispatch_id"] == queued["dispatch_id"]
            running = second.receive_json()
            assert running["type"] == "agent_run_update"
            assert running["agent_run"]["status"] == "running"
            assert second.receive_json()["delta"] == f"started:{second_session}"
            assert second.receive_json()["type"] == "message_complete"
            completed = second.receive_json()
            assert completed["type"] == "agent_run_update"
            assert completed["agent_run"]["status"] == "completed_with_warnings"

        assert len(chat_database.list_agent_runs(second_session)) == 1


def test_websocket_dispatcher_cancels_queued_run_before_persistence(
    client,
    monkeypatch,
):
    released = threading.Event()
    first_session = str(uuid4())
    queued_session = str(uuid4())

    async def slow_stream(self, action):
        yield {"type": "delta", "model": "queue-model", "delta": "started"}
        if self.session_id == first_session:
            while not released.is_set():
                await asyncio.sleep(0.01)

    def record_interrupt(self, scope="all"):
        if self.session_id == first_session:
            released.set()

    monkeypatch.setattr(run_dispatcher, "capacity", 1)
    monkeypatch.setattr(SessionOrchestrator, "stream_user_message", slow_stream)
    monkeypatch.setattr(SessionOrchestrator, "interrupt", record_interrupt)

    with client.websocket_connect(f"/ws/sessions/{first_session}") as first:
        first.send_json({
            "type": "post_message",
            "content": "occupy capacity",
            "targets": ["queue-model"],
        })
        assert first.receive_json() == {"type": "ack"}
        assert first.receive_json()["type"] == "agent_run_update"
        assert first.receive_json()["type"] == "delta"

        with client.websocket_connect(f"/ws/sessions/{queued_session}") as queued_ws:
            queued_ws.send_json({
                "type": "post_message",
                "content": "cancel before persistence",
                "targets": ["queue-model"],
            })
            assert queued_ws.receive_json() == {"type": "ack"}
            queued = queued_ws.receive_json()
            assert queued["type"] == "run_queued"

            queued_ws.send_json({
                "type": "interrupt",
                "scope": "all",
                "dispatch_id": queued["dispatch_id"],
            })
            cancellation_events = [
                queued_ws.receive_json(),
                queued_ws.receive_json(),
            ]
            assert {event["type"] for event in cancellation_events} == {
                "interrupt_ack",
                "run_cancelled",
            }
            acknowledgement = next(
                event
                for event in cancellation_events
                if event["type"] == "interrupt_ack"
            )
            assert acknowledgement["queued"] is True
            assert acknowledgement["dispatch_id"] == queued["dispatch_id"]
            assert chat_database.list_agent_runs(queued_session) == []

        first.send_json({"type": "interrupt", "scope": "all"})
        assert first.receive_json() == {"type": "interrupt_ack", "scope": "all"}


def test_websocket_dispatcher_preserves_runtime_failure_as_action_failed(
    client,
    monkeypatch,
):
    async def failing_stream(self, action):
        if False:
            yield {"type": "unreachable"}
        raise RuntimeError("provider execution failed")

    monkeypatch.setattr(SessionOrchestrator, "stream_user_message", failing_stream)
    session_id = str(uuid4())

    with client.websocket_connect(f"/ws/sessions/{session_id}") as websocket:
        websocket.send_json({
            "type": "post_message",
            "content": "preserve the execution error contract",
            "targets": ["failing-model"],
        })
        assert websocket.receive_json() == {"type": "ack"}
        assert websocket.receive_json()["type"] == "agent_run_update"
        error = websocket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "action_failed"
        assert error["message"] == "provider execution failed"


def test_websocket_v1_requires_explicit_project_execution_strategy(client, monkeypatch):
    async def contract_stream(self, action):
        assert action["execution_strategy"] == "planned"
        assert action["effective_execution_strategy"] == "planned"
        yield {"type": "v1_contract_observed"}

    monkeypatch.setattr(SessionOrchestrator, "stream_user_message", contract_stream)
    session_id = str(uuid4())
    with client.websocket_connect(f"/ws/v1/sessions/{session_id}") as websocket:
        websocket.send_json({
            "type": "post_message",
            "content": "缺少执行策略",
            "targets": ["Qwen"],
            "mode": "project",
            "request_id": "v1-contract-test",
        })
        error = websocket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "invalid_action"
        assert error["request_id"] == "v1-contract-test"
        assert "execution_strategy" in error["message"]
        assert error["contract_version"] == "1"
        assert error["sequence"] == 1
        assert error["session_id"] == session_id
        assert error["run_id"] is None
        assert error["step_id"] is None
        assert error["event_id"]
        assert error["occurred_at"].endswith("Z")

        websocket.send_json({
            "type": "post_message",
            "content": "显式计划执行",
            "targets": ["Qwen"],
            "mode": "project",
            "execution_strategy": "planned",
            "request_id": "v1-planned-test",
        })
        acknowledgement = websocket.receive_json()
        assert acknowledgement["type"] == "ack"
        assert acknowledgement["contract_version"] == "1"
        assert acknowledgement["sequence"] == 2
        assert acknowledgement["request_id"] == "v1-planned-test"
        assert acknowledgement["session_id"] == session_id
        assert acknowledgement["run_id"]
        assert acknowledgement["step_id"] is None
        update = websocket.receive_json()
        assert update["type"] == "agent_run_update"
        assert update["contract_version"] == "1"
        assert update["sequence"] == 3
        assert update["request_id"] == "v1-planned-test"
        assert update["session_id"] == session_id
        assert update["run_id"] == acknowledgement["run_id"]
        assert update["agent_run"]["effective_execution_strategy"] == "planned"
        assert update["agent_run"]["execution_strategy_source"] == "explicit"
        observed = websocket.receive_json()
        assert observed["type"] == "v1_contract_observed"
        assert observed["contract_version"] == "1"
        assert observed["sequence"] == 4
        assert observed["request_id"] == "v1-planned-test"
        assert observed["session_id"] == session_id
        assert observed["run_id"] == acknowledgement["run_id"]
        completed = websocket.receive_json()
        assert completed["type"] == "agent_run_update"
        assert completed["sequence"] == 5
        assert completed["run_id"] == acknowledgement["run_id"]
        # The contract-only fixture emits no model message, so finishing the
        # run correctly records a terminal failure instead of false success.
        assert completed["agent_run"]["status"] == "failed"
        trace_context = next(
            event
            for event in chat_database.get_agent_run(
                acknowledgement["run_id"]
            )["events"]
            if event["event_type"] == "trace_context"
        )
        assert trace_context["payload"] == {
            "request_ref": diagnostic_ref("request", "v1-planned-test")
        }
        assert len({
            error["event_id"],
            acknowledgement["event_id"],
            update["event_id"],
            observed["event_id"],
            completed["event_id"],
        }) == 5


def test_websocket_v1_replays_persisted_stream_cursor_across_connections(client):
    session_id = str(uuid4())
    stream_id = f"test-stream-{uuid4()}"
    path = f"/ws/v1/sessions/{session_id}?stream_id={stream_id}"

    with client.websocket_connect(f"{path}&cursor=0") as websocket:
        websocket.send_json({
            "type": "unsupported",
            "request_id": "persisted-event-1",
        })
        first = websocket.receive_json()
        assert first["type"] == "error"
        assert first["sequence"] == 1
        assert first["stream_id"] == stream_id

    with client.websocket_connect(f"{path}&cursor=0") as websocket:
        replayed = websocket.receive_json()
        assert replayed == first
        websocket.send_json({
            "type": "unsupported",
            "request_id": "persisted-event-2",
        })
        second = websocket.receive_json()
        assert second["sequence"] == 2
        assert second["stream_id"] == stream_id

    with client.websocket_connect(f"{path}&cursor=2") as websocket:
        websocket.send_json({
            "type": "unsupported",
            "request_id": "persisted-event-3",
        })
        third = websocket.receive_json()
        assert third["sequence"] == 3

    with client.websocket_connect(f"{path}&cursor=999") as websocket:
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()
        assert closed.value.code == 4009


def test_websocket_v1_allows_only_one_active_writer_per_stream(client):
    session_id = str(uuid4())
    stream_id = f"exclusive-stream-{uuid4()}"
    path = f"/ws/v1/sessions/{session_id}?stream_id={stream_id}&cursor=0"

    with client.websocket_connect(path) as first, client.websocket_connect(path) as replacement:
        with pytest.raises(WebSocketDisconnect) as replaced:
            first.receive_json()
        assert replaced.value.code == 4009

        replacement.send_json({
            "type": "unsupported",
            "request_id": "exclusive-writer",
        })
        event = replacement.receive_json()
        assert event["sequence"] == 1
        assert event["stream_id"] == stream_id


def test_websocket_v1_coalesces_delta_bursts_and_legacy_keeps_fragments(
    client,
    monkeypatch,
):
    async def burst_stream(self, action):
        for fragment in ("A", "ether", "Chat"):
            yield {
                "type": "delta",
                "model": "burst-model",
                "delta": fragment,
                "ts": "2026-08-11T08:00:00Z",
            }
        yield {"type": "burst_boundary"}

    monkeypatch.setattr(SessionOrchestrator, "stream_user_message", burst_stream)

    v1_session_id = str(uuid4())
    with client.websocket_connect(
        f"/ws/v1/sessions/{v1_session_id}"
    ) as websocket:
        websocket.send_json({
            "type": "post_message",
            "content": "测试突发增量",
            "targets": ["burst-model"],
            "mode": "project",
            "execution_strategy": "direct",
            "request_id": "delta-burst-v1",
        })
        assert websocket.receive_json()["type"] == "ack"
        assert websocket.receive_json()["type"] == "agent_run_update"
        delta = websocket.receive_json()
        boundary = websocket.receive_json()
        completed = websocket.receive_json()

    assert delta["type"] == "delta"
    assert delta["delta"] == "AetherChat"
    assert delta["coalesced_count"] == 3
    assert delta["request_id"] == "delta-burst-v1"
    assert [delta["sequence"], boundary["sequence"], completed["sequence"]] == [
        3,
        4,
        5,
    ]
    assert boundary["type"] == "burst_boundary"
    assert completed["type"] == "agent_run_update"

    legacy_session_id = str(uuid4())
    with client.websocket_connect(
        f"/ws/sessions/{legacy_session_id}"
    ) as websocket:
        websocket.send_json({
            "type": "post_message",
            "content": "验证旧版逐片发送",
            "targets": ["burst-model"],
        })
        assert websocket.receive_json()["type"] == "ack"
        assert websocket.receive_json()["type"] == "agent_run_update"
        legacy_deltas = [websocket.receive_json() for _index in range(3)]
        assert websocket.receive_json()["type"] == "burst_boundary"
        assert websocket.receive_json()["type"] == "agent_run_update"

    assert [event["delta"] for event in legacy_deltas] == ["A", "ether", "Chat"]
    assert all("coalesced_count" not in event for event in legacy_deltas)


def test_websocket_waits_for_desktop_navigation_receipt(client, monkeypatch):
    async def navigation_stream(self, action):
        assert self.desktop_action_handler is not None
        receipt = await self.desktop_action_handler({
            "kind": "open_external",
            "url": "https://www.youtube.com",
            "browser": "firefox",
        })
        yield {"type": "desktop_action_test", "receipt": receipt}

    monkeypatch.setattr(SessionOrchestrator, "stream_user_message", navigation_stream)

    session_id = str(uuid4())
    with client.websocket_connect(f"/ws/sessions/{session_id}") as websocket:
        websocket.send_json({
            "type": "post_message",
            "content": "用 Firefox 打开 YouTube",
            "targets": ["Qwen"],
        })
        assert websocket.receive_json() == {"type": "ack"}
        assert websocket.receive_json()["type"] == "agent_run_update"
        request = websocket.receive_json()
        assert request["type"] == "desktop_action_required"
        assert request["desktop_action"] == {
            "kind": "open_external",
            "url": "https://www.youtube.com",
            "browser": "firefox",
        }

        websocket.send_json({
            "type": "desktop_action_result",
            "action_id": request["action_id"],
            "success": True,
            "receipt": {
                "success": True,
                "kind": "external_navigation",
                "url": "https://www.youtube.com/",
                "browser": "firefox",
                "launcher": "firefox",
                "verification": "launcher_accepted",
                "accepted_at": "2026-08-01T10:05:52Z",
            },
        })
        completed = websocket.receive_json()
        assert completed["type"] == "desktop_action_test"
        assert completed["receipt"] == {
            "success": True,
            "kind": "external_navigation",
            "url": "https://www.youtube.com",
            "browser": "firefox",
            "launcher": "firefox",
            "verification": "launcher_accepted",
            "accepted_at": "2026-08-01T10:05:52Z",
        }


def test_websocket_interrupt_is_processed_while_agent_run_is_active(client, monkeypatch):
    interrupted = threading.Event()

    async def slow_stream(self, action):
        yield {"type": "delta", "model": "slow-model", "delta": "started"}
        while not interrupted.is_set():
            await asyncio.sleep(0.01)
        yield {"type": "interrupted", "model": "slow-model"}

    def record_interrupt(self, scope="all"):
        interrupted.set()

    monkeypatch.setattr(SessionOrchestrator, "stream_user_message", slow_stream)
    monkeypatch.setattr(SessionOrchestrator, "interrupt", record_interrupt)

    session_id = str(uuid4())
    with client.websocket_connect(f"/ws/sessions/{session_id}") as websocket:
        websocket.send_json({
            "type": "post_message",
            "content": "start a slow run",
            "targets": ["slow-model"],
        })
        assert websocket.receive_json() == {"type": "ack"}

        saw_active_delta = False
        for _ in range(5):
            event = websocket.receive_json()
            if event.get("type") == "delta":
                saw_active_delta = True
                break
        assert saw_active_delta is True

        websocket.send_json({"type": "interrupt", "scope": "all"})
        saw_ack = False
        for _ in range(6):
            event = websocket.receive_json()
            if event == {"type": "interrupt_ack", "scope": "all"}:
                saw_ack = True
                break
        assert saw_ack is True
        assert interrupted.wait(timeout=1)


def test_websocket_interrupt_can_target_persisted_step(client, monkeypatch):
    interrupted = threading.Event()
    interrupted_scope = []

    async def slow_stream(self, action):
        yield {"type": "delta", "model": "model-a", "delta": "a"}
        yield {"type": "delta", "model": "model-b", "delta": "b"}
        while not interrupted.is_set():
            await asyncio.sleep(0.01)
        yield {"type": "interrupted", "model": interrupted_scope[0]}

    def record_interrupt(self, scope="all"):
        interrupted_scope.append(scope)
        interrupted.set()

    monkeypatch.setattr(SessionOrchestrator, "stream_user_message", slow_stream)
    monkeypatch.setattr(SessionOrchestrator, "interrupt", record_interrupt)

    session_id = str(uuid4())
    with client.websocket_connect(f"/ws/sessions/{session_id}") as websocket:
        websocket.send_json({
            "type": "post_message",
            "content": "run two steps",
            "targets": ["model-a", "model-b"],
        })
        assert websocket.receive_json() == {"type": "ack"}
        update = websocket.receive_json()
        assert update["type"] == "agent_run_update"
        run_id = update["agent_run"]["id"]
        assert websocket.receive_json()["model"] == "model-a"
        assert websocket.receive_json()["model"] == "model-b"

        detail = client.get(
            f"/agent-runs/{run_id}",
            params={"session_id": session_id},
        ).json()
        step_id = next(step["id"] for step in detail["steps"] if step["model"] == "model-a")
        websocket.send_json({
            "type": "interrupt",
            "run_id": run_id,
            "step_id": step_id,
        })
        assert websocket.receive_json() == {
            "type": "interrupt_ack",
            "scope": "model-a",
        }
        assert interrupted.wait(timeout=1)
        assert interrupted_scope == ["model-a"]


def test_websocket_interrupt_can_target_persisted_plan_step(client, monkeypatch):
    interrupted = threading.Event()
    interrupted_scope = []

    async def slow_plan_stream(self, action):
        plan = self.run_service.ensure_plan(
            run_id=action["_agent_run_id"],
            session_id=self.session_id,
            action={
                **action,
                "mode": "project",
                "execution_strategy": "planned",
            },
            models=["model-a"],
            profiles={"model-a": "Worker"},
        )
        worker = next(
            step for step in plan["steps"] if step["kind"] == "worker"
        )
        running = self.run_service.start_plan_step(
            action["_agent_run_id"], worker["id"]
        )
        yield {
            "type": "plan_created",
            "run_id": action["_agent_run_id"],
            "goal": plan["goal"],
            "plan_steps": plan["steps"],
        }
        yield {
            "type": "plan_step_update",
            "run_id": action["_agent_run_id"],
            "plan_step": running,
        }
        while not interrupted.is_set():
            await asyncio.sleep(0.01)
        yield {"type": "interrupted", "model": interrupted_scope[0]}

    def record_interrupt(self, scope="all"):
        interrupted_scope.append(scope)
        interrupted.set()

    monkeypatch.setattr(SessionOrchestrator, "stream_user_message", slow_plan_stream)
    monkeypatch.setattr(SessionOrchestrator, "interrupt", record_interrupt)

    session_id = str(uuid4())
    with client.websocket_connect(f"/ws/sessions/{session_id}") as websocket:
        websocket.send_json({
            "type": "post_message",
            "content": "run a persisted plan step",
            "targets": ["model-a"],
            "mode": "project",
            "execution_strategy": "planned",
        })
        assert websocket.receive_json() == {"type": "ack"}
        assert websocket.receive_json()["type"] == "agent_run_update"
        plan_event = websocket.receive_json()
        assert plan_event["type"] == "plan_created"
        update = websocket.receive_json()
        assert update["type"] == "plan_step_update"
        plan_step_id = update["plan_step"]["id"]

        websocket.send_json({
            "type": "interrupt",
            "run_id": update["run_id"],
            "step_id": plan_step_id,
        })
        assert websocket.receive_json() == {
            "type": "interrupt_ack",
            "scope": "model-a",
        }
        assert interrupted.wait(timeout=1)
        assert interrupted_scope == ["model-a"]


def test_websocket_plan_edit_and_confirmation_resume_same_run(client, monkeypatch):
    phases = []

    async def confirmation_stream(self, action):
        run_id = action["_agent_run_id"]
        if not action.get("_plan_confirmed"):
            phases.append("draft")
            plan = self.run_service.ensure_plan(
                run_id=run_id,
                session_id=self.session_id,
                action=action,
                models=["model-a"],
                profiles={"model-a": "Architect/Reviewer"},
            )
            yield {
                "type": "plan_created",
                "run_id": run_id,
                "goal": plan["goal"],
                "plan_steps": plan["steps"],
            }
            waiting = self.run_service.database.update_agent_run(
                run_id, "waiting_plan_confirmation", error=None
            )
            yield {
                "type": "plan_confirmation_required",
                "run_id": run_id,
                "revision": waiting["plan_revision"],
                "agent_run": waiting,
            }
            return

        phases.append("execute")
        for step in self.run_service.database.list_agent_plan_steps(run_id):
            if step["kind"] == "planner":
                continue
            self.run_service.start_plan_step(run_id, step["id"])
            self.run_service.finish_plan_step(
                run_id,
                step["id"],
                status="completed",
                content=f"completed-{step['kind']}",
            )
        message = Message(
            id=uuid4(),
            session_id=self.session_id,
            author_model="model-a",
            role="assistant",
            content="confirmed final",
            created_at=datetime.now(UTC),
        )
        yield {
            "type": "message_complete",
            "message": message.model_dump(mode="json"),
        }

    monkeypatch.setattr(
        SessionOrchestrator,
        "stream_user_message",
        confirmation_stream,
    )
    session_id = str(uuid4())
    with client.websocket_connect(f"/ws/sessions/{session_id}") as websocket:
        websocket.send_json({
            "type": "post_message",
            "content": "review this plan first",
            "targets": ["model-a"],
            "mode": "project",
            "execution_strategy": "planned",
            "plan_confirmation_required": True,
        })
        assert websocket.receive_json() == {"type": "ack"}
        initial = websocket.receive_json()
        assert initial["type"] == "agent_run_update"
        run_id = initial["agent_run"]["id"]
        plan_event = websocket.receive_json()
        assert plan_event["type"] == "plan_created"
        worker = next(
            step for step in plan_event["plan_steps"]
            if step["kind"] == "worker"
        )
        required = websocket.receive_json()
        assert required["type"] == "plan_confirmation_required"
        assert required["agent_run"]["status"] == "waiting_plan_confirmation"
        waiting_update = websocket.receive_json()
        assert waiting_update["type"] == "agent_run_update"
        assert waiting_update["agent_run"]["status"] == "waiting_plan_confirmation"
        assert phases == ["draft"]

        websocket.send_json({
            "type": "update_agent_plan",
            "run_id": run_id,
            "expected_revision": 1,
            "goal_spec": {
                "objective": "edited websocket goal",
                "success_criteria": ["must be confirmed"],
                "constraints": ["read only before approval"],
                "deliverable": "confirmed result",
            },
            "plan_steps": [{
                "id": worker["id"],
                "title": "edited worker",
                "description": "wait for confirmation",
                "assigned_model": "model-a",
                "role": "Reviewed Worker",
            }],
            "change_summary": "edited over websocket",
        })
        updated = websocket.receive_json()
        assert updated["type"] == "plan_updated"
        assert updated["revision"] == 2
        assert updated["goal"]["objective"] == "edited websocket goal"

        websocket.send_json({
            "type": "confirm_agent_plan",
            "run_id": run_id,
            "expected_revision": 2,
        })
        assert websocket.receive_json() == {"type": "ack"}
        approved = websocket.receive_json()
        assert approved["type"] == "agent_run_update"
        assert approved["agent_run"]["plan_status"] == "approved"
        final_message = websocket.receive_json()
        assert final_message["type"] == "message_complete"
        assert final_message["message"]["content"] == "confirmed final"
        completed = websocket.receive_json()
        assert completed["type"] == "agent_run_update"
        assert completed["agent_run"]["status"] == "completed_with_warnings"
        assert phases == ["draft", "execute"]


def test_websocket_second_message_becomes_persisted_steer(client, monkeypatch):
    async def steerable_stream(self, action):
        yield {"type": "delta", "model": "slow-model", "delta": "initial plan"}
        for _ in range(200):
            if self._has_pending_steers("slow-model"):
                break
            await asyncio.sleep(0.01)
        steers = self._consume_steers("slow-model")
        assert len(steers) == 1
        for steer in steers:
            self.run_service.mark_steer_applied(
                action["_agent_run_id"],
                steer["id"],
                "slow-model",
            )
            yield {
                "type": "steer_applied",
                "model": "slow-model",
                "steer_id": steer["id"],
            }
        yield {"type": "stream_reset", "model": "slow-model"}
        yield {"type": "delta", "model": "slow-model", "delta": "replanned"}
        message = Message(
            id=uuid4(),
            session_id=self.session_id,
            author_model="slow-model",
            role="assistant",
            content="已按新方向完成",
            created_at=datetime.now(UTC),
        )
        yield {"type": "message_complete", "message": message.model_dump(mode="json")}

    monkeypatch.setattr(SessionOrchestrator, "stream_user_message", steerable_stream)

    session_id = str(uuid4())
    run_id = None
    saw_steer_ack = False
    saw_user_steer = False
    saw_applied = False
    saw_completed = False
    with client.websocket_connect(f"/ws/sessions/{session_id}") as websocket:
        websocket.send_json({
            "type": "post_message",
            "content": "start a steerable run",
            "targets": ["slow-model"],
        })
        assert websocket.receive_json() == {"type": "ack"}
        while True:
            event = websocket.receive_json()
            if event.get("type") == "agent_run_update":
                run_id = event["agent_run"]["id"]
            if event.get("type") == "delta":
                break

        websocket.send_json({
            "type": "post_message",
            "content": "改用方案 B，并停止旧计划",
            "targets": ["slow-model"],
        })
        for _ in range(12):
            event = websocket.receive_json()
            if event.get("type") == "steer_ack":
                saw_steer_ack = event.get("run_id") == run_id
            elif (
                event.get("type") == "message_complete"
                and event.get("message", {}).get("role") == "user"
            ):
                saw_user_steer = event["message"]["content"] == "改用方案 B，并停止旧计划"
            elif event.get("type") == "steer_applied":
                saw_applied = event.get("model") == "slow-model"
            elif (
                event.get("type") == "agent_run_update"
                and event.get("agent_run", {}).get("status") == "completed_with_warnings"
            ):
                saw_completed = True
                break

    assert run_id
    assert saw_steer_ack is True
    assert saw_user_steer is True
    assert saw_applied is True
    assert saw_completed is True
    detail = client.get(f"/agent-runs/{run_id}", params={"session_id": session_id}).json()
    assert detail["steers"][0]["content"] == "改用方案 B，并停止旧计划"
    assert detail["steers"][0]["applied_models"] == ["slow-model"]
    event_types = [event["event_type"] for event in detail["events"]]
    assert "steer_queued" in event_types
    assert "steer_applied" in event_types
    stored = client.get(f"/sessions/{session_id}/messages").json()
    assert any(message["content"] == "改用方案 B，并停止旧计划" for message in stored)
