from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from overseer.codex_store import CodexStore
from overseer.daemon_api import OverseerDaemon, _resolve_queue
from overseer.execution.backend import ExecutionRecord, LocalBackend
from overseer.human_api import HumanAPI
from overseer.integrators.codex import CodexIntegrator


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _setup_daemon(tmp_path: Path) -> tuple[OverseerDaemon, HumanAPI, LocalBackend]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Overseer Tests")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    (repo / "codex").mkdir(parents=True)

    store = CodexStore(repo)
    store.init_structure()
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "seed")

    human_api = HumanAPI(store)
    backend = LocalBackend(store.codex_root, human_api=human_api)
    integrator = CodexIntegrator(store.repo_root, human_api=human_api, backend=backend)
    daemon = OverseerDaemon(backend=backend, integrator=integrator, human_api=human_api, poll_interval_s=0.05)
    return daemon, human_api, backend




def _write_human_schema(human_api: HumanAPI) -> None:
    human_api.schema_file.write_text(
        (
            "# Human Request Schema (strict)\n\n"
            "HUMAN_REQUEST:\n"
            "TYPE: {design_direction | decision | external_action | clarification | review}\n"
            "URGENCY: {low | medium | high | interrupt_now}\n"
            "TIME_REQUIRED_MIN: <int>\n"
            "CONTEXT: <short>\n"
            "OPTIONS:\n"
            "  - <option A>\n"
            "  - <option B>\n"
            "RECOMMENDATION: <one of options or custom>\n"
            "WHY:\n"
            "  - <1-3 bullets>\n"
            "UNBLOCKS: <what changes after you answer>\n"
            "REPLY_FORMAT: <exact expected reply>\n"
        ),
        encoding="utf-8",
    )


def _write_queued_run(backend: LocalBackend, run_id: str) -> None:
    run_dir = backend.runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_path = run_dir / "meta.json"
    record = ExecutionRecord(
        run_id=run_id,
        task_id="task-123456789abc",
        status="queued",
        command=[sys.executable, "-c", "print('ok')"],
        cwd=str(run_dir),
        stdout_log=str(run_dir / "stdout.log"),
        stderr_log=str(run_dir / "stderr.log"),
        meta_path=str(meta_path),
        lock_path=str(run_dir / "run.lock"),
        created_at="2020-01-01T00:00:00Z",
    )
    backend._write_record(meta_path, record)
    (run_dir / "events.jsonl").write_text(
        json.dumps({"type": "started", "at": record.created_at, "payload": {"record": record.__dict__}})
        + "\n",
        encoding="utf-8",
    )


def test_daemon_poll_refreshes_runs_snapshot(tmp_path: Path) -> None:
    daemon, _, backend = _setup_daemon(tmp_path)
    try:
        daemon.start()
        _write_queued_run(backend, run_id="run-runtime-1234")

        deadline = time.time() + 3
        while time.time() < deadline:
            runs = daemon.runs()
            if any(run["run_id"] == "run-runtime-1234" for run in runs):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("daemon did not refresh run snapshot")
    finally:
        daemon.stop()


def test_resolve_queue_helper_writes_resolution(tmp_path: Path) -> None:
    daemon, human_api, _ = _setup_daemon(tmp_path)
    _write_human_schema(human_api)
    human_api.append_request({"id": "task-xyz"}, "Need approval")
    request = human_api.list_requests()[0]

    resolution_path = _resolve_queue(
        daemon,
        request_id=request.request_id,
        choice=request.options[0],
        rationale="approved",
    )
    assert resolution_path.exists()
    assert request.request_id in resolution_path.read_text(encoding="utf-8")
