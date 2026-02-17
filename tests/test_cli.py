from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def run_cli(
    repo: Path, *args: str, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
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
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True, text=True
    )
    (repo / ".gitignore").write_text(".pytest_cache/\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", ".gitignore"], cwd=repo, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True
    )


def _fake_codex_script(bin_dir: Path, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "codex"
    script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body + "\n", encoding="utf-8")
    script.chmod(0o755)


def test_run_agent_and_status(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    task_id = run_cli(repo, "add-task", "integration objective").stdout.strip()

    bin_dir = tmp_path / "bin"
    _fake_codex_script(bin_dir, 'echo "ok"\n')
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    run_id = run_cli(repo, "run-agent", "--task", task_id, env=env).stdout.strip()

    deadline = time.time() + 10
    status_output = ""
    while time.time() < deadline:
        status_output = run_cli(repo, "run-status", "--run", run_id, env=env).stdout
        if "status=done" in status_output or "status=failed" in status_output:
            break
        time.sleep(0.1)

    assert "task=" in status_output
    runs_output = run_cli(repo, "runs", env=env).stdout
    assert run_id in runs_output


def test_integrate_sets_escalated_when_codex_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    task_id = run_cli(repo, "add-task", "integration objective").stdout.strip()

    git_dir = str(Path(shutil.which("git") or "").parent)
    result = run_cli(repo, "integrate", "--task", task_id, check=False, env={"PATH": git_dir})
    assert result.returncode != 0

    queue = (repo / "codex" / "04_HUMAN_API" / "HUMAN_QUEUE.md").read_text(encoding="utf-8")
    assert "Install steps:" in queue


def test_requires_git_repository_context(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / "codex").mkdir(parents=True)

    result = run_cli(repo, "init", check=False)
    assert result.returncode != 0
    assert "Not inside a git repository" in result.stderr


def test_init_prints_message(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    result = run_cli(repo, "init")
    assert result.returncode == 0
    assert "Initialized" in result.stdout or "initialized" in result.stdout.lower()


def test_add_task_prints_task_id(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    result = run_cli(repo, "add-task", "my objective")
    assert result.returncode == 0
    task_id = result.stdout.strip()
    assert task_id.startswith("task-")
    assert len(task_id) == 17  # task- (5) + 12 hex


def test_brief_prints_queued_and_escalated(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    run_cli(repo, "add-task", "first")
    run_cli(repo, "add-task", "second")
    result = run_cli(repo, "brief")
    assert result.returncode == 0
    assert "queued" in result.stdout.lower()
    assert "2" in result.stdout or "escalated" in result.stdout.lower()


def test_run_cancel_command(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")

    run_id = "run-cancel-cli"
    run_dir = repo / "codex" / "08_TELEMETRY" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = run_dir / "meta.json"
    meta.write_text(
        """{
  "run_id": "run-cancel-cli",
  "task_id": "task-1",
  "status": "queued",
  "command": [],
  "cwd": ".",
  "stdout_log": "stdout.log",
  "stderr_log": "stderr.log",
  "meta_path": "meta.json",
  "lock_path": "lock",
  "created_at": "2020-01-01T00:00:00Z",
  "started_at": null,
  "ended_at": null,
  "exit_code": null,
  "worker_pid": null,
  "notes_enforced": false
}
""",
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text(
        "{\"type\":\"started\",\"at\":\"2020-01-01T00:00:00Z\",\"payload\":{\"record\":{\"run_id\":\"run-cancel-cli\",\"task_id\":\"task-1\",\"status\":\"queued\",\"command\":[],\"cwd\":\".\",\"stdout_log\":\"stdout.log\",\"stderr_log\":\"stderr.log\",\"meta_path\":\"meta.json\",\"lock_path\":\"lock\",\"created_at\":\"2020-01-01T00:00:00Z\",\"started_at\":null,\"ended_at\":null,\"exit_code\":null,\"worker_pid\":null,\"notes_enforced\":false}}}\n",
        encoding="utf-8",
    )

    cancel_output = run_cli(repo, "run-cancel", "--run", run_id).stdout
    assert f"{run_id} task=" in cancel_output
    assert "status=canceled" in cancel_output
