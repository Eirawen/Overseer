from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from overseer.execution.backend import ExecutionRecord, LocalBackend
from overseer.locks import file_lock


def _seed_started_event(backend: LocalBackend, meta_path: Path) -> ExecutionRecord:
    record = ExecutionRecord(
        run_id="run-evt-1",
        task_id="task-evt-1",
        status="queued",
        command=["python", "-c", "print('ok')"],
        cwd=str(meta_path.parent),
        stdout_log=str(meta_path.parent / "stdout.log"),
        stderr_log=str(meta_path.parent / "stderr.log"),
        meta_path=str(meta_path),
        lock_path=str(meta_path.parent / "run.lock"),
        created_at="2020-01-01T00:00:00+00:00",
    )
    with file_lock(backend._events_lock_path(meta_path)):
        backend._append_event(meta_path, "started", {"record": record.__dict__})
    return record


def test_event_append_and_reduce(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    backend = LocalBackend(codex_root)
    meta_path = codex_root / "08_TELEMETRY" / "runs" / "run-evt-1" / "meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)

    _seed_started_event(backend, meta_path)
    with file_lock(backend._events_lock_path(meta_path)):
        backend._append_event(meta_path, "status_change", {"status": "running", "started_at": "t1"})
        backend._append_event(meta_path, "completed", {"status": "done", "exit_code": 0, "ended_at": "t2"})

    with file_lock(backend._events_lock_path(meta_path)):
        record = backend._derive_record(meta_path)
    assert record.status == "done"
    assert record.started_at == "t1"
    assert record.ended_at == "t2"
    assert record.exit_code == 0


def test_reducer_rebuilds_meta_after_restart(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    backend = LocalBackend(codex_root)
    meta_path = codex_root / "08_TELEMETRY" / "runs" / "run-evt-1" / "meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)

    _seed_started_event(backend, meta_path)
    with file_lock(backend._events_lock_path(meta_path)):
        backend._append_event(meta_path, "completed", {"status": "failed", "exit_code": 2, "ended_at": "t3"})

    meta_path.unlink(missing_ok=True)
    restarted_backend = LocalBackend(codex_root)
    record = restarted_backend.status("run-evt-1")

    assert record.status == "failed"
    assert record.exit_code == 2
    rebuilt = json.loads(meta_path.read_text(encoding="utf-8"))
    assert rebuilt["status"] == "failed"
    assert rebuilt["exit_code"] == 2


def test_concurrent_event_appends_are_valid_jsonl(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    backend = LocalBackend(codex_root)
    meta_path = codex_root / "08_TELEMETRY" / "runs" / "run-evt-1" / "meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_started_event(backend, meta_path)

    append_count = 50

    def append_one(i: int) -> None:
        with file_lock(backend._events_lock_path(meta_path)):
            backend._append_event(meta_path, "stdout", {"chunk": f"line-{i}"})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append_one, range(append_count)))

    events_path = meta_path.parent / "events.jsonl"
    lines = [line for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == append_count + 1
    for line in lines:
        payload = json.loads(line)
        assert "type" in payload
        assert "at" in payload
        assert isinstance(payload["payload"], dict)
