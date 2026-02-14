"""Tests for overseer.termination: TerminationPolicy.from_codex."""

from __future__ import annotations

from pathlib import Path

from overseer.termination import TerminationPolicy


def test_from_codex_reads_termination_file(tmp_path: Path) -> None:
    codex = tmp_path / "codex"
    (codex / "05_AGENTS").mkdir(parents=True)
    (codex / "05_AGENTS" / "TERMINATION.md").write_text(
        "# Rules\n"
        "max review cycles per task: 5\n"
        "if Reviewer and Verifier disagree twice => escalate to human\n"
        "if tests fail two without progress => escalate\n",
        encoding="utf-8",
    )
    policy = TerminationPolicy.from_codex(codex)
    assert policy.max_review_cycles == 5
    assert policy.max_verifier_disputes == 2
    assert policy.max_test_failures_without_progress == 2


def test_from_codex_uses_defaults_when_patterns_missing(tmp_path: Path) -> None:
    codex = tmp_path / "codex"
    (codex / "05_AGENTS").mkdir(parents=True)
    (codex / "05_AGENTS" / "TERMINATION.md").write_text("# No numbers here\n", encoding="utf-8")
    policy = TerminationPolicy.from_codex(codex)
    assert policy.max_review_cycles == 3
    assert policy.max_verifier_disputes == 2
    assert policy.max_test_failures_without_progress == 2
