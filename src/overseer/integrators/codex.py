from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class CodexWorktreeTarget:
    task_id: str
    branch_name: str
    path: Path


@dataclass(frozen=True)
class GitCommandError(RuntimeError):
    step: str
    command: tuple[str, ...]
    returncode: int
    stderr: str

    def __str__(self) -> str:
        cmd = " ".join(self.command)
        return (
            f"{self.step} failed (exit {self.returncode}). "
            f"command={cmd!r} stderr={self.stderr.strip()!r}"
        )


def _run_git_command(command: list[str], step: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GitCommandError(
            step=step,
            command=tuple(command),
            returncode=result.returncode,
            stderr=result.stderr,
        )
    return result.stdout.strip()


def resolve_codex_worktree_target(task_id: str) -> CodexWorktreeTarget:
    repo_root = Path(_run_git_command(["git", "rev-parse", "--show-toplevel"], step="resolve_repo_root"))
    branch_name = f"overseer/{task_id}"
    path = repo_root / "codex" / "10_OVERSEER" / "worktrees" / task_id
    return CodexWorktreeTarget(task_id=task_id, branch_name=branch_name, path=path)


def ensure_codex_worktree(task_id: str) -> CodexWorktreeTarget:
    target = resolve_codex_worktree_target(task_id)
    if target.path.exists():
        return target

    target.path.parent.mkdir(parents=True, exist_ok=True)
    _run_git_command(
        ["git", "worktree", "add", "-b", target.branch_name, str(target.path), "HEAD"],
        step="create_worktree",
    )
    return target
