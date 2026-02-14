from __future__ import annotations

import json
import subprocess
from pathlib import Path

from overseer.integrator import Integrator


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _scaffold_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Overseer Tests")

    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    codex = repo / "codex"
    (codex / "04_HUMAN_API").mkdir(parents=True)
    (codex / "08_TELEMETRY").mkdir(parents=True)
    (codex / "10_OVERSEER").mkdir(parents=True)
    (codex / "04_HUMAN_API" / "HUMAN_QUEUE.md").write_text("# Human Queue\n", encoding="utf-8")
    (codex / "08_TELEMETRY" / "RUN_LOG.jsonl").write_text("", encoding="utf-8")

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")


def test_integrator_creates_and_reuses_worktree_with_expected_branch_and_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    _scaffold_repo(repo)

    calls: list[tuple[list[str], Path]] = []

    def fake_run(cmd: list[str], cwd: Path | None = None, **kwargs):
        calls.append((cmd, Path(cwd) if cwd else repo))
        if cmd[:3] == ["git", "worktree", "add"]:
            worktree = Path(cmd[-2])
            worktree.mkdir(parents=True, exist_ok=True)
            (worktree / ".git").write_text("gitdir", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["codex", "run"]:
            return subprocess.CompletedProcess(cmd, 0, "ok", "")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    integrator = Integrator(repo)
    task_id = "task-abc123"

    first = integrator.run_task(task_id, "build feature")
    second = integrator.run_task(task_id, "build feature")

    assert first.worktree == repo / "codex" / "10_OVERSEER" / "worktrees" / task_id
    assert first.branch == f"overseer/{task_id}"
    assert (first.worktree / "INSTRUCTIONS.md").exists()

    git_worktree_add_calls = [cmd for cmd, _ in calls if cmd[:3] == ["git", "worktree", "add"]]
    assert len(git_worktree_add_calls) == 1
    assert git_worktree_add_calls[0][4] == f"overseer/{task_id}"

    run_dir = repo / "codex" / "10_OVERSEER" / "runs" / task_id
    assert (run_dir / "codex.stdout.log").read_text(encoding="utf-8") == "ok"
    assert (run_dir / "codex.stderr.log").read_text(encoding="utf-8") == ""
    assert json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))["branch"] == first.branch
    assert second.status == "done"


def test_integrator_appends_run_log_and_escalates_with_diagnosis_packet(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    _scaffold_repo(repo)

    def fake_run(cmd: list[str], cwd: Path | None = None, **kwargs):
        if cmd[:3] == ["git", "worktree", "add"]:
            worktree = Path(cmd[-2])
            worktree.mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["codex", "run"]:
            return subprocess.CompletedProcess(cmd, 2, "partial output", "boom stderr")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    integrator = Integrator(repo)
    task_id = "task-escalate"
    result = integrator.run_task(task_id, "force escalation")

    assert result.escalated is True
    assert result.reason == "codex_exit_nonzero"

    lines = [
        json.loads(line)
        for line in (repo / "codex" / "08_TELEMETRY" / "RUN_LOG.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert lines[-1]["task_id"] == task_id
    assert lines[-1]["status"] == "escalated"
    assert lines[-1]["integrator"]["branch"] == f"overseer/{task_id}"
    assert lines[-1]["integrator"]["codex_returncode"] == 2
    assert lines[-1]["integrator"]["escalation_reason"] == "codex_exit_nonzero"

    queue = (repo / "codex" / "04_HUMAN_API" / "HUMAN_QUEUE.md").read_text(encoding="utf-8")
    assert "HUMAN_REQUEST:" in queue
    assert "TYPE: diagnosis" in queue
    assert f"TASK_ID: {task_id}" in queue
    assert "ESCALATION_REASON: codex_exit_nonzero" in queue
    assert "stdout_tail: partial output" in queue
    assert "stderr_tail: boom stderr" in queue
