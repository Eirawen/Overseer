from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from overseer.execution.backend import ExecutionRequest, LocalBackend


def test_local_backend_status_transitions_and_logs(tmp_path: Path) -> None:
    codex_root = tmp_path / "repo" / "codex"
    backend = LocalBackend(codex_root)
    run_id = LocalBackend.new_run_id()
    run_root = codex_root / "08_TELEMETRY" / "runs" / run_id

    request = ExecutionRequest(
        run_id=run_id,
        task_id="task-1",
        command=[sys.executable, "-c", "import sys; print('hello'); print('err', file=sys.stderr)"],
        cwd=tmp_path,
        stdout_log=run_root / "stdout.log",
        stderr_log=run_root / "stderr.log",
        meta_path=run_root / "meta.json",
        lock_path=codex_root / "10_OVERSEER" / "locks" / "task-1.lock",
    )

    backend.submit(request)
    first = backend.status(run_id)
    assert first.status in {"queued", "running"}

    deadline = time.time() + 10
    while time.time() < deadline:
        current = backend.status(run_id)
        if current.status in {"done", "failed"}:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("run did not complete")

    final = backend.status(run_id)
    assert final.status == "done"
    assert "hello" in (run_root / "stdout.log").read_text(encoding="utf-8")
    assert "err" in (run_root / "stderr.log").read_text(encoding="utf-8")
    meta = json.loads((run_root / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "done"
