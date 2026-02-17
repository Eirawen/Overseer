from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from overseer.fs import atomic_write_text, test_delay_meta_after_read
from overseer.human_api import HumanAPI
from overseer.locks import file_lock

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


@dataclass(frozen=True)
class RunEvent:
    type: str
    at: str
    payload: dict[str, Any]


class ExecutionBackend(Protocol):
    def submit(self, request: ExecutionRequest) -> str: ...

    def status(self, run_id: str) -> ExecutionRecord: ...

    def list_runs(self) -> list[ExecutionRecord]: ...

    def cancel(self, run_id: str) -> ExecutionRecord: ...


class LocalBackend:
    def __init__(
        self, codex_root: Path, human_api: HumanAPI | None = None, worker_role: str = "builder"
    ) -> None:
        self.codex_root = codex_root
        self.runs_root = codex_root / "08_TELEMETRY" / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.human_api = human_api
        self.worker_role = worker_role

    @staticmethod
    def new_run_id() -> str:
        return f"run-{uuid4().hex[:12]}"

    def _meta_lock_path(self, meta_path: Path) -> Path:
        return meta_path.parent / "meta.lock"

    def _events_path(self, meta_path: Path) -> Path:
        return meta_path.parent / "events.jsonl"

    def _events_lock_path(self, meta_path: Path) -> Path:
        return meta_path.parent / "events.lock"

    def submit(self, request: ExecutionRequest) -> str:
        request.stdout_log.parent.mkdir(parents=True, exist_ok=True)
        request.stderr_log.parent.mkdir(parents=True, exist_ok=True)
        record = ExecutionRecord(
            run_id=request.run_id,
            task_id=request.task_id,
            status="queued",
            command=request.command,
            cwd=str(request.cwd),
            stdout_log=str(request.stdout_log),
            stderr_log=str(request.stderr_log),
            meta_path=str(request.meta_path),
            lock_path=str(request.lock_path),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with file_lock(self._events_lock_path(request.meta_path)):
            self._append_event(
                request.meta_path,
                "started",
                {
                    "record": asdict(record),
                },
            )

        worker_command = [
            sys.executable,
            "-m",
            "overseer",
            "execution-worker",
            "--meta",
            str(request.meta_path),
        ]
        env = os.environ.copy()
        env.setdefault("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
        process = subprocess.Popen(
            worker_command, cwd=request.cwd, env=env, start_new_session=True
        )  # noqa: S603
        with file_lock(self._events_lock_path(request.meta_path)):
            self._append_event(request.meta_path, "status_change", {"worker_pid": process.pid})
        return request.run_id

    def status(self, run_id: str) -> ExecutionRecord:
        meta_path = self.runs_root / run_id / "meta.json"
        with file_lock(self._events_lock_path(meta_path)):
            record = self._derive_record(meta_path)
            return self._enforce_required_notes(record, meta_path)

    def list_runs(self) -> list[ExecutionRecord]:
        records: list[ExecutionRecord] = []
        for meta in sorted(self.runs_root.glob("*/meta.json")):
            with file_lock(self._events_lock_path(meta)):
                record = self._derive_record(meta)
                records.append(self._enforce_required_notes(record, meta))
        return records

    def cancel(self, run_id: str) -> ExecutionRecord:
        meta_path = self.runs_root / run_id / "meta.json"
        with file_lock(self._events_lock_path(meta_path)):
            record = self._derive_record(meta_path)
            test_delay_meta_after_read()
            if record.status in TERMINAL_STATUSES:
                return record
            self._append_event(
                meta_path,
                "cancel_requested",
                {
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            if record.status != "canceling":
                self._append_event(
                    meta_path,
                    "status_change",
                    {
                        "status": "canceling",
                    },
                )
            if record.worker_pid is not None:
                try:
                    os.kill(record.worker_pid, signal.SIGTERM)
                except OSError:
                    pass
            if record.status == "queued":
                self._append_event(
                    meta_path,
                    "canceled",
                    {
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            return self._derive_record(meta_path)

    def run_worker(self, meta_path: Path) -> int:
        meta_path = Path(meta_path)
        with file_lock(self._events_lock_path(meta_path)):
            record = self._derive_record(meta_path)
            test_delay_meta_after_read()
            if record.status in {"canceling", "canceled"}:
                if record.status == "canceling":
                    self._append_event(
                        meta_path,
                        "canceled",
                        {
                            "ended_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                return 1
            self._append_event(
                meta_path,
                "status_change",
                {"status": "running", "started_at": datetime.now(timezone.utc).isoformat()},
            )
            record = self._derive_record(meta_path)

        with file_lock(Path(record.lock_path)):
            with (
                Path(record.stdout_log).open("w", encoding="utf-8") as stdout_handle,
                Path(record.stderr_log).open("w", encoding="utf-8") as stderr_handle,
            ):
                process = subprocess.Popen(  # noqa: S603
                    record.command,
                    cwd=record.cwd,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    text=True,
                )
                while process.poll() is None:
                    with file_lock(self._events_lock_path(meta_path)):
                        refreshed = self._derive_record(meta_path)
                        if refreshed.status in {"canceling", "canceled"}:
                            process.terminate()
                            try:
                                process.wait(timeout=2)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=2)
                            break
                    time.sleep(0.1)
                result_exit_code = process.wait()

        self._write_required_notes(record)

        with file_lock(self._events_lock_path(meta_path)):
            record = self._derive_record(meta_path)
            test_delay_meta_after_read()
            if record.status == "canceling":
                self._append_event(
                    meta_path,
                    "canceled",
                    {
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                record = self._derive_record(meta_path)
            if record.status not in TERMINAL_STATUSES:
                self._append_event(
                    meta_path,
                    "completed",
                    {
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                        "exit_code": result_exit_code,
                        "status": "done" if result_exit_code == 0 else "failed",
                    },
                )
        return result_exit_code

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

    def _enforce_required_notes(self, record: ExecutionRecord, meta_path: Path) -> ExecutionRecord:
        if record.status not in REQUIRES_NOTES_STATUSES or record.notes_enforced:
            return record

        run_notes = self.runs_root / record.run_id / "notes.md"
        worker_notes = self.codex_root / "11_WORKERS" / self.worker_role / "NOTES.md"
        has_run_notes = run_notes.exists() and bool(run_notes.read_text(encoding="utf-8").strip())
        has_worker_notes = worker_notes.exists() and record.run_id in worker_notes.read_text(encoding="utf-8")

        if has_run_notes and has_worker_notes:
            record.notes_enforced = True
            self._write_record(meta_path, record)
            return record

        record.status = "failed"
        record.notes_enforced = True
        if record.exit_code is None:
            record.exit_code = 1
        if record.ended_at is None:
            record.ended_at = datetime.now(timezone.utc).isoformat()
        self._append_event(meta_path, "escalated", {"reason": "missing required notes"})
        self._write_record(meta_path, record)
        if self.human_api is not None:
            self.human_api.append_request({"id": record.task_id}, "missing required notes", run_id=record.run_id)
        return record

    def _write_record(self, path: Path, record: ExecutionRecord) -> None:
        text = json.dumps(asdict(record), indent=2) + "\n"
        atomic_write_text(path, text)

    def _read_record(self, path: Path) -> ExecutionRecord:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ExecutionRecord(**payload)

    def _append_event(self, meta_path: Path, event_type: str, payload: dict[str, Any]) -> None:
        events_path = self._events_path(meta_path)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events = self._read_events(events_path)
        if not events and event_type != "started" and meta_path.exists():
            seed = RunEvent(
                type="started",
                at=datetime.now(timezone.utc).isoformat(),
                payload={"record": asdict(self._read_record(meta_path))},
            )
            with events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(seed), sort_keys=True) + "\n")
            events = [seed]

        event = RunEvent(type=event_type, at=datetime.now(timezone.utc).isoformat(), payload=payload)
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), sort_keys=True) + "\n")
        events.append(event)
        record = self._reduce_events(events)
        self._write_record(meta_path, record)

    def _read_events(self, events_path: Path) -> list[RunEvent]:
        if not events_path.exists():
            return []
        events: list[RunEvent] = []
        with events_path.open(encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                payload = json.loads(text)
                events.append(RunEvent(type=payload["type"], at=payload["at"], payload=payload["payload"]))
        return events

    def _derive_record(self, meta_path: Path) -> ExecutionRecord:
        events_path = self._events_path(meta_path)
        events = self._read_events(events_path)
        if events:
            record = self._reduce_events(events)
            self._write_record(meta_path, record)
            return record
        return self._read_record(meta_path)

    def _reduce_events(self, events: list[RunEvent]) -> ExecutionRecord:
        if not events:
            raise ValueError("cannot reduce empty event stream")
        first = events[0]
        if first.type != "started":
            raise ValueError("first run event must be started")
        record = ExecutionRecord(**first.payload["record"])
        for event in events[1:]:
            payload = event.payload
            if event.type == "status_change":
                if "status" in payload:
                    record.status = payload["status"]
                if "started_at" in payload:
                    record.started_at = payload["started_at"]
                if "worker_pid" in payload:
                    record.worker_pid = payload["worker_pid"]
            elif event.type == "canceled":
                record.status = "canceled"
                record.ended_at = payload.get("ended_at", event.at)
            elif event.type == "cancel_requested":
                continue
            elif event.type == "completed":
                record.status = payload["status"]
                record.ended_at = payload.get("ended_at", event.at)
                record.exit_code = payload.get("exit_code")
            elif event.type == "escalated":
                record.status = "failed"
            elif event.type in {"stdout", "stderr", "summary_written"}:
                continue
        return record
