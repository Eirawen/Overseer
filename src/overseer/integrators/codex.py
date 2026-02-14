from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class CodexIntegrator:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def run_task(self, task: dict[str, Any]) -> dict[str, Any]:
        role = task["role"]
        payload = task["task"]

        if role == "builder":
            return self._run_builder(payload, task.get("state", {}))
        if role == "reviewer":
            return self._run_reviewer(payload)
        if role == "verifier":
            return self._run_verifier(payload, task["reviewer_report"])
        raise ValueError(f"Unsupported task role: {role}")

    def _run_builder(self, task: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        objective = task["objective"]
        failing = 2 if "force-test-fail" in objective else 0
        previous = state.get("last_failing_tests")
        progress = previous is None or failing < previous
        return {
            "agent": "builder",
            "summary": "Builder execution complete",
            "tests": {"failing": failing},
            "progress": progress,
            "git": self._git_snapshot(),
        }

    def _run_reviewer(self, task: dict[str, Any]) -> dict[str, Any]:
        approve = "force-review-reject" not in task["objective"]
        return {
            "agent": "reviewer",
            "approved": approve,
            "summary": "Reviewer approval" if approve else "Reviewer requests changes",
            "git": self._git_snapshot(),
        }

    def _run_verifier(self, task: dict[str, Any], reviewer_report: dict[str, Any]) -> dict[str, Any]:
        approved = not reviewer_report["approved"] if "force-escalate-disagreement" in task["objective"] else reviewer_report["approved"]
        return {
            "agent": "verifier",
            "approved": approved,
            "summary": "Verifier validation complete",
            "git": self._git_snapshot(),
        }

    def _git_snapshot(self) -> dict[str, str | None]:
        return {
            "branch": self._git_output("rev-parse", "--abbrev-ref", "HEAD"),
            "commit": self._git_output("rev-parse", "HEAD"),
        }

    def _git_output(self, *args: str) -> str | None:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
