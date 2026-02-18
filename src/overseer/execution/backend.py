from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from overseer.fs import atomic_write_text
from overseer.human_api import HumanAPI
from overseer.locks import file_lock
from overseer.execution.run_store import RunStore, RunSubmission, SQLiteRunStore

RunStatus = Literal["queued", "running", "canceling", "done", "failed", "canceled"]

TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "failed", "canceled"})
REQUIRES_NOTES_STATUSES: frozenset[str] = frozenset({"done", "failed"})


@dataclass(frozen=True)
class ExecutionRequest:
    run_id: str
    task_id: str
    command: list[str]
    cwd: Path
    stdout_log: Path
    stderr_log: Path
    meta_path: Path
    lock_path: Path


@dataclass
class ExecutionRecord:
    run_id: str
    task_id: str
    status: RunStatus
    command: list[str]
    cwd: str
    stdout_log: str
    stderr_log: str
    meta_path: str
    lock_path: str
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    exit_code: int | None = None
    worker_pid: int | None = None
    notes_enforced: bool = False
    failure_reason: str | None = None
    heartbeat_at: str | None = None


class ExecutionBackend(Protocol):
    def submit(self, request: ExecutionRequest) -> str: ...

    def status(self, run_id: str) -> ExecutionRecord: ...

    def list_runs(self) -> list[ExecutionRecord]: ...

    def cancel(self, run_id: str) -> ExecutionRecord: ...

    def reconcile(self, stale_after_seconds: int) -> list[ExecutionRecord]: ...


