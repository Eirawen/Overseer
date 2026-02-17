from __future__ import annotations

import shutil
from pathlib import Path

from overseer.execution.backend import ExecutionRequest, LocalBackend
from overseer.git_worktree import GitWorktreeManager
from overseer.human_api import HumanAPI
from overseer.integrators.base import RunRequest, RunResult


class CodexIntegrator:
    def __init__(
        self,
        repo_root: Path,
        human_api: HumanAPI,
        backend: LocalBackend,
        command: list[str] | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.codex_root = repo_root / "codex"
        self.human_api = human_api
        self.backend = backend
        self.command = command or ["codex", "run"]
        self.worktrees = GitWorktreeManager(repo_root=repo_root, codex_root=self.codex_root)

    def submit(self, request: RunRequest) -> str:
        codex_bin = shutil.which(self.command[0])
        if codex_bin is None:
            attempted = " ".join(self.command)
            reason = (
                "codex cli unavailable. Attempted command: "
                f"{attempted}. Install steps: 1) Install Codex CLI per org instructions. "
                "2) Ensure executable 'codex' is on PATH. 3) Validate with `codex --help`. "
                "Reply format: `DECISION: installed|blocked` and one sentence describing outcome."
            )
            self.human_api.append_request(
                {"id": request.task_id},
                reason,
                {
                    "last_exit_code": "not-run",
                    "codex_log_tail": "codex binary missing from PATH",
                    "git_status_short": "",
                    "diff_summary": {"changed_files": 0, "stat": ""},
                },
            )
            raise RuntimeError("codex CLI not installed or not on PATH")

        run_id = LocalBackend.new_run_id()
        worktree = self.worktrees.create_for_run(task_id=request.task_id, run_id=run_id)
        instructions = worktree.path / "INSTRUCTIONS.md"
        instructions.write_text(request.objective + "\n", encoding="utf-8")

        run_root = self.codex_root / "08_TELEMETRY" / "runs" / run_id
        execution_request = ExecutionRequest(
            run_id=run_id,
            task_id=request.task_id,
            command=[codex_bin, *self.command[1:]],
            cwd=worktree.path,
            stdout_log=run_root / "stdout.log",
            stderr_log=run_root / "stderr.log",
            meta_path=run_root / "meta.json",
            lock_path=worktree.lock_path,
        )
        return self.backend.submit(execution_request)

    def status(self, run_id: str) -> RunResult:
        record = self.backend.status(run_id)
        return RunResult(
            run_id=record.run_id,
            task_id=record.task_id,
            status=record.status,
            exit_code=record.exit_code,
        )

    def runs(self) -> list[RunResult]:
        records = self.backend.list_runs()
        return [
            RunResult(run_id=r.run_id, task_id=r.task_id, status=r.status, exit_code=r.exit_code)
            for r in records
        ]

    def cancel(self, run_id: str) -> RunResult:
        record = self.backend.cancel(run_id)
        return RunResult(
            run_id=record.run_id,
            task_id=record.task_id,
            status=record.status,
            exit_code=record.exit_code,
        )

