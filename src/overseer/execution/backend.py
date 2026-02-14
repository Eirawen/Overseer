from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from overseer.fs import atomic_write_text, test_delay_meta_after_read
from overseer.human_api import HumanAPI
from overseer.locks import file_lock

RunStatus = Literal["queued", "running", "done", "failed", "canceled"]

TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "failed", "canceled"})


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
        with file_lock(self._meta_lock_path(request.meta_path)):
            self._write_record(request.meta_path, record)

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
        record.worker_pid = process.pid
        with file_lock(self._meta_lock_path(request.meta_path)):
            self._write_record(request.meta_path, record)
        return request.run_id

    def status(self, run_id: str) -> ExecutionRecord:
        meta_path = self.runs_root / run_id / "meta.json"
        with file_lock(self._meta_lock_path(meta_path)):
            record = self._read_record(meta_path)
            return self._enforce_required_notes(record, meta_path)

    def list_runs(self) -> list[ExecutionRecord]:
        records: list[ExecutionRecord] = []
        for meta in sorted(self.runs_root.glob("*/meta.json")):
            with file_lock(self._meta_lock_path(meta)):
                record = self._read_record(meta)
                records.append(self._enforce_required_notes(record, meta))
        return records

    def cancel(self, run_id: str) -> ExecutionRecord:
        meta_path = self.runs_root / run_id / "meta.json"
        with file_lock(self._meta_lock_path(meta_path)):
            record = self._read_record(meta_path)
            test_delay_meta_after_read()
            if record.status in TERMINAL_STATUSES:
                return record
            if record.worker_pid is not None:
                try:
                    os.kill(record.worker_pid, signal.SIGTERM)
                except OSError:
                    pass
            record.status = "canceled"
            record.ended_at = datetime.now(timezone.utc).isoformat()
            self._write_record(meta_path, record)
            return record

    def run_worker(self, meta_path: Path) -> int:
        meta_path = Path(meta_path)
        with file_lock(self._meta_lock_path(meta_path)):
            record = self._read_record(meta_path)
            test_delay_meta_after_read()
            if record.status == "canceled":
                return 1
            record.status = "running"
            record.started_at = datetime.now(timezone.utc).isoformat()
            self._write_record(meta_path, record)

        with file_lock(Path(record.lock_path)):
            with (
                Path(record.stdout_log).open("w", encoding="utf-8") as stdout_handle,
                Path(record.stderr_log).open("w", encoding="utf-8") as stderr_handle,
            ):
                result = subprocess.run(  # noqa: S603
                    record.command,
                    cwd=record.cwd,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    text=True,
                    check=False,
                )

        self._write_required_notes(record)

        with file_lock(self._meta_lock_path(meta_path)):
            record = self._read_record(meta_path)
            test_delay_meta_after_read()
            record.ended_at = datetime.now(timezone.utc).isoformat()
            record.exit_code = result.returncode
            if record.status not in TERMINAL_STATUSES:
                record.status = "done" if result.returncode == 0 else "failed"
            self._write_record(meta_path, record)
        return result.returncode

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
        if record.status not in TERMINAL_STATUSES or record.notes_enforced:
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
        self._write_record(meta_path, record)
        if self.human_api is not None:
            self.human_api.append_request({"id": record.task_id}, "missing required notes")
        return record

    def _write_record(self, path: Path, record: ExecutionRecord) -> None:
        text = json.dumps(asdict(record), indent=2) + "\n"
        atomic_write_text(path, text)

    def _read_record(self, path: Path) -> ExecutionRecord:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ExecutionRecord(**payload)
