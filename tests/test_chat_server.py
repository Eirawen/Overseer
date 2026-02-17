from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import request
from urllib.error import HTTPError

import pytest

from overseer.chat_server import OverseerChatService, build_server, serve_chat
from overseer.codex_store import CodexStore
from overseer.execution.backend import ExecutionRecord, LocalBackend
from overseer.human_api import HumanAPI
from overseer.integrators.codex import CodexIntegrator
from overseer.task_store import TaskStore


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _fake_codex(bin_dir: Path, body: str) -> dict[str, str]:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "codex"
    script.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


def _setup_service(
    tmp_path: Path, monkeypatch, script_body: str = "echo done"
) -> tuple[OverseerChatService, ThreadingHTTPServer, str, CodexStore]:
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

    env = _fake_codex(tmp_path / "bin", script_body)
    monkeypatch.setenv("PATH", env["PATH"])

    task_store = TaskStore(store)
    human_api = HumanAPI(store)
    backend = LocalBackend(store.codex_root, human_api=human_api)
    integrator = CodexIntegrator(store.repo_root, human_api=human_api, backend=backend)
    service = OverseerChatService(store, task_store, integrator, human_api)
    service.start()
    server = build_server(service, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    return service, server, base_url, store


def _post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=5) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str) -> dict | list:
    with request.urlopen(url, timeout=5) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def test_serve_refuses_outside_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / "codex").mkdir(parents=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-m", "overseer", "--repo-root", str(repo), "serve"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 1
    assert "Not inside a git repository" in result.stderr


def test_message_returns_immediately_and_creates_run(tmp_path: Path, monkeypatch) -> None:
    service, server, base_url, _ = _setup_service(tmp_path, monkeypatch, "sleep 2\necho done")
    try:
        start = time.perf_counter()
        out = _post_json(f"{base_url}/message", {"text": "ship the fix"})
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0
        assert out["created_run_ids"]
    finally:
        server.shutdown()
        service.stop()


def test_runs_support_multiple_runs_for_same_task(tmp_path: Path, monkeypatch) -> None:
    service, server, base_url, store = _setup_service(tmp_path, monkeypatch)
    try:
        task = TaskStore(store).add_task("same task")
        _post_json(f"{base_url}/message", {"text": f"please run {task['id']}"})
        _post_json(f"{base_url}/message", {"text": f"run again for {task['id']}"})
        runs = _get_json(f"{base_url}/runs")
        task_runs = [r for r in runs if r["task_id"] == task["id"]]
        assert len(task_runs) >= 2
    finally:
        server.shutdown()
        service.stop()


def test_message_reuses_existing_task_id(tmp_path: Path, monkeypatch) -> None:
    service, server, base_url, store = _setup_service(tmp_path, monkeypatch)
    try:
        task_store = TaskStore(store)
        task = task_store.add_task("existing objective")
        before_count = len(task_store.load_tasks())
        out = _post_json(f"{base_url}/message", {"text": f"please execute {task['id']} now"})
        after_tasks = task_store.load_tasks()
        assert len(after_tasks) == before_count
        assert out["created_task_id"] is None
    finally:
        server.shutdown()
        service.stop()


def test_message_writes_conversation_log_and_run_details(tmp_path: Path, monkeypatch) -> None:
    service, server, base_url, store = _setup_service(tmp_path, monkeypatch)
    try:
        out = _post_json(f"{base_url}/message", {"text": "capture logs"})
        run_id = out["created_run_ids"][0]

        runs_detail = _get_json(f"{base_url}/runs/{run_id}")
        assert runs_detail["run_id"] == run_id
        assert runs_detail["worktree"].endswith(f"/codex/10_OVERSEER/worktrees/{run_id}")
        assert runs_detail["meta_path"].endswith(f"/codex/08_TELEMETRY/runs/{run_id}/meta.json")
        assert runs_detail["stdout_log"].endswith(f"/codex/08_TELEMETRY/runs/{run_id}/stdout.log")

        conversation_files = sorted((store.codex_root / "08_TELEMETRY" / "conversations").glob("*.jsonl"))
        assert conversation_files
        lines = [json.loads(line) for line in conversation_files[-1].read_text(encoding="utf-8").splitlines()]
        assert [entry["role"] for entry in lines[-2:]] == ["human", "assistant"]
        assert lines[-1]["payload"]["created_run_ids"] == [run_id]
    finally:
        server.shutdown()
        service.stop()


def test_message_rejects_empty_text(tmp_path: Path, monkeypatch) -> None:
    service, server, base_url, _ = _setup_service(tmp_path, monkeypatch)
    try:
        with pytest.raises(HTTPError) as exc:
            _post_json(f"{base_url}/message", {"text": "   "})
        assert exc.value.code == 400
    finally:
        server.shutdown()
        service.stop()


def test_serve_chat_rejects_non_localhost_binding() -> None:
    with pytest.raises(RuntimeError, match="localhost only"):
        serve_chat(object(), host="0.0.0.0", port=8765)  # type: ignore[arg-type]


def test_events_emit_status_transitions(tmp_path: Path, monkeypatch) -> None:
    service, server, base_url, _ = _setup_service(tmp_path, monkeypatch, "echo done")
    try:
        _post_json(f"{base_url}/message", {"text": "run event test"})
        with request.urlopen(f"{base_url}/events", timeout=5) as resp:  # noqa: S310
            deadline = time.time() + 5
            saw_status = False
            while time.time() < deadline:
                line = resp.readline().decode("utf-8")
                if line.startswith("data: ") and "run_status" in line:
                    saw_status = True
                    break
            assert saw_status
    finally:
        server.shutdown()
        service.stop()


def test_notes_enforcement_escalates_when_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / "codex").mkdir(parents=True)
    store = CodexStore(repo)
    store.init_structure()
    human_api = HumanAPI(store)
    backend = LocalBackend(store.codex_root, human_api=human_api)

    run_id = "run-123456789abc"
    run_dir = store.codex_root / "08_TELEMETRY" / "runs" / run_id
    run_dir.mkdir(parents=True)
    meta_path = run_dir / "meta.json"
    record = ExecutionRecord(
        run_id=run_id,
        task_id="task-123456789abc",
        status="done",
        command=[],
        cwd=str(repo),
        stdout_log=str(run_dir / "stdout.log"),
        stderr_log=str(run_dir / "stderr.log"),
        meta_path=str(meta_path),
        lock_path=str(store.codex_root / "10_OVERSEER" / "locks" / f"{run_id}.lock"),
        created_at="2020-01-01T00:00:00Z",
    )
    backend._write_record(meta_path, record)

    updated = backend.status(run_id)
    assert updated.status == "failed"
    queue = (store.codex_root / "04_HUMAN_API" / "HUMAN_QUEUE.md").read_text(encoding="utf-8")
    assert "missing required notes" in queue
