from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from overseer.execution.backend import ExecutionRequest, LocalBackend
from overseer.execution.run_store import RunSubmission


def test_new_run_id_format() -> None:
    run_id = LocalBackend.new_run_id()
    assert run_id.startswith("run-")
    assert len(run_id) == 16


def test_list_runs_empty(tmp_path: Path) -> None:
    backend = LocalBackend(tmp_path / "codex")
    assert backend.list_runs() == []


def test_submit_creates_meta_and_returns_run_id(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
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
    meta = json.loads((run_root / "meta.json").read_text(encoding="utf-8"))
    assert meta["run_id"] == run_id
    assert meta["status"] == "queued"
    assert "worker_pid" in meta


def test_cancel_queued_run_stays_canceled_on_status(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    backend = LocalBackend(codex_root)
    run_id = "run-cancel-queued"
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
    backend.run_store.create_run(
        RunSubmission(
            run_id=run_id,
            task_id="task-1",
            backend_type="local",
            worktree_path=str(tmp_path),
            meta_json={
                "task_id": "task-1",
                "command": req.command,
                "cwd": str(req.cwd),
                "stdout_log": str(req.stdout_log),
                "stderr_log": str(req.stderr_log),
                "meta_path": str(req.meta_path),
                "lock_path": str(req.lock_path),
            },
        )
    )

    canceled = backend.cancel(run_id)
    assert canceled.status == "canceled"
    assert backend.status(run_id).status == "canceled"


def test_cancel_running_in_process_worker_does_not_sigterm_current_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_root = tmp_path / "codex"
    backend = LocalBackend(codex_root)
    run_id = "run-inproc-worker"
    run_root = codex_root / "08_TELEMETRY" / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
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
    meta = {
        "task_id": "task-1",
        "command": req.command,
        "cwd": str(req.cwd),
        "stdout_log": str(req.stdout_log),
        "stderr_log": str(req.stderr_log),
        "meta_path": str(req.meta_path),
        "lock_path": str(req.lock_path),
    }
    backend.run_store.create_run(
        RunSubmission(
            run_id=run_id,
            task_id="task-1",
            backend_type="local",
            worktree_path=str(tmp_path),
            meta_json=meta,
        )
    )
    backend.run_store.update_status(run_id, "running", updated_fields={"pid": os.getpid(), "meta_json": meta})

    calls: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))

    monkeypatch.setattr("overseer.execution.backend.os.kill", fake_kill)

    canceled = backend.cancel(run_id)
    assert canceled.status == "canceling"
    assert calls == []
