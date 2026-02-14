from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from overseer.human_api import HumanAPI
from overseer.task_store import TaskStore
from overseer.termination import TerminationPolicy


@dataclass(frozen=True)
class GitCommandError(RuntimeError):
    message: str


@dataclass(frozen=True)
class CodexExecutionError(RuntimeError):
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class CodexWorktreeTarget:
    task_id: str
    branch_name: str
    path: Path


class CodexIntegrator:
    def __init__(
        self,
        repo_root: Path,
        task_store: TaskStore,
        human_api: HumanAPI,
        policy: TerminationPolicy,
        command: list[str] | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.task_store = task_store
        self.human_api = human_api
        self.policy = policy
        self.command = command or ["codex", "run"]
        self.codex_root = repo_root / "codex"

    def _run_git(self, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=cwd or self.repo_root, capture_output=True, text=True, check=False)

    def _ensure_git_repo(self) -> None:
        if not (self.repo_root / ".git").exists():
            raise GitCommandError("Not inside a git repository: missing .git at repo root")
        result = self._run_git(["rev-parse", "--is-inside-work-tree"])
        if result.returncode != 0 or result.stdout.strip() != "true":
            raise GitCommandError("Not inside a git repository")

    def resolve_codex_worktree_target(self, task_id: str) -> CodexWorktreeTarget:
        self._ensure_git_repo()
        branch_name = f"overseer/{task_id}"
        path = self.codex_root / "10_OVERSEER" / "worktrees" / task_id
        return CodexWorktreeTarget(task_id=task_id, branch_name=branch_name, path=path)

    def ensure_codex_worktree(self, task_id: str) -> CodexWorktreeTarget:
        target = self.resolve_codex_worktree_target(task_id)
        if target.path.exists():
            return target
        target.path.parent.mkdir(parents=True, exist_ok=True)
        result = self._run_git(["worktree", "add", "-b", target.branch_name, str(target.path), "HEAD"])
        if result.returncode != 0:
            raise GitCommandError(f"Failed to create worktree: {result.stderr.strip()}")
        return target

    def _instructions_text(self, task: dict[str, Any]) -> str:
        return "\n".join(
            [
                "# INSTRUCTIONS",
                "",
                "## Objective",
                task["objective"],
                "",
                "## Required",
                "- Run tests relevant to your change before finishing.",
                "- Follow termination rules and stop when blocked.",
                "- Do not modify canonical codex files under codex/01_PROJECT, codex/02_MEMORY, codex/03_WORK, codex/04_HUMAN_API, codex/05_AGENTS.",
                "",
            ]
        )

    def _write_artifacts(
        self,
        task_id: str,
        attempt_number: int,
        target: CodexWorktreeTarget,
        command: list[str],
        result: subprocess.CompletedProcess[str],
        patch_diff: str,
    ) -> tuple[Path, Path, Path]:
        run_dir = self.codex_root / "10_OVERSEER" / "runs" / task_id
        run_dir.mkdir(parents=True, exist_ok=True)

        codex_log = run_dir / "codex.log"
        meta = run_dir / "meta.json"
        patch = run_dir / "patch.diff"

        codex_log.write_text(result.stdout + ("\n" if result.stdout and result.stderr else "") + result.stderr, encoding="utf-8")
        meta_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "exit_code": result.returncode,
            "worktree_path": str(target.path),
            "attempt_number": attempt_number,
        }
        meta.write_text(json.dumps(meta_payload, indent=2) + "\n", encoding="utf-8")
        patch.write_text(patch_diff, encoding="utf-8")
        return codex_log, meta, patch

    def _append_telemetry(self, task_id: str, attempt_number: int, exit_code: int, diff_present: bool) -> None:
        run_log = self.codex_root / "08_TELEMETRY" / "RUN_LOG.jsonl"
        entry = {
            "phase": "integrator",
            "task_id": task_id,
            "attempt_number": attempt_number,
            "exit_code": exit_code,
            "diff_present": diff_present,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with run_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def _build_diagnosis_packet(self, target: CodexWorktreeTarget, codex_log_path: Path, exit_code: int) -> dict[str, Any]:
        lines = codex_log_path.read_text(encoding="utf-8").splitlines()
        status = self._run_git(["-C", str(target.path), "status", "--short"])
        diff_stat = self._run_git(["-C", str(target.path), "diff", "HEAD", "--stat"])
        changed_files = len([line for line in diff_stat.stdout.splitlines() if "|" in line])
        return {
            "last_exit_code": exit_code,
            "codex_log_tail": "\n".join(lines[-200:]),
            "git_status_short": status.stdout.strip(),
            "diff_summary": {"changed_files": changed_files, "stat": diff_stat.stdout.strip()},
        }

    def _escalate(self, task: dict[str, Any], reason: str, diagnosis: dict[str, Any]) -> dict[str, Any]:
        updated = self.task_store.update_status(
            task["id"],
            "escalated",
            escalation_reason=reason,
            escalation_packet=diagnosis,
            escalated=True,
        )
        self.human_api.append_request(updated, reason, diagnosis)
        return {"status": "escalated", "task": updated, "reason": reason, "diagnosis": diagnosis, "diff_present": False}

    def run_task(self, task: dict[str, Any]) -> dict[str, Any]:
        if shutil.which("codex") is None:
            diagnosis = {
                "last_exit_code": "not-run",
                "codex_log_tail": "codex binary not found on PATH",
                "git_status_short": "",
                "diff_summary": {"changed_files": 0, "stat": ""},
            }
            self.human_api.append_request(task, "codex cli unavailable", diagnosis)
            raise CodexExecutionError("codex_not_found", "codex CLI not installed or not on PATH")

        target = self.ensure_codex_worktree(task["id"])
        instructions = target.path / "INSTRUCTIONS.md"
        instructions.write_text(self._instructions_text(task), encoding="utf-8")

        previous: dict[str, Any] | None = None
        for attempt in range(1, self.policy.max_review_cycles + 1):
            result = subprocess.run(self.command, cwd=target.path, capture_output=True, text=True, check=False)
            diff_proc = self._run_git(["-C", str(target.path), "diff", "HEAD"])
            patch_diff = diff_proc.stdout
            diff_present = bool(patch_diff.strip())
            codex_log, _, _ = self._write_artifacts(task["id"], attempt, target, self.command, result, patch_diff)
            self._append_telemetry(task["id"], attempt, result.returncode, diff_present)

            current = {"exit_code": result.returncode, "diff": patch_diff}
            same_diff = previous is not None and previous["diff"] == current["diff"]
            nonzero_no_progress = previous is not None and previous["exit_code"] != 0 and current["exit_code"] != 0 and same_diff

            if result.returncode == 0 and diff_present:
                updated = self.task_store.update_status(task["id"], "awaiting_review")
                return {"status": "awaiting_review", "task": updated, "diff_present": True, "attempt_number": attempt}
            if nonzero_no_progress:
                diagnosis = self._build_diagnosis_packet(target, codex_log, result.returncode)
                return self._escalate(task, "integrator exited non-zero twice without diff progress", diagnosis)
            if same_diff and attempt >= 2:
                diagnosis = self._build_diagnosis_packet(target, codex_log, result.returncode)
                return self._escalate(task, "integrator diff unchanged across two attempts", diagnosis)
            previous = current

        diagnosis = self._build_diagnosis_packet(target, self.codex_root / "10_OVERSEER" / "runs" / task["id"] / "codex.log", previous["exit_code"] if previous else 1)
        return self._escalate(task, "max review cycles reached", diagnosis)
