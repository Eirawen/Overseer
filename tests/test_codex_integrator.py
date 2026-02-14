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
