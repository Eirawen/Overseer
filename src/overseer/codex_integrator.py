from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any


class CodexExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, diagnosis: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.diagnosis = diagnosis

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "diagnosis": self.diagnosis}


class CodexIntegrator:
    def __init__(self, command: list[str] | None = None) -> None:
        self.command = command or ["codex", "run"]

    def _instruction_text(self, task: dict[str, Any]) -> str:
        objective = task.get("objective", "")
        return "\n".join(
            [
                "# INSTRUCTIONS",
                "",
                "## Objective",
                objective,
                "",
                "## Required test execution",
                "Run the project's required automated tests before finishing and report the results.",
                "",
                "## Termination constraints",
                "Terminate once the objective is complete, when blocked by missing dependencies/tools, or when policy constraints prevent safe progress.",
                "",
                "## Codex file modification restrictions",
                "Do not modify canonical codex files, including codex/01_PROJECT, codex/02_MEMORY, codex/03_WORK, codex/04_HUMAN_API, and codex/05_AGENTS.",
                "",
            ]
        )

    def run_task(self, task: dict[str, Any], worktree: Path) -> dict[str, Any]:
        codex_binary = shutil.which("codex")
        if codex_binary is None:
            raise CodexExecutionError(
                code="codex_not_found",
                message="Unable to execute Codex task because the codex CLI is not available.",
                diagnosis="shutil.which('codex') returned no result; install Codex CLI or ensure it is on PATH.",
            )

        worktree.mkdir(parents=True, exist_ok=True)
        instructions_path = worktree / "INSTRUCTIONS.md"
        instructions_path.write_text(self._instruction_text(task), encoding="utf-8")

        result = subprocess.run(
            self.command,
            cwd=worktree,
            capture_output=True,
            text=True,
        )

        return {
            "command": self.command,
            "codex_binary": codex_binary,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "instructions_path": str(instructions_path),
        }
