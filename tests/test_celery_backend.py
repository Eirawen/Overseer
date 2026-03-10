from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from overseer.execution.backend import CeleryBackend, ExecutionRequest, LocalBackend
from overseer.execution.factory import build_backend
from overseer.execution.run_store import RunSubmission


class _FakeAsyncResult:
    def __init__(self, task_id: str) -> None:
        self.id = task_id


class _FakeControl:
    def __init__(self) -> None:
        self.revoked: list[tuple[str, bool]] = []

    def revoke(self, task_id: str, terminate: bool = False) -> None:
        self.revoked.append((task_id, terminate))


class _FakeCeleryApp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.control = _FakeControl()

    def send_task(self, task_name: str, args: list[str]):
        self.calls.append((task_name, args))
        return _FakeAsyncResult("celery-task-123")


def test_celery_backend_submit_dispatches_task_and_persists_events(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    run_id = LocalBackend.new_run_id()
    run_root = codex_root / "08_TELEMETRY" / "runs" / run_id
    req = ExecutionRequest(
        run_id=run_id,
        task_id="task-celery",
        command=[sys.executable, "-c", "pass"],
        cwd=tmp_path,
        stdout_log=run_root / "stdout.log",
        stderr_log=run_root / "stderr.log",
        meta_path=run_root / "meta.json",
        lock_path=codex_root / "10_OVERSEER" / "locks" / f"{run_id}.lock",
    )
    fake_app = _FakeCeleryApp()
    backend = CeleryBackend(codex_root=codex_root, celery_app=fake_app)

    out = backend.submit(req)

    assert out == run_id
    assert fake_app.calls == [
        (
            "overseer.execution.celery_worker.execute_run",
            [run_id, str(codex_root)],
        )
    ]
    events = [
        json.loads(line)["type"]
        for line in (run_root / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert events == ["started", "worker_dispatched"]


def test_celery_backend_cancel_revokes_dispatched_task(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    fake_app = _FakeCeleryApp()
    backend = CeleryBackend(codex_root=codex_root, celery_app=fake_app)
    run_id = "run-celery-cancel"
    backend.run_store.create_run(
        RunSubmission(
            run_id=run_id,
            task_id="task-1",
            backend_type="celery",
            worktree_path=str(tmp_path),
            meta_json={"task_id": "task-1", "command": [sys.executable, "-c", "pass"], "cwd": str(tmp_path), "stdout_log": "", "stderr_log": "", "meta_path": str(codex_root / "08_TELEMETRY" / "runs" / run_id / "meta.json"), "lock_path": ""},
        )
    )
    backend._append_event(run_id, "worker_dispatched", {"celery_task_id": "task-abc"})

    backend.cancel(run_id)

    assert fake_app.control.revoked == [("task-abc", True)]


def test_build_backend_defaults_to_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OVERSEER_EXECUTION_BACKEND", raising=False)
    backend = build_backend(tmp_path / "codex")
    assert isinstance(backend, LocalBackend)


def test_build_backend_allows_celery_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pytest.importorskip("celery")
    monkeypatch.setenv("OVERSEER_EXECUTION_BACKEND", "celery")
    backend = build_backend(tmp_path / "codex")
    assert isinstance(backend, CeleryBackend)


@pytest.mark.integration
def test_celery_backend_default_app_configuration_with_redis_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL not set; skipping redis-backed celery configuration test")

    backend = CeleryBackend(codex_root=tmp_path / "codex")

    assert backend.celery_app.conf.broker_url == redis_url
    assert backend.celery_app.conf.result_backend == redis_url
