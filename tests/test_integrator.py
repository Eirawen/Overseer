from __future__ import annotations

import json
import subprocess
from pathlib import Path

from overseer.codex_store import CodexStore
from overseer.human_api import HumanAPI
from overseer.integrators.codex import CodexIntegrator
from overseer.task_store import TaskStore
from overseer.termination import TerminationPolicy


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _setup(repo: Path) -> tuple[CodexStore, TaskStore, HumanAPI]:
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
    return store, TaskStore(store), HumanAPI(store)


def test_escalates_after_nonzero_without_diff_progress(tmp_path: Path, monkeypatch) -> None:
    codex_store, task_store, human_api = _setup(tmp_path / "repo")
    task = task_store.add_task("objective")
    policy = TerminationPolicy(max_review_cycles=3, max_verifier_disputes=2, max_test_failures_without_progress=2)
    integrator = CodexIntegrator(codex_store.repo_root, task_store, human_api, policy)

    monkeypatch.setattr("overseer.integrators.codex.shutil.which", lambda _name: "/usr/bin/codex")

    real_run = subprocess.run

    def fake_run(cmd: list[str], cwd: Path | None = None, capture_output: bool = True, text: bool = True, check: bool = False):
        if cmd[0] == "codex":
            return subprocess.CompletedProcess(cmd, 2, "", "err")
        return real_run(cmd, cwd=cwd, capture_output=capture_output, text=text, check=check)

    monkeypatch.setattr("overseer.integrators.codex.subprocess.run", fake_run)

    result = integrator.run_task(task)

    assert result["status"] == "escalated"
    assert result["reason"] == "integrator exited non-zero twice without diff progress"
    queue = (codex_store.codex_root / "04_HUMAN_API" / "HUMAN_QUEUE.md").read_text(encoding="utf-8")
    assert "DIAGNOSIS_PACKET:" in queue


def test_escalates_after_unchanged_diff_twice(tmp_path: Path, monkeypatch) -> None:
    codex_store, task_store, human_api = _setup(tmp_path / "repo")
    task = task_store.add_task("objective")
    policy = TerminationPolicy(max_review_cycles=3, max_verifier_disputes=2, max_test_failures_without_progress=2)
    integrator = CodexIntegrator(codex_store.repo_root, task_store, human_api, policy)

    monkeypatch.setattr("overseer.integrators.codex.shutil.which", lambda _name: "/usr/bin/codex")

    real_run = subprocess.run

    def fake_run(cmd: list[str], cwd: Path | None = None, capture_output: bool = True, text: bool = True, check: bool = False):
        if cmd[0] == "codex":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, cwd=cwd, capture_output=capture_output, text=text, check=check)

    monkeypatch.setattr("overseer.integrators.codex.subprocess.run", fake_run)

    result = integrator.run_task(task)

    assert result["status"] == "escalated"
    assert result["reason"] == "integrator diff unchanged across two attempts"

    logs = [json.loads(line) for line in (codex_store.codex_root / "08_TELEMETRY" / "RUN_LOG.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert logs[-1]["phase"] == "integrator"
