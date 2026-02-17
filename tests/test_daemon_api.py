from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from overseer.codex_store import CodexStore
from overseer.daemon_api import OverseerDaemon, create_app
from overseer.execution.backend import ExecutionRecord, ExecutionRequest, LocalBackend
from overseer.human_api import HumanAPI
from overseer.integrators.codex import CodexIntegrator


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _setup_daemon(tmp_path: Path) -> tuple[OverseerDaemon, TestClient, HumanAPI, LocalBackend]:
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
    daemon.start()
    client = TestClient(create_app(daemon))
    return daemon, client, human_api, backend


def _create_queued_record(backend: LocalBackend, run_id: str, task_id: str) -> None:
    run_dir = backend.runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_path = run_dir / "meta.json"
    record = ExecutionRecord(
        run_id=run_id,
        task_id=task_id,
        status="queued",
        command=[sys.executable, "-c", "print('queued')"],
        cwd=str(run_dir),
        stdout_log=str(run_dir / "stdout.log"),
        stderr_log=str(run_dir / "stderr.log"),
        meta_path=str(meta_path),
        lock_path=str(run_dir / "run.lock"),
        created_at="2020-01-01T00:00:00Z",
    )
    backend._write_record(meta_path, record)
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "started",
                "at": "2020-01-01T00:00:00Z",
                "payload": {"record": record.__dict__},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_health_endpoint(tmp_path: Path) -> None:
    daemon, client, _, _ = _setup_daemon(tmp_path)
    try:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    finally:
        daemon.stop()


def test_dummy_run_is_visible_in_runs_endpoint(tmp_path: Path) -> None:
    daemon, client, _, backend = _setup_daemon(tmp_path)
    try:
        run_id = "run-daemon-1234"
        run_dir = backend.runs_root / run_id
        request = ExecutionRequest(
            run_id=run_id,
            task_id="task-daemon-123",
            command=[sys.executable, "-c", "print('daemon run')"],
            cwd=tmp_path,
            stdout_log=run_dir / "stdout.log",
            stderr_log=run_dir / "stderr.log",
            meta_path=run_dir / "meta.json",
            lock_path=tmp_path / "dummy.lock",
        )
        backend.submit(request)

        deadline = time.time() + 5
        while time.time() < deadline:
            runs = client.get("/runs").json()
            if any(run["run_id"] == run_id for run in runs):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("run never appeared in /runs")

        detail = client.get(f"/runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["task_id"] == "task-daemon-123"
    finally:
        daemon.stop()


def test_get_unknown_run_returns_404(tmp_path: Path) -> None:
    daemon, client, _, _ = _setup_daemon(tmp_path)
    try:
        response = client.get("/runs/run-does-not-exist")
        assert response.status_code == 404
        assert "run not found" in response.json()["detail"]
    finally:
        daemon.stop()


def test_cancel_endpoint_cancels_queued_run(tmp_path: Path) -> None:
    daemon, client, _, backend = _setup_daemon(tmp_path)
    try:
        run_id = "run-cancel-1234"
        _create_queued_record(backend, run_id=run_id, task_id="task-cancel-123")
        daemon.refresh_now()

        response = client.post(f"/runs/{run_id}/cancel")
        assert response.status_code == 200
        payload = response.json()
        assert payload["run_id"] == run_id
        assert payload["status"] in {"canceling", "canceled"}

        detail = client.get(f"/runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] in {"canceling", "canceled"}
    finally:
        daemon.stop()


def test_queue_resolve_endpoint_success_and_failures(tmp_path: Path) -> None:
    daemon, client, human_api, _ = _setup_daemon(tmp_path)
    try:
        human_api.append_request({"id": "task-queue-123"}, "need human decision")
        request = human_api.list_requests()[0]

        queued = client.get("/queue")
        assert queued.status_code == 200
        assert queued.json()[0]["request_id"] == request.request_id

        response = client.post(
            f"/queue/{request.request_id}/resolve",
            json={"choice": request.options[0], "rationale": "approved"},
        )
        assert response.status_code == 200
        assert response.json()["request_id"] == request.request_id

        invalid = client.post(
            f"/queue/{request.request_id}/resolve",
            json={"choice": "Not an option", "rationale": "bad"},
        )
        assert invalid.status_code == 400

        missing = client.post(
            "/queue/hr-aaaaaaaaaaaa/resolve",
            json={"choice": request.options[0], "rationale": "missing"},
        )
        assert missing.status_code == 404
    finally:
        daemon.stop()
