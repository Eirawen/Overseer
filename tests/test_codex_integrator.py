from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


from overseer.codex_integrator import CodexExecutionError, CodexIntegrator


def test_run_task_raises_structured_error_when_codex_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("overseer.codex_integrator.shutil.which", lambda _name: None)

    with pytest.raises(CodexExecutionError) as exc_info:
        CodexIntegrator().run_task({"objective": "Fix flaky tests"}, tmp_path / "wt")

    error = exc_info.value
    assert error.code == "codex_not_found"
    assert "codex CLI" in error.message
    assert "shutil.which('codex') returned no result" in error.diagnosis


def test_run_task_writes_instructions_and_captures_process_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("overseer.codex_integrator.shutil.which", lambda _name: "/usr/bin/codex")

    def _fake_run(cmd: list[str], cwd: Path, capture_output: bool, text: bool) -> subprocess.CompletedProcess[str]:
        assert cmd == ["codex", "run"]
        assert cwd == tmp_path / "wt"
        assert capture_output is True
        assert text is True
        return subprocess.CompletedProcess(cmd, 7, stdout="builder output", stderr="builder error")

    monkeypatch.setattr("overseer.codex_integrator.subprocess.run", _fake_run)

    integrator = CodexIntegrator()
    result = integrator.run_task({"objective": "Implement feature X"}, tmp_path / "wt")

    instructions = (tmp_path / "wt" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "## Objective" in instructions
    assert "Implement feature X" in instructions
    assert "## Required test execution" in instructions
    assert "## Termination constraints" in instructions
    assert "Do not modify canonical codex files" in instructions

    assert result["exit_code"] == 7
    assert result["stdout"] == "builder output"
    assert result["stderr"] == "builder error"


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True, text=True)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, text=True)


def test_resolve_codex_worktree_target_uses_repo_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)

    target = resolve_codex_worktree_target("TASK-1")

    assert target.branch_name == "overseer/TASK-1"
    assert target.path == repo / "codex" / "10_OVERSEER" / "worktrees" / "TASK-1"


def test_resolve_codex_worktree_target_raises_structured_error_outside_git_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(GitCommandError) as exc_info:
        resolve_codex_worktree_target("TASK-1")

    assert exc_info.value.step == "resolve_repo_root"
    assert exc_info.value.command == ("git", "rev-parse", "--show-toplevel")


def test_ensure_codex_worktree_reuses_existing_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    existing_path = repo / "codex" / "10_OVERSEER" / "worktrees" / "TASK-2"
    existing_path.mkdir(parents=True)
    monkeypatch.chdir(repo)

    target = ensure_codex_worktree("TASK-2")

    assert target.path == existing_path
    assert target.path.exists()


def test_ensure_codex_worktree_creates_worktree_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)

    target = ensure_codex_worktree("TASK-3")

    assert target.path.exists()
    result = subprocess.run(
        ["git", "-C", str(target.path), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "overseer/TASK-3"

