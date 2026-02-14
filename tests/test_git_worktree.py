"""Tests for overseer.git_worktree: resolve_git_root, GitWorktreeManager, WorktreeHandle."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from overseer.git_worktree import (
    GitRepoError,
    GitWorktreeManager,
    resolve_git_root,
    WorktreeHandle,
)


def test_resolve_git_root_returns_root(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "t@x.com"],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    )
    (tmp_path / "f").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    root = resolve_git_root(tmp_path)
    assert root == tmp_path.resolve()
    sub_dir = tmp_path / "a" / "b"
    sub_dir.mkdir(parents=True)
    root2 = resolve_git_root(sub_dir)
    assert root2 == tmp_path.resolve()


def test_resolve_git_root_raises_outside_repo(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    with pytest.raises(GitRepoError, match="Not inside a git repository"):
        resolve_git_root(tmp_path)


def test_worktree_handle_has_run_id_and_path() -> None:
    handle = WorktreeHandle(
        run_id="run-1",
        task_id="task-1",
        branch_name="overseer/task-1/run-1",
        path=Path("/codex/10_OVERSEER/worktrees/run-1"),
        lock_path=Path("/codex/10_OVERSEER/locks/run-1.lock"),
    )
    assert handle.run_id == "run-1"
    assert handle.task_id == "task-1"
    assert "run-1" in str(handle.path)
    assert "run-1.lock" in str(handle.lock_path)


def test_create_for_run_returns_handle_with_per_run_lock(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "t@x.com"],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    )
    (tmp_path / "f").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    codex = tmp_path / "codex"
    codex.mkdir(parents=True)
    (codex / "10_OVERSEER" / "worktrees").mkdir(parents=True)
    (codex / "10_OVERSEER" / "locks").mkdir(parents=True, exist_ok=True)
    mgr = GitWorktreeManager(repo_root=tmp_path, codex_root=codex)
    handle = mgr.create_for_run(task_id="task-a", run_id="run-xyz")
    assert handle.run_id == "run-xyz"
    assert handle.task_id == "task-a"
    assert handle.path == codex / "10_OVERSEER" / "worktrees" / "run-xyz"
    assert handle.lock_path == codex / "10_OVERSEER" / "locks" / "run-xyz.lock"
    assert handle.path.exists()
