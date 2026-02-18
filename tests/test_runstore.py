from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from overseer.execution import LocalBackend
from overseer.execution.run_store import RunSubmission, SQLiteRunStore


def test_runstore_persists_across_reinit(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    store = SQLiteRunStore(codex_root)
    run_id = "run-persist-1"
    store.create_run(
        RunSubmission(
            run_id=run_id,
            task_id="task-1",
            backend_type="local",
            worktree_path=str(tmp_path),
            meta_json={"meta_path": str(codex_root / "08_TELEMETRY" / "runs" / run_id / "meta.json")},
        )
    )

    reopened = SQLiteRunStore(codex_root)
    run = reopened.get_run(run_id)
    assert run.run_id == run_id
    assert run.status == "queued"


def test_reconcile_stale_running_marks_failed(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    backend = LocalBackend(codex_root)
    run_id = "run-stale-1"
    backend.run_store.create_run(
        RunSubmission(
            run_id=run_id,
            task_id="task-1",
            backend_type="local",
            worktree_path=str(tmp_path),
            meta_json={"task_id": "task-1", "command": [], "cwd": str(tmp_path), "stdout_log": "", "stderr_log": "", "meta_path": str(codex_root / "08_TELEMETRY" / "runs" / run_id / "meta.json"), "lock_path": ""},
        )
    )
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    backend.run_store.update_status(run_id, "running", updated_fields={"heartbeat_at": stale})

    reconciled = backend.reconcile(stale_after_seconds=30)

    assert [r.run_id for r in reconciled] == [run_id]
    run = backend.run_store.get_run(run_id)
    assert run.status == "failed"
    assert run.failure_reason == "worker_lost"


def test_cancel_marks_canceled_and_records_event(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    backend = LocalBackend(codex_root)
    run_id = "run-cancel-1"
    backend.run_store.create_run(
        RunSubmission(
            run_id=run_id,
            task_id="task-1",
            backend_type="local",
            worktree_path=str(tmp_path),
            meta_json={"task_id": "task-1", "command": [], "cwd": str(tmp_path), "stdout_log": "", "stderr_log": "", "meta_path": str(codex_root / "08_TELEMETRY" / "runs" / run_id / "meta.json"), "lock_path": ""},
        )
    )

    canceled = backend.cancel(run_id)

    assert canceled.status == "canceled"
    events_path = codex_root / "08_TELEMETRY" / "runs" / run_id / "events.jsonl"
    events = events_path.read_text(encoding="utf-8")
    assert "cancel_requested" in events
    assert "canceled" in events
