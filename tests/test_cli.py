from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None
HAS_LANGCHAIN = importlib.util.find_spec("langchain") is not None
HAS_RUNTIME_DEPS = HAS_LANGGRAPH and HAS_LANGCHAIN


def run_cli(repo: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    run_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    if env:
        run_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "overseer", "--repo-root", str(repo), *args],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=check,
        env=run_env,
    )


def init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / ".gitignore").write_text(".pytest_cache/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True)


def _fake_codex_script(bin_dir: Path, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "codex"
    script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body + "\n", encoding="utf-8")
    script.chmod(0o755)


def test_init_validates_codex_structure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "codex").mkdir(parents=True)

    run_cli(repo, "init")

    assert (repo / "codex" / "03_WORK" / "TASK_GRAPH.jsonl").exists()
    assert (repo / "codex" / "10_OVERSEER").exists()
    assert (repo / "codex" / "11_WORKERS" / "builder").exists()


def test_add_task_appends_valid_jsonl(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")

    result = run_cli(repo, "add-task", "scaffold sanity check")
    task_id = result.stdout.strip()

    lines = (repo / "codex" / "03_WORK" / "TASK_GRAPH.jsonl").read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[-1])
    assert payload["id"] == task_id
    assert payload["status"] == "queued"


@pytest.mark.skipif(not HAS_RUNTIME_DEPS, reason="langgraph/langchain not installed in test environment")
def test_run_writes_run_log_updates_status(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    task_id = run_cli(repo, "add-task", "normal objective").stdout.strip()

    run_cli(repo, "run", "--task", task_id)

    tasks = [json.loads(line) for line in (repo / "codex" / "03_WORK" / "TASK_GRAPH.jsonl").read_text(encoding="utf-8").splitlines() if line]
    task = next(t for t in tasks if t["id"] == task_id)
    assert task["status"] == "done"


def test_integrate_sets_awaiting_review_when_diff_exists(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    task_id = run_cli(repo, "add-task", "integration objective").stdout.strip()

    bin_dir = tmp_path / "bin"
    _fake_codex_script(bin_dir, 'echo "new" > integrated.txt\ngit add integrated.txt')
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    run_cli(repo, "integrate", "--task", task_id, env=env)

    tasks = [json.loads(line) for line in (repo / "codex" / "03_WORK" / "TASK_GRAPH.jsonl").read_text(encoding="utf-8").splitlines() if line]
    task = next(t for t in tasks if t["id"] == task_id)
    assert task["status"] == "awaiting_review"


def test_integrate_sets_escalated_when_codex_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    task_id = run_cli(repo, "add-task", "integration objective").stdout.strip()

    result = run_cli(repo, "integrate", "--task", task_id, check=False, env={"PATH": ""})
    assert result.returncode != 0

    queue = (repo / "codex" / "04_HUMAN_API" / "HUMAN_QUEUE.md").read_text(encoding="utf-8")
    assert "HUMAN_REQUEST:" in queue


def test_integrate_requires_git_repository_context(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    task_id = run_cli(repo, "add-task", "integration objective").stdout.strip()

    bin_dir = tmp_path / "bin"
    _fake_codex_script(bin_dir, 'echo "noop"')
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    result = run_cli(repo, "integrate", "--task", task_id, check=False, env=env)
    assert result.returncode != 0
    assert "Not inside a git repository" in result.stderr
