from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from overseer.codex_store import CodexStore
from overseer.execution.backend import LocalBackend
from overseer.human_api import HumanAPI
from overseer.integrators.codex import CodexIntegrator
from overseer.integrators.base import RunRequest


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _setup(repo: Path) -> tuple[CodexStore, HumanAPI, LocalBackend]:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Overseer Tests")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    (repo / "codex").mkdir(parents=True, exist_ok=True)
    store = CodexStore(repo)
    store.init_structure()
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "seed")
    return store, HumanAPI(store), LocalBackend(store.codex_root)


def test_worktree_created_per_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, human_api, backend = _setup(tmp_path / "repo")
    integrator = CodexIntegrator(store.repo_root, human_api=human_api, backend=backend)
    monkeypatch.setattr("overseer.integrators.codex.shutil.which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(backend, "submit", lambda request: request.run_id)

    run_id = integrator.submit(RunRequest(task_id="task-1", objective="do thing"))

    assert (store.codex_root / "10_OVERSEER" / "worktrees" / run_id).exists()


def test_missing_codex_binary_escalates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, human_api, backend = _setup(tmp_path / "repo")
    integrator = CodexIntegrator(store.repo_root, human_api=human_api, backend=backend)
    monkeypatch.setattr("overseer.integrators.codex.shutil.which", lambda _name: None)

    with pytest.raises(RuntimeError):
        integrator.submit(RunRequest(task_id="task-1", objective="do thing"))

    queue = (store.codex_root / "04_HUMAN_API" / "HUMAN_QUEUE.md").read_text(encoding="utf-8")
    assert "Attempted command: codex run" in queue
    assert "Install steps:" in queue


def test_integrator_status_delegates_to_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, human_api, backend = _setup(tmp_path / "repo")
    integrator = CodexIntegrator(store.repo_root, human_api=human_api, backend=backend)
    monkeypatch.setattr("overseer.integrators.codex.shutil.which", lambda _name: "/usr/bin/codex")
    run_id = "run-fake123"
    (store.codex_root / "08_TELEMETRY" / "runs" / run_id).mkdir(parents=True)
    meta = store.codex_root / "08_TELEMETRY" / "runs" / run_id / "meta.json"
    meta.write_text(
        '{"run_id":"run-fake123","task_id":"task-1","status":"done","command":[],"cwd":"","stdout_log":"","stderr_log":"","meta_path":"","lock_path":"","created_at":"2020-01-01T00:00:00Z","ended_at":"2020-01-01T00:01:00Z","exit_code":0}',
        encoding="utf-8",
    )
    (store.codex_root / "08_TELEMETRY" / "runs" / run_id / "notes.md").write_text(
        "- existing note\n", encoding="utf-8"
    )
    worker_notes = store.codex_root / "11_WORKERS" / "builder" / "NOTES.md"
    worker_notes.parent.mkdir(parents=True, exist_ok=True)
    worker_notes.write_text(f"- run={run_id}\n", encoding="utf-8")
    result = integrator.status(run_id)
    assert result.run_id == run_id
    assert result.status == "done"
    assert result.exit_code == 0


def test_integrator_runs_delegates_to_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, human_api, backend = _setup(tmp_path / "repo")
    integrator = CodexIntegrator(store.repo_root, human_api=human_api, backend=backend)
    monkeypatch.setattr("overseer.integrators.codex.shutil.which", lambda _name: "/usr/bin/codex")
    runs = integrator.runs()
    assert isinstance(runs, list)
