from __future__ import annotations

from pathlib import Path
import sqlite3
import pytest
from overseer.execution.run_store import RunSubmission, SQLiteRunStore

@pytest.fixture
def store(tmp_path: Path) -> SQLiteRunStore:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    return SQLiteRunStore(codex_root)

def test_update_status_sql_injection_prevention(store: SQLiteRunStore, tmp_path: Path) -> None:
    run_id = "test-run"
    store.create_run(
        RunSubmission(
            run_id=run_id,
            task_id="task-1",
            backend_type="local",
            worktree_path=str(tmp_path),
        )
    )

    # Attempt SQL injection through a key in updated_fields
    # If the fix is working, this key will not be in allowed_fields and will be ignored.
    store.update_status(run_id, "running", updated_fields={"status = 'injected' --": "value"})

    # Verify the status was NOT changed to 'injected'
    run = store.get_run(run_id)
    assert run.status == "running"
    assert run.status != "injected"

def test_list_runs_sql_injection_prevention(store: SQLiteRunStore, tmp_path: Path) -> None:
    run_id = "test-run"
    store.create_run(
        RunSubmission(
            run_id=run_id,
            task_id="task-1",
            backend_type="local",
            worktree_path=str(tmp_path),
        )
    )

    # Attempt SQL injection through a key in filters
    # If the fix is working, this key will not be in allowed_filters and will be ignored.
    # We use a filter that would cause an error if it were interpolated directly.
    filters = {"1=1 OR status='queued'": "value"}
    runs = store.list_runs(filters=filters)

    # Since the filter is ignored, it should return all runs (in this case, 1)
    # If it was NOT ignored and was vulnerable, it might return runs based on the injected condition.
    # More importantly, if it was directly interpolated as `WHERE 1=1 OR status='queued' = ?`, it might fail or behave unexpectedly.
    assert len(runs) == 1
    assert runs[0].run_id == run_id

def test_list_runs_allowed_filters(store: SQLiteRunStore, tmp_path: Path) -> None:
    run_id = "test-run"
    store.create_run(
        RunSubmission(
            run_id=run_id,
            task_id="task-1",
            backend_type="local",
            worktree_path=str(tmp_path),
        )
    )

    # Test that allowed filters still work
    runs = store.list_runs(filters={"status": "queued"})
    assert len(runs) == 1

    runs = store.list_runs(filters={"status": "running"})
    assert len(runs) == 0

    runs = store.list_runs(filters={"task_id": "task-1"})
    assert len(runs) == 1

    runs = store.list_runs(filters={"run_id": "test-run"})
    assert len(runs) == 1
