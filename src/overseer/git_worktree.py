from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitRepoError(RuntimeError):
    pass


def resolve_git_root(start_path: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GitRepoError("Not inside a git repository. Run overseer from within a git repo.")
    return Path(result.stdout.strip())


@dataclass(frozen=True)
class WorktreeHandle:
    run_id: str
    task_id: str
    branch_name: str
    path: Path
    lock_path: Path


class GitWorktreeManager:
    def __init__(self, repo_root: Path, codex_root: Path) -> None:
        self.repo_root = repo_root
        self.codex_root = codex_root

    def create_for_run(self, task_id: str, run_id: str) -> WorktreeHandle:
        branch_name = f"overseer/{task_id}/{run_id}"
        path = self.codex_root / "10_OVERSEER" / "worktrees" / run_id
        lock_path = self.codex_root / "10_OVERSEER" / "locks" / f"{task_id}.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            result = subprocess.run(
                ["git", "worktree", "add", "-b", branch_name, str(path), "HEAD"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise GitRepoError(f"Failed to create worktree: {result.stderr.strip()}")
        return WorktreeHandle(
            run_id=run_id, task_id=task_id, branch_name=branch_name, path=path, lock_path=lock_path
        )

    def cleanup(self, handle: WorktreeHandle) -> None:
        # keep worktrees for inspectability; explicit prune can be added later.
        _ = handle
