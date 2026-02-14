from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class IntegratorResult:
    task_id: str
    branch: str
    worktree: Path
    run_dir: Path
    status: str
    escalated: bool
    reason: str | None


class Integrator:
    """Executes Codex in a per-task git worktree and records artifacts."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.codex_root = repo_root / "codex"
        self.overseer_root = self.codex_root / "10_OVERSEER"
        self.run_log_path = self.codex_root / "08_TELEMETRY" / "RUN_LOG.jsonl"
        self.human_queue_path = self.codex_root / "04_HUMAN_API" / "HUMAN_QUEUE.md"

    def run_task(self, task_id: str, objective: str) -> IntegratorResult:
        branch = f"overseer/{task_id}"
        worktree = self.overseer_root / "worktrees" / task_id
        run_dir = self.overseer_root / "runs" / task_id
        run_dir.mkdir(parents=True, exist_ok=True)

        self._ensure_worktree(worktree, branch)
        instructions = self._write_instructions(worktree, task_id, objective)

        proc = subprocess.run(
            ["codex", "run", "--instructions", str(instructions)],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
        )

        (run_dir / "codex.stdout.log").write_text(proc.stdout, encoding="utf-8")
        (run_dir / "codex.stderr.log").write_text(proc.stderr, encoding="utf-8")
        (run_dir / "meta.json").write_text(
            json.dumps({"task_id": task_id, "branch": branch, "worktree": str(worktree)}, indent=2),
            encoding="utf-8",
        )

        reason = self._escalation_reason(proc)
        escalated = reason is not None
        status = "escalated" if escalated else "done"

        self._append_run_log(task_id, branch, worktree, run_dir, status, proc.returncode, reason)
        if escalated:
            self._append_human_request(task_id, objective, reason, proc.stdout, proc.stderr)

        return IntegratorResult(
            task_id=task_id,
            branch=branch,
            worktree=worktree,
            run_dir=run_dir,
            status=status,
            escalated=escalated,
            reason=reason,
        )

    def _ensure_worktree(self, worktree: Path, branch: str) -> None:
        if worktree.exists():
            return
        worktree.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "worktree", "add", "-b", branch, str(worktree), "HEAD"], cwd=self.repo_root, check=True)

    def _write_instructions(self, worktree: Path, task_id: str, objective: str) -> Path:
        path = worktree / "INSTRUCTIONS.md"
        path.write_text(
            "# Integrator Instructions\n"
            f"TASK_ID: {task_id}\n"
            f"OBJECTIVE: {objective}\n",
            encoding="utf-8",
        )
        return path

    def _append_run_log(
        self,
        task_id: str,
        branch: str,
        worktree: Path,
        run_dir: Path,
        status: str,
        codex_returncode: int,
        reason: str | None,
    ) -> None:
        entry = {
            "task_id": task_id,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "integrator": {
                "branch": branch,
                "worktree": str(worktree),
                "run_dir": str(run_dir),
                "codex_returncode": codex_returncode,
                "escalation_reason": reason,
            },
        }
        with self.run_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def _append_human_request(self, task_id: str, objective: str, reason: str, stdout: str, stderr: str) -> None:
        packet = (
            "\nHUMAN_REQUEST:\n"
            "TYPE: diagnosis\n"
            "URGENCY: high\n"
            f"TASK_ID: {task_id}\n"
            f"ESCALATION_REASON: {reason}\n"
            f"OBJECTIVE: {objective}\n"
            "DIAGNOSIS_PACKET:\n"
            f"  - stdout_tail: {stdout[-200:].strip() or '(empty)'}\n"
            f"  - stderr_tail: {stderr[-200:].strip() or '(empty)'}\n"
            "REPLY_FORMAT: Provide decision + next step\n"
        )
        with self.human_queue_path.open("a", encoding="utf-8") as handle:
            handle.write(packet)

    def _escalation_reason(self, proc: subprocess.CompletedProcess[str]) -> str | None:
        if proc.returncode != 0:
            return "codex_exit_nonzero"
        if "HUMAN_REQUEST" in proc.stdout or "HUMAN_REQUEST" in proc.stderr:
            return "codex_requested_human"
        return None
