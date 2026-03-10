from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from fastapi import WebSocket, WebSocketDisconnect, status
except Exception:  # pragma: no cover
    WebSocket = Any  # type: ignore[assignment]
    WebSocketDisconnect = RuntimeError  # type: ignore[assignment]
    status = Any  # type: ignore[assignment]

from overseer.execution.backend import ExecutionBackend
from overseer.human_api import HumanAPI
from overseer.integrators import CodexIntegrator, RunRequest
from overseer.task_store import TaskStore

if TYPE_CHECKING:
    from overseer.handoff.service import HandoffService
    from overseer.overseer_graph import OverseerCoreGraph

MAX_WS_MESSAGE_BYTES = 4096
MAX_MESSAGE_BYTES = 32_768
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TASK_ID_RE = re.compile(r"\b(task-[0-9a-f]{12})\b")
SESSION_ID_RE = re.compile(r"^sess-[0-9a-f]{12}$")
MAX_LOG_LINES = 400


class OverseerDaemon:
    def __init__(
        self,
        backend: ExecutionBackend,
        integrator: CodexIntegrator,
        human_api: HumanAPI,
        task_store: TaskStore | None = None,
        overseer_graph: "OverseerCoreGraph | None" = None,
        handoff_service: "HandoffService | None" = None,
        poll_interval_s: float = 0.3,
    ) -> None:
        self.backend = backend
        self.integrator = integrator
        self.human_api = human_api
        self.task_store = task_store
        self.overseer_graph = overseer_graph
        self.handoff_service = handoff_service
        self.poll_interval_s = poll_interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._graph_lock = threading.Lock()
        self._runs: dict[str, dict[str, Any]] = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.refresh_now()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self.refresh_now()
            time.sleep(self.poll_interval_s)

    def refresh_now(self) -> None:
        runs = self.backend.list_runs()
        snapshot = {record.run_id: asdict(record) for record in runs}
        with self._lock:
            self._runs = snapshot

    def health_payload(self) -> dict[str, Any]:
        backend_kind = getattr(self.backend, "backend_kind", self.backend.__class__.__name__.replace("Backend", "").lower())
        codex_command = getattr(self.integrator, "command", ["codex", "run"])
        codex_bin = codex_command[0] if codex_command else "codex"
        codex_available = shutil.which(codex_bin) is not None
        codex_root = getattr(self.backend, "codex_root", None)
        codex_root_path = Path(codex_root) if codex_root is not None else None
        codex_writable = bool(codex_root_path and codex_root_path.exists() and os.access(codex_root_path, os.W_OK))

        llm_component: dict[str, Any] = {"mode": "unconfigured", "status": "unknown"}
        if self.overseer_graph is not None:
            llm = self.overseer_graph.llm
            if hasattr(llm, "health"):
                llm_component = llm.health()
            else:
                llm_name = llm.__class__.__name__
                llm_component = {
                    "adapter": llm_name,
                    "mode": "stubbed" if llm_name == "FakeLLM" else "configured",
                    "status": "degraded" if llm_name == "FakeLLM" else "ok",
                }

        backend_component: dict[str, Any] = {"kind": backend_kind, "status": "ok"}
        if backend_kind == "celery":
            redis_url = os.environ.get("REDIS_URL", "").strip()
            backend_component["redis_url_configured"] = bool(redis_url)
            backend_component["status"] = "ok" if redis_url else "degraded"
            if not redis_url:
                backend_component["detail"] = "REDIS_URL not set; celery mode is not ready for self-hosted use."

        components = {
            "backend": backend_component,
            "codex": {
                "command": codex_bin,
                "available": codex_available,
                "status": "ok" if codex_available else "degraded",
            },
            "codex_root": {
                "path": str(codex_root_path) if codex_root_path is not None else None,
                "writable": codex_writable,
                "status": "ok" if codex_writable else "degraded",
            },
            "llm": llm_component,
        }
        statuses = {component["status"] for component in components.values()}
        return {
            "status": "ok" if statuses == {"ok"} else "degraded",
            "deployment": "self-hosted",
            "operator_model": "single-operator",
            "recommended_backend": "local",
            "components": components,
        }

    def runs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._runs[key] for key in sorted(self._runs)]

    def run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            if run_id in self._runs:
                return self._runs[run_id]
        record = self.backend.status(run_id)
        payload = asdict(record)
        with self._lock:
            self._runs[run_id] = payload
        return payload

    def handle_message(self, text: str, session_id: str | None = None) -> dict[str, Any]:
        if self.overseer_graph is not None:
            return self._handle_overseer_chat_message(text, session_id=session_id)
        return self._handle_legacy_message(text)

    def _handle_legacy_message(self, text: str) -> dict[str, Any]:
        if self.task_store is None:
            raise RuntimeError("daemon message endpoint unavailable: task store not configured")
        task, created_task_id = self._find_or_create_task(text)
        run_id = self.integrator.submit(RunRequest(task_id=task["id"], objective=task["objective"]))
        self.task_store.update_status(task["id"], "running", run_id=run_id)
        self.refresh_now()
        assistant_text = (
            "Submitted task to Overseer.\n"
            f"Task ID: {task['id']}\n"
            f"Run ID: {run_id}\n"
            "Use the Runs panel to inspect status and logs."
        )
        return {
            "assistant_text": assistant_text,
            "created_task_id": created_task_id,
            "created_run_ids": [run_id],
            "task_id": task["id"],
            "run_id": run_id,
        }

    def create_overseer_session(self) -> dict[str, Any]:
        if self.overseer_graph is None:
            raise RuntimeError("overseer session API unavailable: graph not configured")
        with self._graph_lock:
            session_id = self.overseer_graph.create_session()
            state = self.overseer_graph.load_state(session_id)
        return {
            "session_id": session_id,
            "instance_id": self._instance_id(),
            "assistant_text": f"Created session {session_id}.",
            **self._state_payload(state),
        }

    def list_overseer_sessions(self) -> list[dict[str, Any]]:
        if self.overseer_graph is None:
            raise RuntimeError("overseer session API unavailable: graph not configured")
        sessions: list[dict[str, Any]] = []
        with self._graph_lock:
            for session_id in self.overseer_graph.list_sessions():
                try:
                    state = self.overseer_graph.load_state(session_id)
                except FileNotFoundError:
                    continue
                sessions.append(
                    {
                        "session_id": session_id,
                        "mode": state.get("mode"),
                        "active_run_count": len(state.get("active_runs", {})),
                        "pending_human_requests": list(state.get("pending_human_requests", [])),
                        "updated_at": state.get("updated_at"),
                    }
                )
        return sessions

    def get_overseer_session(self, session_id: str) -> dict[str, Any]:
        if self.overseer_graph is None:
            raise RuntimeError("overseer session API unavailable: graph not configured")
        with self._graph_lock:
            state = self.overseer_graph.load_state(session_id)
        return {
            "session_id": session_id,
            "instance_id": self._instance_id(),
            **self._state_payload(state),
        }

    def tick_overseer_session(self, session_id: str) -> dict[str, Any]:
        if self.overseer_graph is None:
            raise RuntimeError("overseer session API unavailable: graph not configured")
        with self._graph_lock:
            before = self.overseer_graph.load_state(session_id)
            state = self.overseer_graph.tick(session_id)
        self.refresh_now()
        created_run_ids = self._new_run_ids(before, state)
        return {
            "session_id": session_id,
            "instance_id": self._instance_id(),
            "assistant_text": str(state.get("latest_response", "Tick complete.")),
            "created_run_ids": created_run_ids,
            "run_id": created_run_ids[0] if created_run_ids else None,
            **self._state_payload(state),
        }

    def _handle_overseer_chat_message(self, text: str, session_id: str | None) -> dict[str, Any]:
        if self.overseer_graph is None:
            raise RuntimeError("overseer graph not configured")
        raw = text.strip()
        if not raw:
            raise ValueError("text cannot be empty")

        with self._graph_lock:
            current_session_id = session_id
            if current_session_id is None and not raw.startswith("/"):
                current_session_id = self.overseer_graph.create_session()
            if raw.startswith("/"):
                payload = self._handle_slash_command(raw, current_session_id)
                # Slash command handlers may return a new current session.
                payload.setdefault("instance_id", self._instance_id())
                return payload

            if current_session_id is None:
                current_session_id = self.overseer_graph.create_session()
            before = self.overseer_graph.load_state(current_session_id)
            state = self.overseer_graph.submit_user_message(current_session_id, raw)

        self.refresh_now()
        created_run_ids = self._new_run_ids(before, state)
        return {
            "session_id": current_session_id,
            "instance_id": self._instance_id(),
            "assistant_text": str(state.get("latest_response", "")),
            "created_run_ids": created_run_ids,
            "run_id": created_run_ids[0] if created_run_ids else None,
            **self._state_payload(state),
        }

    def _handle_slash_command(self, raw: str, session_id: str | None) -> dict[str, Any]:
        if self.overseer_graph is None:
            raise RuntimeError("overseer graph not configured")
        parts = raw.split()
        cmd = parts[0].lower()
        if cmd == "/new":
            session_id = self.overseer_graph.create_session()
            state = self.overseer_graph.load_state(session_id)
            return {
                "session_id": session_id,
                "assistant_text": f"Created session {session_id}.",
                **self._state_payload(state),
            }
        if cmd == "/resume":
            if len(parts) != 2:
                raise ValueError("usage: /resume <session_id>")
            target = _validate_session_id(parts[1]) or parts[1]
            state = self.overseer_graph.load_state(target)
            return {
                "session_id": target,
                "assistant_text": f"Resumed {target}.",
                **self._state_payload(state),
            }
        if session_id is None:
            raise ValueError("no active session; use /new first")
        if cmd == "/status":
            state = self.overseer_graph.load_state(session_id)
            pending = ",".join(state.get("pending_human_requests", [])) or "-"
            text = (
                f"mode={state.get('mode')} active_runs={len(state.get('active_runs', {}))} "
                f"pending_human={pending}"
            )
            return {"session_id": session_id, "assistant_text": text, **self._state_payload(state)}
        if cmd == "/plan":
            state = self.overseer_graph.load_state(session_id)
            plan = state.get("plan", [])
            text = "\n".join(f"{s['id']} [{s['status']}] {s['title']}" for s in plan) if plan else "No plan yet."
            return {"session_id": session_id, "assistant_text": text, **self._state_payload(state)}
        if cmd == "/tick":
            before = self.overseer_graph.load_state(session_id)
            state = self.overseer_graph.tick(session_id)
            self.refresh_now()
            created_run_ids = self._new_run_ids(before, state)
            return {
                "session_id": session_id,
                "assistant_text": str(state.get("latest_response", "Tick complete.")),
                "created_run_ids": created_run_ids,
                "run_id": created_run_ids[0] if created_run_ids else None,
                **self._state_payload(state),
            }
        if cmd == "/handoff":
            return self._handle_handoff_command(parts, session_id)
        raise ValueError("unknown command")

    def _handle_handoff_command(self, parts: list[str], session_id: str) -> dict[str, Any]:
        if self.handoff_service is None:
            raise RuntimeError("handoff service not configured")
        if len(parts) < 2:
            raise ValueError("usage: /handoff <status|assess|prepare|observe|switch>")
        action = parts[1]
        if action == "status":
            status = self.handoff_service.status(session_id)
            text = self._format_handoff_status(status)
        elif action == "assess":
            assessment = self.handoff_service.assess_pressure(session_id)
            text = json.dumps(assessment.__dict__, indent=2, sort_keys=True)
        elif action == "prepare":
            checkpoint = self.handoff_service.prepare_handoff(session_id, self.handoff_service.instance_id)
            text = f"handoff_id={checkpoint.handoff_id}\nbrief={checkpoint.handoff_brief_path}"
        elif action == "observe" and len(parts) == 3:
            checkpoint = self.handoff_service.register_observer(session_id, parts[2], self.handoff_service.instance_id)
            text = f"handoff_id={checkpoint.handoff_id} mode=observe read_only=true"
        elif action == "switch" and len(parts) == 4:
            checkpoint = self.handoff_service.switch_handoff(
                session_id,
                parts[2],
                from_owner_instance_id=self.handoff_service.instance_id,
                to_owner_instance_id=parts[3],
            )
            text = f"handoff_id={checkpoint.handoff_id} switched_to={parts[3]}"
        else:
            raise ValueError(
                "usage: /handoff <status|assess|prepare|observe <handoff_id>|switch <handoff_id> <to_instance_id>>"
            )
        state = self.overseer_graph.load_state(session_id)
        return {"session_id": session_id, "assistant_text": text, **self._state_payload(state)}

    def _format_handoff_status(self, status: Any) -> str:
        latest = status.latest_assessment or {}
        latest_score = latest.get("score", "-")
        latest_band = latest.get("band", "-")
        lease = status.lease
        lines = [
            f"instance_id={status.instance_id}",
            f"owner={lease.get('owner_instance_id')} lease_epoch={lease.get('lease_epoch')} status={lease.get('status')}",
            f"active_handoff={lease.get('active_handoff_id') or '-'} observers={','.join(lease.get('observer_instance_ids', [])) or '-'}",
            f"pressure_score={latest_score} pressure_band={latest_band}",
        ]
        return "\n".join(lines)

    def _instance_id(self) -> str | None:
        if self.handoff_service is not None:
            return self.handoff_service.instance_id
        return None

    def _state_payload(self, state: dict[str, Any]) -> dict[str, Any]:
        turns = list(state.get("conversation_turns", []))
        return {
            "mode": state.get("mode"),
            "latest_response": state.get("latest_response"),
            "active_run_count": len(state.get("active_runs", {})),
            "pending_human_requests": list(state.get("pending_human_requests", [])),
            "plan": list(state.get("plan", [])),
            "active_runs": state.get("active_runs", {}),
            "conversation_turns": turns[-50:],
        }

    def _new_run_ids(self, before: dict[str, Any], after: dict[str, Any]) -> list[str]:
        before_ids = set((before.get("active_runs") or {}).keys())
        after_ids = set((after.get("active_runs") or {}).keys())
        return sorted(after_ids - before_ids)

    def _find_or_create_task(self, text: str) -> tuple[dict[str, Any], str | None]:
        if self.task_store is None:
            raise RuntimeError("task store not configured")
        match = TASK_ID_RE.search(text)
        if match:
            task_id = match.group(1)
            return self.task_store.get_task(task_id), None
        created = self.task_store.add_task(text)
        return created, created["id"]


