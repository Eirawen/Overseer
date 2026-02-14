from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from overseer.codex_store import CodexStore
from overseer.human_api import HumanAPI
from overseer.integrators.codex import CodexExecutionError, CodexIntegrator, GitCommandError
from overseer.task_store import TaskStore
from overseer.termination import TerminationPolicy


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _setup_repo(repo: Path) -> tuple[CodexStore, TaskStore, HumanAPI]:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Overseer Tests")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")

    (repo / "codex").mkdir(parents=True, exist_ok=True)
    (repo / "codex").mkdir(parents=True, exist_ok=True)
    codex_store = CodexStore(repo)
    codex_store.init_structure()

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")

    task_store = TaskStore(codex_store)
    human_api = HumanAPI(codex_store)
    return codex_store, task_store, human_api


def test_worktree_helpers_create_and_reuse_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codex_store, task_store, human_api = _setup_repo(tmp_path / "repo")
    policy = TerminationPolicy(max_review_cycles=2, max_verifier_disputes=2, max_test_failures_without_progress=2)
    integrator = CodexIntegrator(codex_store.repo_root, task_store, human_api, policy)

    target = integrator.ensure_codex_worktree("task-1")
    again = integrator.ensure_codex_worktree("task-1")

    assert target.path.exists()
    assert again.path == target.path
    assert target.branch_name == "overseer/task-1"


def test_non_git_repo_raises_clear_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "codex").mkdir(parents=True, exist_ok=True)
    (repo / "codex").mkdir(parents=True, exist_ok=True)
    codex_store = CodexStore(repo)
    codex_store.init_structure()
    task_store = TaskStore(codex_store)
    human_api = HumanAPI(codex_store)
    policy = TerminationPolicy(max_review_cycles=2, max_verifier_disputes=2, max_test_failures_without_progress=2)
    integrator = CodexIntegrator(repo, task_store, human_api, policy)

    with pytest.raises(GitCommandError):
        integrator.ensure_codex_worktree("task-2")


def test_missing_codex_binary_raises_and_escalates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codex_store, task_store, human_api = _setup_repo(tmp_path / "repo")
    task = task_store.add_task("objective")
    policy = TerminationPolicy(max_review_cycles=2, max_verifier_disputes=2, max_test_failures_without_progress=2)
    integrator = CodexIntegrator(codex_store.repo_root, task_store, human_api, policy)

    monkeypatch.setattr("overseer.integrators.codex.shutil.which", lambda _name: None)

    with pytest.raises(CodexExecutionError):
        integrator.run_task(task)

    queue = (codex_store.codex_root / "04_HUMAN_API" / "HUMAN_QUEUE.md").read_text(encoding="utf-8")
    assert "codex cli unavailable" in queue


def test_run_task_writes_artifacts_and_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codex_store, task_store, human_api = _setup_repo(tmp_path / "repo")
    task = task_store.add_task("implement change")
    policy = TerminationPolicy(max_review_cycles=2, max_verifier_disputes=2, max_test_failures_without_progress=2)
    integrator = CodexIntegrator(codex_store.repo_root, task_store, human_api, policy)

    def fake_which(_name: str) -> str:
        return "/usr/bin/codex"

    real_run = subprocess.run

    def fake_run(cmd: list[str], cwd: Path | None = None, capture_output: bool = True, text: bool = True, check: bool = False):
        if cmd[0] == "codex":
            (Path(cwd) / "changed.txt").write_text("hello\n", encoding="utf-8")
            _git(Path(cwd), "add", "changed.txt")
            return subprocess.CompletedProcess(cmd, 0, "ok", "")
        return real_run(cmd, cwd=cwd, capture_output=capture_output, text=text, check=check)

    monkeypatch.setattr("overseer.integrators.codex.shutil.which", fake_which)
    monkeypatch.setattr("overseer.integrators.codex.subprocess.run", fake_run)

    result = integrator.run_task(task)
    run_dir = codex_store.codex_root / "10_OVERSEER" / "runs" / task["id"]
    telemetry_lines = [json.loads(line) for line in (codex_store.codex_root / "08_TELEMETRY" / "RUN_LOG.jsonl").read_text(encoding="utf-8").splitlines() if line]

    assert result["status"] == "awaiting_review"
    assert (run_dir / "codex.log").exists()
    assert (run_dir / "meta.json").exists()
    assert (run_dir / "patch.diff").exists()
    assert telemetry_lines[-1]["phase"] == "integrator"
    assert telemetry_lines[-1]["diff_present"] is True
