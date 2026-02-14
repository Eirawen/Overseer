from __future__ import annotations

import subprocess
from pathlib import Path

from overseer.codex_store import CodexStore
from overseer.task_store import TaskStore


class CodexIntegrator:
    """Runs integration for a task and returns the working tree diff."""

    def __init__(self, repo_root: Path, codex_store: CodexStore, task_store: TaskStore) -> None:
        self.repo_root = repo_root
        self.codex_store = codex_store
        self.task_store = task_store

    def run_task(self, task_id: str) -> dict[str, str | bool]:
        self.task_store.get_task(task_id)
        diff = subprocess.run(
            ["git", "-C", str(self.repo_root), "diff", "--", ".", ":(exclude)codex"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return {"status": "completed", "diff": diff, "success": True}
