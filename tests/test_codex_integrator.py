from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from overseer.codex_integrator import CodexExecutionError, CodexIntegrator


def test_run_task_raises_structured_error_when_codex_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("overseer.codex_integrator.shutil.which", lambda _name: None)

    with pytest.raises(CodexExecutionError) as exc_info:
        CodexIntegrator().run_task({"objective": "Fix flaky tests"}, tmp_path / "wt")

    error = exc_info.value
    assert error.code == "codex_not_found"
    assert "codex CLI" in error.message
    assert "shutil.which('codex') returned no result" in error.diagnosis


def test_run_task_writes_instructions_and_captures_process_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("overseer.codex_integrator.shutil.which", lambda _name: "/usr/bin/codex")

    def _fake_run(cmd: list[str], cwd: Path, capture_output: bool, text: bool) -> subprocess.CompletedProcess[str]:
        assert cmd == ["codex", "run"]
        assert cwd == tmp_path / "wt"
        assert capture_output is True
        assert text is True
        return subprocess.CompletedProcess(cmd, 7, stdout="builder output", stderr="builder error")

    monkeypatch.setattr("overseer.codex_integrator.subprocess.run", _fake_run)

    integrator = CodexIntegrator()
    result = integrator.run_task({"objective": "Implement feature X"}, tmp_path / "wt")

    instructions = (tmp_path / "wt" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "## Objective" in instructions
    assert "Implement feature X" in instructions
    assert "## Required test execution" in instructions
    assert "## Termination constraints" in instructions
    assert "Do not modify canonical codex files" in instructions

    assert result["exit_code"] == 7
    assert result["stdout"] == "builder output"
    assert result["stderr"] == "builder error"