class LocalBackend:
    def __init__(
        self,
        codex_root: Path,
        human_api: HumanAPI | None = None,
        worker_role: str = "builder",
        run_store: RunStore | None = None,
    ) -> None:
        self.codex_root = codex_root
        self.runs_root = codex_root / "08_TELEMETRY" / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.human_api = human_api
        self.worker_role = worker_role
        self.run_store = run_store or SQLiteRunStore(codex_root)

    @staticmethod
    def new_run_id() -> str:
        return f"run-{uuid4().hex[:12]}"

    def _normalize_run_id(self, run_id: str | Path) -> str:
        if isinstance(run_id, Path):
            return run_id.parent.name
        return run_id

    def _events_path(self, run_id: str | Path) -> Path:
        rid = self._normalize_run_id(run_id)
        return self.runs_root / rid / "events.jsonl"

    def _events_lock_path(self, run_id: str | Path) -> Path:
        rid = self._normalize_run_id(run_id)
        return self.runs_root / rid / "events.lock"

    def _append_event(self, run_id: str | Path, event_type: str, payload: dict[str, Any]) -> None:
        rid = self._normalize_run_id(run_id)
        if event_type == "started" and isinstance(payload.get("record"), dict):
            record_payload = payload["record"]
            if not self._run_exists(rid):
                rec = ExecutionRecord(**record_payload)
                self._write_record(Path(rec.meta_path), rec)
        self.run_store.append_event(rid, event_type, payload)
        event = {
            "type": event_type,
            "at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        events_path = self._events_path(rid)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")

    def _record_to_meta(self, record: ExecutionRecord) -> dict[str, Any]:
        return asdict(record)

    def _write_meta(self, record: ExecutionRecord) -> None:
        if not record.meta_path:
            return
        atomic_write_text(Path(record.meta_path), json.dumps(self._record_to_meta(record), indent=2) + "\n")

    def _meta_lock_path(self, path: Path) -> Path:
        return path.parent / "meta.lock"

    def _read_record(self, path: Path) -> ExecutionRecord:
        with file_lock(self._meta_lock_path(path)):
            payload = json.loads(path.read_text(encoding="utf-8"))
        return ExecutionRecord(**payload)

    def _write_record(self, path: Path, record: ExecutionRecord) -> None:
        with file_lock(self._meta_lock_path(path)):
            if not self._run_exists(record.run_id):
                try:
                    self.run_store.create_run(
                        RunSubmission(
                            run_id=record.run_id,
                            task_id=record.task_id,
                            backend_type=self.__class__.__name__.replace("Backend", "").lower(),
                            worktree_path=record.cwd,
                            pid=record.worker_pid,
                            meta_json=asdict(record),
                        )
                    )
                except ValueError:
                    pass
            self.run_store.update_status(
                record.run_id,
                record.status,
                reason=record.failure_reason,
                updated_fields={
                    "task_id": record.task_id,
                    "pid": record.worker_pid,
                    "exit_code": record.exit_code,
                    "meta_json": asdict(record),
                },
            )
            atomic_write_text(path, json.dumps(asdict(record), indent=2) + "\n")

    def _run_exists(self, run_id: str) -> bool:
        try:
            self.run_store.get_run(run_id)
            return True
        except FileNotFoundError:
            return False

    def _to_record(self, run: Any) -> ExecutionRecord:
        meta = run.meta_json or {}
        return ExecutionRecord(
            run_id=run.run_id,
            task_id=run.task_id or meta.get("task_id", ""),
            status=run.status,
            command=list(meta.get("command", [])),
            cwd=meta.get("cwd", run.worktree_path),
            stdout_log=meta.get("stdout_log", ""),
            stderr_log=meta.get("stderr_log", ""),
            meta_path=meta.get("meta_path", str(self.runs_root / run.run_id / "meta.json")),
            lock_path=meta.get("lock_path", ""),
            created_at=run.created_at,
            started_at=meta.get("started_at"),
            ended_at=meta.get("ended_at"),
            exit_code=run.exit_code,
            worker_pid=run.pid,
            notes_enforced=bool(meta.get("notes_enforced", False)),
            failure_reason=run.failure_reason,
            heartbeat_at=run.heartbeat_at,
        )

    def _persist_record(self, record: ExecutionRecord, status: str | None = None, reason: str | None = None) -> ExecutionRecord:
        meta = self._record_to_meta(record)
        updated_fields = {
            "task_id": record.task_id,
            "pid": record.worker_pid,
            "exit_code": record.exit_code,
            "meta_json": meta,
        }
        target_status = status or record.status
        run = self.run_store.update_status(record.run_id, target_status, reason=reason, updated_fields=updated_fields)
        refreshed = self._to_record(run)
        self._write_meta(refreshed)
        return refreshed

    def submit(self, request: ExecutionRequest) -> str:
        request.stdout_log.parent.mkdir(parents=True, exist_ok=True)
        request.stderr_log.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "task_id": request.task_id,
            "command": request.command,
            "cwd": str(request.cwd),
            "stdout_log": str(request.stdout_log),
            "stderr_log": str(request.stderr_log),
            "meta_path": str(request.meta_path),
            "lock_path": str(request.lock_path),
            "started_at": None,
            "ended_at": None,
            "notes_enforced": False,
        }
        self.run_store.create_run(
            RunSubmission(
                run_id=request.run_id,
                task_id=request.task_id,
                backend_type=self.__class__.__name__.replace("Backend", "").lower(),
                worktree_path=str(request.cwd),
                meta_json=meta,
            )
        )
        self._append_event(request.run_id, "started", {"record": meta})

        worker_command = [
            sys.executable,
            "-m",
            "overseer",
            "execution-worker",
            "--run-id",
            request.run_id,
            "--codex-root",
            str(self.codex_root),
        ]
        env = os.environ.copy()
        env.setdefault("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
        process = subprocess.Popen(worker_command, cwd=request.cwd, env=env, start_new_session=True)  # noqa: S603
        run = self.run_store.update_status(request.run_id, "queued", updated_fields={"pid": process.pid})
        self._append_event(request.run_id, "status_change", {"worker_pid": process.pid})
        self._write_meta(self._to_record(run))
        return request.run_id

    def _hydrate_from_meta_if_needed(self, run_id: str) -> None:
        if self._run_exists(run_id):
            return
        meta_path = self.runs_root / run_id / "meta.json"
        if not meta_path.exists():
            return
        record = self._read_record(meta_path)
        self._write_record(meta_path, record)

    def status(self, run_id: str) -> ExecutionRecord:
        self._hydrate_from_meta_if_needed(run_id)
        record = self._to_record(self.run_store.get_run(run_id))
        record = self._enforce_required_notes(record)
        self._write_meta(record)
        return record

    def list_runs(self) -> list[ExecutionRecord]:
        return [self._enforce_required_notes(self._to_record(run)) for run in self.run_store.list_runs()]

    def cancel(self, run_id: str) -> ExecutionRecord:
        self._hydrate_from_meta_if_needed(run_id)
        record = self._to_record(self.run_store.get_run(run_id))
        if record.status in TERMINAL_STATUSES:
            return record
        self._append_event(run_id, "cancel_requested", {"requested_at": datetime.now(timezone.utc).isoformat()})
        self.run_store.update_status(run_id, "canceling")
        if record.worker_pid is not None:
            try:
                os.kill(record.worker_pid, signal.SIGTERM)
            except OSError:
                pass
        if record.status == "queued":
            record.status = "canceled"
            record.ended_at = datetime.now(timezone.utc).isoformat()
            self._append_event(run_id, "canceled", {"ended_at": record.ended_at})
            return self._persist_record(record, status="canceled")
        record.status = "canceling"
        return self._persist_record(record, status="canceling")

    def run_worker(self, run_id: str | Path) -> int:
        run_id = self._normalize_run_id(run_id)
        record = self._to_record(self.run_store.get_run(run_id))
        if record.status in {"canceling", "canceled"}:
            if record.status == "canceling":
                self.cancel(run_id)
            return 1

        record.status = "running"
        record.started_at = datetime.now(timezone.utc).isoformat()
        record.worker_pid = os.getpid()
        record = self._persist_record(record, status="running")
        self.run_store.heartbeat(run_id)
        self._append_event(run_id, "status_change", {"status": "running", "started_at": record.started_at})

        with file_lock(Path(record.lock_path)):
            with (
                Path(record.stdout_log).open("w", encoding="utf-8") as stdout_handle,
                Path(record.stderr_log).open("w", encoding="utf-8") as stderr_handle,
            ):
                process = subprocess.Popen(  # noqa: S603
                    record.command,
                    cwd=record.cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )

                def stream_output(stream: Any, handle: Any, event_type: str) -> None:
                    if stream is None:
                        return
                    for chunk in iter(stream.readline, ""):
                        handle.write(chunk)
                        handle.flush()
                        self._append_event(run_id, event_type, {"chunk": chunk})
                    stream.close()

                stdout_thread = threading.Thread(target=stream_output, args=(process.stdout, stdout_handle, "stdout"), daemon=True)
                stderr_thread = threading.Thread(target=stream_output, args=(process.stderr, stderr_handle, "stderr"), daemon=True)
                stdout_thread.start()
                stderr_thread.start()

                while process.poll() is None:
                    refreshed = self._to_record(self.run_store.get_run(run_id))
                    if refreshed.status in {"canceling", "canceled"}:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
                        break
                    self.run_store.heartbeat(run_id)
                    time.sleep(0.5)

                result_exit_code = process.wait()
                stdout_thread.join(timeout=2)
                stderr_thread.join(timeout=2)

        self._write_required_notes(record)

        refreshed = self._to_record(self.run_store.get_run(run_id))
        if refreshed.status == "canceling":
            return self.cancel(run_id).exit_code or 1
        if refreshed.status not in TERMINAL_STATUSES:
            refreshed.ended_at = datetime.now(timezone.utc).isoformat()
            refreshed.exit_code = result_exit_code
            refreshed.status = "done" if result_exit_code == 0 else "failed"
            reason = None if refreshed.status == "done" else "process_failed"
            self._append_event(
                run_id,
                "completed",
                {"ended_at": refreshed.ended_at, "exit_code": refreshed.exit_code, "status": refreshed.status},
            )
            self._persist_record(refreshed, status=refreshed.status, reason=reason)
        return result_exit_code

    def reconcile(self, stale_after_seconds: int) -> list[ExecutionRecord]:
        now = datetime.now(timezone.utc)
        reconciled: list[ExecutionRecord] = []
        for run in self.run_store.list_runs(filters={"status": "running"}):
            if not run.heartbeat_at:
                continue
            heartbeat_at = datetime.fromisoformat(run.heartbeat_at)
            if (now - heartbeat_at).total_seconds() <= stale_after_seconds:
                continue
            record = self._to_record(run)
            record.status = "failed"
            record.ended_at = now.isoformat()
            record.failure_reason = "worker_lost"
            self._append_event(record.run_id, "reconciled", {"from": "running", "to": "failed", "reason": "worker_lost"})
            reconciled.append(self._persist_record(record, status="failed", reason="worker_lost"))
        return reconciled

    def _write_required_notes(self, record: ExecutionRecord) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        run_notes = self.runs_root / record.run_id / "notes.md"
        run_notes.parent.mkdir(parents=True, exist_ok=True)
        with run_notes.open("a", encoding="utf-8") as handle:
            handle.write(f"- [{timestamp}] role={self.worker_role} run={record.run_id} task={record.task_id}\n")

        worker_notes = self.codex_root / "11_WORKERS" / self.worker_role / "NOTES.md"
        worker_notes.parent.mkdir(parents=True, exist_ok=True)
        with worker_notes.open("a", encoding="utf-8") as handle:
            handle.write(f"- [{timestamp}] run={record.run_id} task={record.task_id}\n")

    def _enforce_required_notes(self, record: ExecutionRecord) -> ExecutionRecord:
        if record.status not in REQUIRES_NOTES_STATUSES or record.notes_enforced:
            return record

        run_notes = self.runs_root / record.run_id / "notes.md"
        worker_notes = self.codex_root / "11_WORKERS" / self.worker_role / "NOTES.md"
        has_run_notes = run_notes.exists() and bool(run_notes.read_text(encoding="utf-8").strip())
        has_worker_notes = worker_notes.exists() and record.run_id in worker_notes.read_text(encoding="utf-8")

        if has_run_notes and has_worker_notes:
            record.notes_enforced = True
            return self._persist_record(record)

        record.status = "failed"
        record.notes_enforced = True
        if record.exit_code is None:
            record.exit_code = 1
        if record.ended_at is None:
            record.ended_at = datetime.now(timezone.utc).isoformat()
        self._append_event(record.run_id, "escalated", {"reason": "missing required notes"})
        saved = self._persist_record(record, status="failed", reason="missing required notes")
        if self.human_api is not None:
            self.human_api.append_request({"id": record.task_id}, "missing required notes", run_id=record.run_id)
        return saved


class CeleryBackend(LocalBackend):
    def __init__(
        self,
        codex_root: Path,
        human_api: HumanAPI | None = None,
        worker_role: str = "builder",
        run_store: RunStore | None = None,
        celery_app: Any | None = None,
        task_name: str = "overseer.execution.celery_worker.execute_run",
    ) -> None:
        super().__init__(codex_root=codex_root, human_api=human_api, worker_role=worker_role, run_store=run_store)
        if celery_app is None:
            from overseer.execution.celery_app import build_celery_app

            self.celery_app = build_celery_app()
        else:
            self.celery_app = celery_app
        self.task_name = task_name

    def submit(self, request: ExecutionRequest) -> str:
        request.stdout_log.parent.mkdir(parents=True, exist_ok=True)
        request.stderr_log.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "task_id": request.task_id,
            "command": request.command,
            "cwd": str(request.cwd),
            "stdout_log": str(request.stdout_log),
            "stderr_log": str(request.stderr_log),
            "meta_path": str(request.meta_path),
            "lock_path": str(request.lock_path),
            "started_at": None,
            "ended_at": None,
            "notes_enforced": False,
        }
        self.run_store.create_run(
            RunSubmission(
                run_id=request.run_id,
                task_id=request.task_id,
                backend_type="celery",
                worktree_path=str(request.cwd),
                meta_json=meta,
            )
        )
        self._append_event(request.run_id, "started", {"record": meta})
        async_result = self.celery_app.send_task(
            self.task_name,
            args=[request.run_id, str(self.codex_root)],
        )
        self._append_event(request.run_id, "worker_dispatched", {"celery_task_id": getattr(async_result, "id", None)})
        self._write_meta(self._to_record(self.run_store.get_run(request.run_id)))
        return request.run_id

    def cancel(self, run_id: str) -> ExecutionRecord:
        record = super().cancel(run_id)
        task_id = None
        if isinstance(self.run_store, SQLiteRunStore):
            for event in reversed(self.run_store.list_events(run_id)):
                if event.type == "worker_dispatched":
                    task_id = event.payload.get("celery_task_id")
                    if task_id:
                        break
        if task_id:
            self.celery_app.control.revoke(task_id, terminate=True)
        return record
