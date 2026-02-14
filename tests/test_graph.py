"""Tests for overseer.graph: OverseerGraph run_task, update_codex, node behavior."""

from __future__ import annotations

import json
from pathlib import Path

from overseer.codex_store import CodexStore
from overseer.graph import (
    OverseerGraph,
    _mock_builder_report,
    _mock_reviewer_report,
    _mock_verifier_report,
    RunState,
)
from overseer.human_api import HumanAPI
from overseer.task_store import TaskStore


def _graph_fixture(tmp_path: Path) -> tuple[CodexStore, TaskStore, HumanAPI, OverseerGraph]:
    codex = tmp_path / "codex"
    codex.mkdir(parents=True)
    (codex / "03_WORK").mkdir(parents=True)
    (codex / "04_HUMAN_API").mkdir(parents=True)
    (codex / "05_AGENTS").mkdir(parents=True)
    (codex / "08_TELEMETRY").mkdir(parents=True)
    (codex / "11_WORKERS" / "builder").mkdir(parents=True)
    (codex / "11_WORKERS" / "reviewer").mkdir(parents=True)
    (codex / "11_WORKERS" / "verifier").mkdir(parents=True)
    (codex / "10_OVERSEER" / "locks").mkdir(parents=True, exist_ok=True)
    (codex / "05_AGENTS" / "TERMINATION.md").write_text(
        "max review cycles per task: 3\n"
        "if Reviewer and Verifier disagree twice => escalate\n"
        "if tests fail two without progress => escalate\n",
        encoding="utf-8",
    )
    (codex / "04_HUMAN_API" / "HUMAN_QUEUE.md").write_text("# Human Queue\n\n## Pending\n\n- (empty)\n", encoding="utf-8")
    store = CodexStore(tmp_path)
    store.codex_root = codex
    store.init_structure()
    task_store = TaskStore(store)
    human_api = HumanAPI(store)
    graph = OverseerGraph(store, task_store, human_api)
    return store, task_store, human_api, graph


def test_mock_builder_report_progress_when_fewer_failures() -> None:
    task = {"id": "t1", "objective": "build"}
    state: RunState = {}
    report = _mock_builder_report(task, state)
    assert report["tests"]["failing"] == 0
    assert report["progress"] is True


def test_mock_builder_report_force_test_fail() -> None:
    task = {"id": "t1", "objective": "build force-test-fail"}
    state: RunState = {}
    report = _mock_builder_report(task, state)
    assert report["tests"]["failing"] == 2


def test_mock_reviewer_approves_by_default() -> None:
    task = {"id": "t1", "objective": "build"}
    report = _mock_reviewer_report(task, {})
    assert report["approved"] is True


def test_mock_reviewer_rejects_force_review_reject() -> None:
    task = {"id": "t1", "objective": "force-review-reject"}
    report = _mock_reviewer_report(task, {})
    assert report["approved"] is False


def test_mock_verifier_follows_reviewer_by_default() -> None:
    task = {"id": "t1", "objective": "build"}
    reviewer = {"approved": True}
    report = _mock_verifier_report(task, reviewer)
    assert report["approved"] is True


def test_run_task_updates_status_and_run_log(tmp_path: Path) -> None:
    _, task_store, _, graph = _graph_fixture(tmp_path)
    t = task_store.add_task("simple objective")
    result = graph.run_task(t["id"])
    assert result["status"] in ("done", "escalated", "running")
    assert "task" in result
    updated = task_store.get_task(t["id"])
    assert updated["status"] in ("done", "escalated", "running")
    run_log = graph.run_log_path
    if run_log.exists():
        lines = [ln for ln in run_log.read_text(encoding="utf-8").strip().split("\n") if ln]
        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert entry["task_id"] == t["id"]
        assert "status" in entry


def test_run_task_done_when_tests_ok_and_approved(tmp_path: Path) -> None:
    """Objective without force-* triggers should reach done when mocks approve."""
    _, task_store, _, graph = _graph_fixture(tmp_path)
    t = task_store.add_task("normal feature")
    result = graph.run_task(t["id"])
    assert result["status"] == "done"
    assert task_store.get_task(t["id"])["status"] == "done"
