from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from overseer.codex_store import CodexStore
from overseer.daemon_api import OverseerDaemon, create_app
from overseer.execution.backend import ExecutionRecord, ExecutionRequest, LocalBackend
from overseer.human_api import HumanAPI
from overseer.integrators.codex import CodexIntegrator


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _setup_daemon(tmp_path: Path) -> tuple[OverseerDaemon, TestClient, HumanAPI, LocalBackend]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Overseer Tests")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    (repo / "codex").mkdir(parents=True)

    store = CodexStore(repo)
    store.init_structure()
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "seed")

    human_api = HumanAPI(store)
    backend = LocalBackend(store.codex_root, human_api=human_api)
    integrator = CodexIntegrator(store.repo_root, human_api=human_api, backend=backend)
    daemon = OverseerDaemon(backend=backend, integrator=integrator, human_api=human_api, poll_interval_s=0.05)
    daemon.start()
    client = TestClient(create_app(daemon))
    return daemon, client, human_api, backend


def _create_queued_record(backend: LocalBackend, run_id: str, task_id: str) -> None:
    run_dir = backend.runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_path = run_dir / "meta.json"
    record = ExecutionRecord(
        run_id=run_id,
        task_id=task_id,
        status="queued",
        command=[sys.executable, "-c", "print('queued')"],
        cwd=str(run_dir),
        stdout_log=str(run_dir / "stdout.log"),
        stderr_log=str(run_dir / "stderr.log"),
        meta_path=str(meta_path),
        lock_path=str(run_dir / "run.lock"),
        created_at="2020-01-01T00:00:00Z",
    )
    backend._write_record(meta_path, record)
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "started",
                "at": "2020-01-01T00:00:00Z",
                "payload": {"record": record.__dict__},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_health_endpoint(tmp_path: Path) -> None:
    daemon, client, _, _ = _setup_daemon(tmp_path)
    try:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    finally:
        daemon.stop()


def test_health_endpoint_includes_cors_for_local_ui_origin(tmp_path: Path) -> None:
    class _DummyDaemon:
        pass

    with TestClient(create_app(_DummyDaemon())) as client:
        response = client.get("/health", headers={"Origin": "http://127.0.0.1:5173"})
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"


def test_message_endpoint_validates_and_dispatches(tmp_path: Path) -> None:
    calls: list[tuple[str, str | None]] = []

    class _DummyDaemon:
        def handle_message(self, text: str, session_id: str | None = None) -> dict[str, object]:
            calls.append((text, session_id))
            return {"assistant_text": f"echo: {text}", "created_run_ids": ["run-demo-123"], "session_id": session_id or "sess-aaaaaaaaaaaa"}

    with TestClient(create_app(_DummyDaemon())) as client:
        bad = client.post("/message", json={"text": "   "})
        assert bad.status_code == 400

        ok = client.post("/message", json={"text": "hello"})
        assert ok.status_code == 200
        assert ok.json()["assistant_text"] == "echo: hello"
        assert calls[-1] == ("hello", None)

        with_session = client.post("/message", json={"text": "/status", "session_id": "sess-1234567890ab"})
        assert with_session.status_code == 200
        assert calls[-1] == ("/status", "sess-1234567890ab")

        invalid_session = client.post("/message", json={"text": "hello", "session_id": "../bad"})
        assert invalid_session.status_code == 400


