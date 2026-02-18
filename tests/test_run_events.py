from __future__ import annotations

import json
from pathlib import Path

from overseer.execution.backend import LocalBackend
from overseer.execution.run_store import RunSubmission


def test_event_append_written_as_jsonl(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    backend = LocalBackend(codex_root)
    run_id = "run-evt-1"
    backend.run_store.create_run(
        RunSubmission(
            run_id=run_id,
            task_id="task-1",
            backend_type="local",
            worktree_path=str(tmp_path),
            meta_json={"task_id": "task-1", "command": [], "cwd": str(tmp_path), "stdout_log": "", "stderr_log": "", "meta_path": str(codex_root / "08_TELEMETRY" / "runs" / run_id / "meta.json"), "lock_path": ""},
        )
    )

    backend._append_event(run_id, "stdout", {"chunk": "hello"})

    line = (codex_root / "08_TELEMETRY" / "runs" / run_id / "events.jsonl").read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["type"] == "stdout"
    assert payload["payload"]["chunk"] == "hello"