def _resolve_queue(
    daemon: OverseerDaemon,
    request_id: str,
    choice: str,
    rationale: str,
    artifact_path: str | None = None,
) -> Path:
    artifact = str(Path(artifact_path)) if artifact_path is not None else None
    return daemon.human_api.resolve_request(
        request_id=request_id,
        choice=choice,
        rationale=rationale,
        artifact_path=artifact,
    )


def _validate_run_id(run_id: str | None) -> str | None:
    if run_id is None:
        return None
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid run_id")
    if ".." in run_id or "/" in run_id or "\\" in run_id:
        raise ValueError("invalid run_id")
    return run_id


def _validate_session_id(session_id: str | None) -> str | None:
    if session_id is None:
        return None
    if not SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("invalid session_id")
    return session_id


def _tail_log(path: Path, line_count: int) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if line_count <= 0:
        return ""
    return "\n".join(lines[-line_count:])


def create_app(daemon: OverseerDaemon) -> Any:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    app = FastAPI(title="Overseer Local API")

    extra_origins = [
        item.strip()
        for item in os.environ.get("OVERSEER_CORS_ORIGINS", "").split(",")
        if item.strip()
    ]
    # Local dev UI runs on Vite (5173) by default, but allow a few common localhost ports.
    allow_origins = sorted(
        {
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:4173",
            "http://localhost:4173",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
            *extra_origins,
        }
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        if hasattr(daemon, "health_payload"):
            return daemon.health_payload()
        return {"status": "ok"}

    @app.get("/runs")
    def list_runs() -> list[dict[str, Any]]:
        return daemon.runs()

    @app.get("/sessions")
    def list_sessions() -> list[dict[str, Any]]:
        try:
            return daemon.list_overseer_sessions()
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/sessions")
    def create_session() -> dict[str, Any]:
        try:
            return daemon.create_overseer_session()
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        try:
            validated = _validate_session_id(session_id) or session_id
            return daemon.get_overseer_session(validated)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (RuntimeError, ValueError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/sessions/{session_id}/message")
    def submit_session_message(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text", "")).strip()
        if not text:
            raise HTTPException(status_code=400, detail="text cannot be empty")
        if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise HTTPException(status_code=413, detail="message too large")
        try:
            validated = _validate_session_id(session_id) or session_id
            return daemon.handle_message(text, session_id=validated)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, KeyError, RuntimeError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/sessions/{session_id}/tick")
    def tick_session(session_id: str) -> dict[str, Any]:
        try:
            validated = _validate_session_id(session_id) or session_id
            return daemon.tick_overseer_session(validated)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        try:
            return daemon.run(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from exc

    @app.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict[str, Any]:
        try:
            run = daemon.integrator.cancel(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from exc
        daemon.refresh_now()
        return {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "status": run.status,
            "exit_code": run.exit_code,
        }

    @app.get("/runs/{run_id}/logs")
    def get_run_logs(run_id: str, lines: int = 150) -> dict[str, str | int]:
        if lines > MAX_LOG_LINES:
            raise HTTPException(status_code=400, detail=f"lines must be <= {MAX_LOG_LINES}")
        if lines < 1:
            raise HTTPException(status_code=400, detail="lines must be >= 1")
        try:
            run = daemon.run(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from exc

        stdout_log = _tail_log(Path(run["stdout_log"]), line_count=lines)
        stderr_log = _tail_log(Path(run["stderr_log"]), line_count=lines)
        return {
            "run_id": run_id,
            "lines": lines,
            "stdout": stdout_log,
            "stderr": stderr_log,
        }

    @app.get("/queue")
    def list_queue() -> list[dict[str, Any]]:
        requests = daemon.human_api.list_requests()
        return [
            {
                "request_id": req.request_id,
                "status": req.status,
                "task_id": req.task_id,
                "run_id": req.run_id,
                "type": req.request_type,
                "urgency": req.urgency,
                "context": req.context,
                "time_required_min": req.time_required_min,
                "options": req.options,
                "recommendation": req.recommendation,
                "why": req.why,
                "unblocks": req.unblocks,
                "reply_format": req.reply_format,
            }
            for req in requests
        ]

    @app.post("/message")
    def submit_message(payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text", "")).strip()
        session_id = payload.get("session_id")
        if not text:
            raise HTTPException(status_code=400, detail="text cannot be empty")
        if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise HTTPException(status_code=413, detail="message too large")
        try:
            validated_session_id = _validate_session_id(str(session_id)) if session_id is not None else None
            return daemon.handle_message(text, session_id=validated_session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, KeyError, RuntimeError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/queue/{request_id}/resolve")
    def resolve_queue_item(request_id: str, payload: dict[str, Any]) -> dict[str, str]:
        choice = str(payload.get("choice", "")).strip()
        rationale = str(payload.get("rationale", "")).strip()
        if not choice:
            raise HTTPException(status_code=400, detail="choice cannot be empty")
        if not rationale:
            raise HTTPException(status_code=400, detail="rationale cannot be empty")
        try:
            resolution_path = _resolve_queue(
                daemon,
                request_id=request_id,
                choice=choice,
                rationale=rationale,
                artifact_path=payload.get("artifact_path"),
            )
        except ValueError as exc:
            if "request not found" in str(exc):
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"request_id": request_id, "resolution_path": str(resolution_path)}

    @app.websocket("/events")
    async def stream_events(websocket: WebSocket) -> None:
        try:
            run_filter = _validate_run_id(websocket.query_params.get("run_id"))
        except ValueError:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid run_id")
            return

        await websocket.accept()
        offsets: dict[str, int] = {}

        async def send_subscription() -> None:
            await websocket.send_json({"type": "subscribed", "run_id": run_filter})

        def iter_event_paths() -> list[Path]:
            if run_filter is not None:
                return [daemon.backend.runs_root / run_filter / "events.jsonl"]
            return sorted(daemon.backend.runs_root.glob("*/events.jsonl"))

        async def flush_events() -> None:
            for events_path in iter_event_paths():
                key = str(events_path)
                seen = offsets.get(key, 0)
                next_offset = seen
                if not events_path.exists():
                    offsets[key] = seen
                    continue
                with events_path.open(encoding="utf-8") as handle:
                    for idx, line in enumerate(handle):
                        next_offset = idx + 1
                        if idx < seen:
                            continue
                        payload = line.strip()
                        if not payload:
                            continue
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        run_id = events_path.parent.name
                        await websocket.send_json({"type": "event", "run_id": run_id, "event": event})
                offsets[key] = next_offset

        await send_subscription()
        try:
            while True:
                await flush_events()
                try:
                    raw = await asyncio.wait_for(
                        websocket.receive_text(), timeout=daemon.poll_interval_s
                    )
                except asyncio.TimeoutError:
                    continue

                if len(raw.encode("utf-8")) > MAX_WS_MESSAGE_BYTES:
                    await websocket.send_json({"type": "error", "detail": "message too large"})
                    continue

                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "detail": "invalid json"})
                    continue
                if not isinstance(message, dict):
                    await websocket.send_json({"type": "error", "detail": "invalid message"})
                    continue

                action = message.get("action")
                if action == "subscribe":
                    try:
                        run_filter = _validate_run_id(message.get("run_id"))
                    except ValueError:
                        await websocket.send_json({"type": "error", "detail": "invalid run_id"})
                        continue
                    offsets = {}
                    await send_subscription()
                elif action == "unsubscribe":
                    run_filter = None
                    offsets = {}
                    await send_subscription()
                elif action == "ping":
                    await websocket.send_json({"type": "pong"})
                else:
                    await websocket.send_json({"type": "error", "detail": "unknown action"})
        except (WebSocketDisconnect, RuntimeError):
            return

    return app


def serve_daemon(daemon: OverseerDaemon, host: str, port: int) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("overseer serve must bind to localhost only")

    import uvicorn

    app = create_app(daemon)
    daemon.start()
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        daemon.stop()