def test_overseer_daemon_graph_backed_chat_auto_creates_session_and_supports_commands(tmp_path: Path) -> None:
    class _DummyBackend:
        def list_runs(self):
            return []

        runs_root = Path("/tmp/nonexistent")

    class _DummyGraph:
        def __init__(self) -> None:
            self.states = {
                "sess-aaaaaaaaaaaa": {
                    "session_id": "sess-aaaaaaaaaaaa",
                    "mode": "conversation",
                    "conversation_turns": [],
                    "active_runs": {},
                    "pending_human_requests": [],
                    "plan": [{"id": "step-1", "status": "pending", "title": "Do x"}],
                    "latest_response": "",
                }
            }

        def create_session(self) -> str:
            return "sess-aaaaaaaaaaaa"

        def list_sessions(self) -> list[str]:
            return ["sess-aaaaaaaaaaaa"]

        def load_state(self, session_id: str):
            return self.states[session_id]

        def submit_user_message(self, session_id: str, text: str):
            state = dict(self.states[session_id])
            state["latest_response"] = f"assistant:{text}"
            state["mode"] = "waiting"
            state["active_runs"] = {"run-111111111111": {"run_id": "run-111111111111"}}
            state["conversation_turns"] = [
                {"role": "user", "content": text},
                {"role": "assistant", "content": state["latest_response"]},
            ]
            self.states[session_id] = state
            return state

        def tick(self, session_id: str):
            state = dict(self.states[session_id])
            state["latest_response"] = "Tick processed."
            state["conversation_turns"] = [
                *state.get("conversation_turns", []),
                {"role": "assistant", "content": "Tick processed."},
            ]
            self.states[session_id] = state
            return state

    class _DummyHandoff:
        instance_id = "ovr-test"

    daemon = OverseerDaemon(
        backend=_DummyBackend(),  # type: ignore[arg-type]
        integrator=object(),  # type: ignore[arg-type]
        human_api=object(),  # type: ignore[arg-type]
        overseer_graph=_DummyGraph(),  # type: ignore[arg-type]
        handoff_service=_DummyHandoff(),  # type: ignore[arg-type]
    )

    first = daemon.handle_message("build x")
    assert first["session_id"] == "sess-aaaaaaaaaaaa"
    assert first["assistant_text"] == "assistant:build x"
    assert first["created_run_ids"] == ["run-111111111111"]
    assert first["run_id"] == "run-111111111111"
    assert first["instance_id"] == "ovr-test"

    status_reply = daemon.handle_message("/status", session_id="sess-aaaaaaaaaaaa")
    assert "mode=" in str(status_reply["assistant_text"])
    assert status_reply["session_id"] == "sess-aaaaaaaaaaaa"

    plan_reply = daemon.handle_message("/plan", session_id="sess-aaaaaaaaaaaa")
    assert "step-1 [pending] Do x" in str(plan_reply["assistant_text"])

    tick_reply = daemon.handle_message("/tick", session_id="sess-aaaaaaaaaaaa")
    assert tick_reply["assistant_text"] == "Tick processed."
    assert tick_reply["session_id"] == "sess-aaaaaaaaaaaa"


def test_session_endpoints_dispatch_to_daemon(tmp_path: Path) -> None:
    class _DummyDaemon:
        def create_overseer_session(self) -> dict[str, object]:
            return {"session_id": "sess-aaaaaaaaaaaa", "assistant_text": "Created session sess-aaaaaaaaaaaa.", "mode": "conversation"}

        def list_overseer_sessions(self) -> list[dict[str, object]]:
            return [{"session_id": "sess-aaaaaaaaaaaa", "mode": "conversation", "active_run_count": 0}]

        def get_overseer_session(self, session_id: str) -> dict[str, object]:
            return {"session_id": session_id, "mode": "conversation", "conversation_turns": []}

        def tick_overseer_session(self, session_id: str) -> dict[str, object]:
            return {"session_id": session_id, "assistant_text": "Tick processed.", "mode": "waiting"}

        def handle_message(self, text: str, session_id: str | None = None) -> dict[str, object]:
            return {"session_id": session_id, "assistant_text": f"echo: {text}", "mode": "conversation"}

    with TestClient(create_app(_DummyDaemon())) as client:
        created = client.post("/sessions")
        assert created.status_code == 200
        assert created.json()["session_id"] == "sess-aaaaaaaaaaaa"

        listed = client.get("/sessions")
        assert listed.status_code == 200
        assert listed.json()[0]["session_id"] == "sess-aaaaaaaaaaaa"

        fetched = client.get("/sessions/sess-aaaaaaaaaaaa")
        assert fetched.status_code == 200
        assert fetched.json()["session_id"] == "sess-aaaaaaaaaaaa"

        msg = client.post("/sessions/sess-aaaaaaaaaaaa/message", json={"text": "hello"})
        assert msg.status_code == 200
        assert msg.json()["assistant_text"] == "echo: hello"

        msg_empty = client.post("/sessions/sess-aaaaaaaaaaaa/message", json={"text": "   "})
        assert msg_empty.status_code == 400

        msg_invalid = client.post("/sessions/not-a-session/message", json={"text": "hello"})
        assert msg_invalid.status_code == 400

        tick = client.post("/sessions/sess-aaaaaaaaaaaa/tick")
        assert tick.status_code == 200
        assert tick.json()["assistant_text"] == "Tick processed."

        tick_invalid = client.post("/sessions/not-a-session/tick")
        assert tick_invalid.status_code == 400


