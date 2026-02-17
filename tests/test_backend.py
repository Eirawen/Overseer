"""Unit tests for execution backend: new_run_id, list_runs, cancel, status."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path


from overseer.execution.backend import (
    ExecutionRecord,
    ExecutionRequest,
    LocalBackend,
)
from overseer.locks import file_lock


def test_new_run_id_format() -> None:
    run_id = LocalBackend.new_run_id()
    assert run_id.startswith("run-")
    assert len(run_id) == 16  # run- (4) + 12 hex
    assert run_id[4:].isalnum()
    run_id2 = LocalBackend.new_run_id()
    assert run_id != run_id2


def test_list_runs_empty(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    (codex_root / "08_TELEMETRY" / "runs").mkdir(parents=True)
    backend = LocalBackend(codex_root)
    assert backend.list_runs() == []


def test_status_returns_record(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    run_id = "run-test123"
    run_dir = codex_root / "08_TELEMETRY" / "runs" / run_id
    run_dir.mkdir(parents=True)
    meta = run_dir / "meta.json"
    record = ExecutionRecord(
        run_id=run_id,
        task_id="task-1",
        status="done",
        command=[],
        cwd=str(tmp_path),
        stdout_log="",
        stderr_log="",
        meta_path=str(meta),
        lock_path=str(tmp_path / "locks" / "x.lock"),
        created_at="2020-01-01T00:00:00Z",
        ended_at="2020-01-01T00:01:00Z",
        exit_code=0,
    )
    meta.write_text(json.dumps(asdict(record), indent=2) + "\n", encoding="utf-8")
    (run_dir / "notes.md").write_text("- existing note\n", encoding="utf-8")
    worker_notes = codex_root / "11_WORKERS" / "builder" / "NOTES.md"
    worker_notes.parent.mkdir(parents=True, exist_ok=True)
    worker_notes.write_text(f"- run={run_id}\n", encoding="utf-8")
    backend = LocalBackend(codex_root)
    got = backend.status(run_id)
    assert got.run_id == run_id
    assert got.status == "done"
    assert got.exit_code == 0


def test_cancel_already_done_returns_unchanged(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    run_id = "run-done"
    run_dir = codex_root / "08_TELEMETRY" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (codex_root / "08_TELEMETRY" / "runs" / run_id / "meta.lock").parent.mkdir(parents=True, exist_ok=True)
    meta = run_dir / "meta.json"
    record = ExecutionRecord(
        run_id=run_id,
        task_id="task-1",
        status="done",
        command=[],
        cwd=str(tmp_path),
        stdout_log="",
        stderr_log="",
        meta_path=str(meta),
        lock_path=str(tmp_path / "locks" / "x.lock"),
        created_at="2020-01-01T00:00:00Z",
        ended_at="2020-01-01T00:01:00Z",
        exit_code=0,
    )
    backend = LocalBackend(codex_root)
    backend._write_record(meta, record)
    rec = backend.cancel(run_id)
    assert rec.status == "done"
    assert json.loads(meta.read_text(encoding="utf-8"))["status"] == "done"


def test_submit_creates_meta_and_returns_run_id(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    (codex_root / "10_OVERSEER" / "locks").mkdir(parents=True, exist_ok=True)
    run_id = LocalBackend.new_run_id()
    run_root = codex_root / "08_TELEMETRY" / "runs" / run_id
    req = ExecutionRequest(
        run_id=run_id,
        task_id="task-1",
        command=[sys.executable, "-c", "pass"],
        cwd=tmp_path,
        stdout_log=run_root / "stdout.log",
        stderr_log=run_root / "stderr.log",
        meta_path=run_root / "meta.json",
        lock_path=codex_root / "10_OVERSEER" / "locks" / f"{run_id}.lock",
    )
    backend = LocalBackend(codex_root)
    out = backend.submit(req)
    assert out == run_id
    assert run_root.exists()
    meta = json.loads((run_root / "meta.json").read_text(encoding="utf-8"))
    assert meta["run_id"] == run_id
    assert meta["status"] == "queued"
    assert "worker_pid" in meta


def test_cancel_records_requested_canceling_canceled_events(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    backend = LocalBackend(codex_root)
    run_id = "run-cancel-seq"
    run_dir = codex_root / "08_TELEMETRY" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = run_dir / "meta.json"
    record = ExecutionRecord(
        run_id=run_id,
        task_id="task-1",
        status="queued",
        command=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        stdout_log=str(run_dir / "stdout.log"),
        stderr_log=str(run_dir / "stderr.log"),
        meta_path=str(meta),
        lock_path=str(tmp_path / "locks" / "x.lock"),
        created_at="2020-01-01T00:00:00Z",
    )
    with file_lock(backend._events_lock_path(meta)):
        backend._append_event(meta, "started", {"record": asdict(record)})

    backend.cancel(run_id)

    events = [
        json.loads(line)["type"]
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert events == ["started", "cancel_requested", "status_change", "canceled"]
    assert json.loads(meta.read_text(encoding="utf-8"))["status"] == "canceled"


def test_cancel_queued_run_stays_canceled_on_status(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    backend = LocalBackend(codex_root)
    run_id = "run-cancel-queued"
    run_dir = codex_root / "08_TELEMETRY" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = run_dir / "meta.json"
    record = ExecutionRecord(
        run_id=run_id,
        task_id="task-1",
        status="queued",
        command=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        stdout_log=str(run_dir / "stdout.log"),
        stderr_log=str(run_dir / "stderr.log"),
        meta_path=str(meta),
        lock_path=str(tmp_path / "locks" / "x.lock"),
        created_at="2020-01-01T00:00:00Z",
    )
    with file_lock(backend._events_lock_path(meta)):
        backend._append_event(meta, "started", {"record": asdict(record)})

    canceled = backend.cancel(run_id)
    assert canceled.status == "canceled"

    got = backend.status(run_id)
    assert got.status == "canceled"
