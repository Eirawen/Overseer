from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from overseer.integrators.codex import GitCommandError, ensure_codex_worktree, resolve_codex_worktree_target


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