def test_dummy_run_is_visible_in_runs_endpoint(tmp_path: Path) -> None:
    daemon, client, _, backend = _setup_daemon(tmp_path)
    try:
        run_id = "run-daemon-1234"
        run_dir = backend.runs_root / run_id
        request = ExecutionRequest(
            run_id=run_id,
            task_id="task-daemon-123",
            command=[sys.executable, "-c", "print('daemon run')"],
            cwd=tmp_path,
            stdout_log=run_dir / "stdout.log",
            stderr_log=run_dir / "stderr.log",
            meta_path=run_dir / "meta.json",
            lock_path=tmp_path / "dummy.lock",
        )
        backend.submit(request)

        deadline = time.time() + 5
        while time.time() < deadline:
            runs = client.get("/runs").json()
            if any(run["run_id"] == run_id for run in runs):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("run never appeared in /runs")

        detail = client.get(f"/runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["task_id"] == "task-daemon-123"
    finally:
        daemon.stop()


def test_run_log_endpoint_returns_tail_and_validation(tmp_path: Path) -> None:
    daemon, client, _, backend = _setup_daemon(tmp_path)
    try:
        run_id = "run-log-viewer"
        _create_queued_record(backend, run_id=run_id, task_id="task-log-viewer")
        run_dir = backend.runs_root / run_id
        (run_dir / "stdout.log").write_text("line-1\nline-2\nline-3\n", encoding="utf-8")
        (run_dir / "stderr.log").write_text("err-1\nerr-2\n", encoding="utf-8")

        ok = client.get(f"/runs/{run_id}/logs?lines=2")
        assert ok.status_code == 200
        assert ok.json()["stdout"] == "line-2\nline-3"
        assert ok.json()["stderr"] == "err-1\nerr-2"

        too_many = client.get(f"/runs/{run_id}/logs?lines=999")
        assert too_many.status_code == 400

        non_positive = client.get(f"/runs/{run_id}/logs?lines=0")
        assert non_positive.status_code == 400

        max_lines = client.get(f"/runs/{run_id}/logs?lines=400")
        assert max_lines.status_code == 200

        missing = client.get("/runs/run-does-not-exist/logs")
        assert missing.status_code == 404
    finally:
        daemon.stop()


def test_get_unknown_run_returns_404(tmp_path: Path) -> None:
    daemon, client, _, _ = _setup_daemon(tmp_path)
    try:
        response = client.get("/runs/run-does-not-exist")
        assert response.status_code == 404
        assert "run not found" in response.json()["detail"]
    finally:
        daemon.stop()


def test_cancel_endpoint_cancels_queued_run(tmp_path: Path) -> None:
    daemon, client, _, backend = _setup_daemon(tmp_path)
    try:
        run_id = "run-cancel-1234"
        _create_queued_record(backend, run_id=run_id, task_id="task-cancel-123")
        daemon.refresh_now()

        response = client.post(f"/runs/{run_id}/cancel")
        assert response.status_code == 200
        payload = response.json()
        assert payload["run_id"] == run_id
        assert payload["status"] in {"canceling", "canceled"}

        detail = client.get(f"/runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] in {"canceling", "canceled"}
    finally:
        daemon.stop()


def test_queue_resolve_endpoint_success_and_failures(tmp_path: Path) -> None:
    daemon, client, human_api, _ = _setup_daemon(tmp_path)
    try:
        human_api.append_request({"id": "task-queue-123"}, "need human decision")
        request = human_api.list_requests()[0]

        queued = client.get("/queue")
        assert queued.status_code == 200
        queue_payload = queued.json()[0]
        assert queue_payload["request_id"] == request.request_id
        assert queue_payload["type"] == "decision"
        assert queue_payload["urgency"] == "high"
        assert queue_payload["reply_format"]

        response = client.post(
            f"/queue/{request.request_id}/resolve",
            json={"choice": f"  {request.options[0]}  ", "rationale": "  approved  "},
        )
        assert response.status_code == 200
        assert response.json()["request_id"] == request.request_id

        refreshed = human_api.show_request(request.request_id)
        assert refreshed.status == "resolved"


        empty_choice = client.post(
            f"/queue/{request.request_id}/resolve",
            json={"choice": "   ", "rationale": "approved"},
        )
        assert empty_choice.status_code == 400

        invalid = client.post(
            f"/queue/{request.request_id}/resolve",
            json={"choice": "Not an option", "rationale": "bad"},
        )
        assert invalid.status_code == 400

        missing = client.post(
            "/queue/hr-aaaaaaaaaaaa/resolve",
            json={"choice": request.options[0], "rationale": "missing"},
        )
        assert missing.status_code == 404
    finally:
        daemon.stop()


def _receive_until(websocket: TestClient, predicate, limit: int = 200):
    messages = []
    for _ in range(limit):
        payload = websocket.receive_json()
        messages.append(payload)
        if predicate(payload):
            return payload, messages
    raise AssertionError("condition was not met before message limit")


def test_websocket_events_stream_stdout_and_stderr(tmp_path: Path) -> None:
    daemon, client, _, backend = _setup_daemon(tmp_path)
    try:
        run_id = "run-ws-events"
        run_dir = backend.runs_root / run_id
        request = ExecutionRequest(
            run_id=run_id,
            task_id="task-ws-events",
            command=[
                sys.executable,
                "-c",
                "import sys, time; print('hello-out', flush=True); print('hello-err', file=sys.stderr, flush=True); time.sleep(0.05)",
            ],
            cwd=tmp_path,
            stdout_log=run_dir / "stdout.log",
            stderr_log=run_dir / "stderr.log",
            meta_path=run_dir / "meta.json",
            lock_path=tmp_path / "ws-events.lock",
        )

        with client.websocket_connect(f"/events?run_id={run_id}") as websocket:
            subscribed = websocket.receive_json()
            assert subscribed == {"type": "subscribed", "run_id": run_id}
            backend.submit(request)

            _, messages = _receive_until(
                websocket,
                lambda payload: payload.get("type") == "event"
                and payload.get("event", {}).get("type") == "completed",
            )

        event_types = [
            message["event"]["type"]
            for message in messages
            if message.get("type") == "event"
        ]
        assert "stdout" in event_types
        assert "stderr" in event_types
    finally:
        daemon.stop()


def test_websocket_supports_two_subscribers_for_same_run(tmp_path: Path) -> None:
    daemon, client, _, backend = _setup_daemon(tmp_path)
    try:
        run_id = "run-ws-multi"
        run_dir = backend.runs_root / run_id
        request = ExecutionRequest(
            run_id=run_id,
            task_id="task-ws-multi",
            command=[sys.executable, "-c", "print('shared-output', flush=True)"],
            cwd=tmp_path,
            stdout_log=run_dir / "stdout.log",
            stderr_log=run_dir / "stderr.log",
            meta_path=run_dir / "meta.json",
            lock_path=tmp_path / "ws-multi.lock",
        )

        with (
            client.websocket_connect(f"/events?run_id={run_id}") as ws_one,
            client.websocket_connect(f"/events?run_id={run_id}") as ws_two,
        ):
            assert ws_one.receive_json() == {"type": "subscribed", "run_id": run_id}
            assert ws_two.receive_json() == {"type": "subscribed", "run_id": run_id}

            backend.submit(request)

            one_event, _ = _receive_until(
                ws_one,
                lambda payload: payload.get("type") == "event"
                and payload.get("event", {}).get("type") == "stdout",
            )
            two_event, _ = _receive_until(
                ws_two,
                lambda payload: payload.get("type") == "event"
                and payload.get("event", {}).get("type") == "stdout",
            )

        assert one_event["event"]["payload"]["chunk"].strip() == "shared-output"
        assert two_event["event"]["payload"]["chunk"].strip() == "shared-output"
    finally:
        daemon.stop()


def test_websocket_rejects_invalid_run_filter_query(tmp_path: Path) -> None:
    daemon, client, _, _ = _setup_daemon(tmp_path)
    try:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/events?run_id=../../etc/passwd'):
                pass
    finally:
        daemon.stop()


def test_websocket_invalid_json_and_oversized_messages_return_errors(tmp_path: Path) -> None:
    daemon, client, _, _ = _setup_daemon(tmp_path)
    try:
        with client.websocket_connect('/events') as websocket:
            assert websocket.receive_json() == {'type': 'subscribed', 'run_id': None}

            websocket.send_text('not-json')
            assert websocket.receive_json() == {'type': 'error', 'detail': 'invalid json'}

            websocket.send_text(json.dumps(['bad', 'shape']))
            assert websocket.receive_json() == {'type': 'error', 'detail': 'invalid message'}

            websocket.send_text(json.dumps({'action': 'ping', 'payload': 'x' * 5000}))
            assert websocket.receive_json() == {'type': 'error', 'detail': 'message too large'}
    finally:
        daemon.stop()


def test_websocket_subscribe_validation_and_ping(tmp_path: Path) -> None:
    daemon, client, _, _ = _setup_daemon(tmp_path)
    try:
        with client.websocket_connect('/events') as websocket:
            assert websocket.receive_json() == {'type': 'subscribed', 'run_id': None}

            websocket.send_json({'action': 'subscribe', 'run_id': 'run-valid-123'})
            assert websocket.receive_json() == {'type': 'subscribed', 'run_id': 'run-valid-123'}

            websocket.send_json({'action': 'subscribe', 'run_id': '../bad'})
            assert websocket.receive_json() == {'type': 'error', 'detail': 'invalid run_id'}

            websocket.send_json({'action': 'ping'})
            assert websocket.receive_json() == {'type': 'pong'}
    finally:
        daemon.stop()
