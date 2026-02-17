from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from overseer.execution.backend import LocalBackend
from overseer.human_api import HumanAPI
from overseer.integrators import CodexIntegrator

MAX_WS_MESSAGE_BYTES = 4096
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_LOG_LINES = 400


class OverseerDaemon:
    def __init__(
        self,
        backend: LocalBackend,
        integrator: CodexIntegrator,
        human_api: HumanAPI,
        poll_interval_s: float = 0.3,
    ) -> None:
        self.backend = backend
        self.integrator = integrator
        self.human_api = human_api
        self.poll_interval_s = poll_interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
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


def _tail_log(path: Path, line_count: int) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if line_count <= 0:
        return ""
    return "\n".join(lines[-line_count:])


def create_app(daemon: OverseerDaemon) -> Any:
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
    from pydantic import BaseModel

    class QueueResolutionPayload(BaseModel):
        choice: str
        rationale: str
        artifact_path: str | None = None

    app = FastAPI(title="Overseer Local API")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/runs")
    def list_runs() -> list[dict[str, Any]]:
        return daemon.runs()

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

    @app.post("/queue/{request_id}/resolve")
    def resolve_queue_item(request_id: str, payload: QueueResolutionPayload) -> dict[str, str]:
        choice = payload.choice.strip()
        rationale = payload.rationale.strip()
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
                artifact_path=payload.artifact_path,
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
