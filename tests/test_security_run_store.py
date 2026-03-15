from __future__ import annotations

from pathlib import Path
from overseer.execution.run_store import SQLiteRunStore, RunSubmission

def test_list_runs_filter_allowlist(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    store = SQLiteRunStore(codex_root)

    # Create a test run
    run_id = "run-1"
    store.create_run(
        RunSubmission(
            run_id=run_id,
            task_id="task-1",
            backend_type="local",
            worktree_path=str(tmp_path),
        )
    )

    # 1. Test valid filter
    runs = store.list_runs(filters={"status": "queued"})
    assert len(runs) == 1
    assert runs[0].run_id == run_id

    # 2. Test multiple valid filters
    runs = store.list_runs(filters={"status": "queued", "task_id": "task-1"})
    assert len(runs) == 1
    assert runs[0].run_id == run_id

    # 3. Test invalid filter key (should be ignored)
    # If not ignored, it might cause a sqlite3.OperationalError: no such column
    runs = store.list_runs(filters={"invalid_column": "some_value"})
    assert len(runs) == 1 # Returns all runs since no valid filter was applied

    # 4. Test malicious filter key (SQL injection attempt)
    # If not ignored/safe, this would break the query or return wrong data
    malicious_key = "status = 'queued' OR 1=1; --"
    runs = store.list_runs(filters={malicious_key: "value"})
    assert len(runs) == 1 # Should ignore the malicious key and return all runs

    # 5. Test valid and invalid mixed
    runs = store.list_runs(filters={"status": "queued", "malicious": "value"})
    assert len(runs) == 1
    assert runs[0].run_id == run_id
