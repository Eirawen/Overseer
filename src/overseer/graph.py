from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

try:
    from langgraph.graph import END, START, StateGraph
except ModuleNotFoundError:  # pragma: no cover - exercised only in offline/minimal envs
    from overseer.graph_runtime import END, START, StateGraph

try:
    from langchain import __version__ as _langchain_version  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    _langchain_version = "unavailable"

from overseer.codex_store import CodexStore
from overseer.human_api import HumanAPI
from overseer.task_store import TaskStore
from overseer.termination import TerminationPolicy


class RunState(TypedDict, total=False):
    task: dict[str, Any]
    cycle_count: int
    verifier_disputes: int
    test_failures_without_progress: int
    last_failing_tests: int | None
    builder_report: dict[str, Any]
    reviewer_report: dict[str, Any]
    verifier_report: dict[str, Any]
    status: str
    escalation_reason: str
    decision: str


def _mock_builder_report(task: dict[str, Any], state: RunState) -> dict[str, Any]:
    objective = task["objective"]
    failing = 2 if "force-test-fail" in objective else 0
    previous = state.get("last_failing_tests")
    progress = previous is None or failing < previous
    return {
        "agent": "builder",
        "summary": "Builder execution complete",
        "tests": {"failing": failing},
        "progress": progress,
    }


def _mock_reviewer_report(task: dict[str, Any], _state: RunState) -> dict[str, Any]:
    approve = "force-review-reject" not in task["objective"]
    return {
        "agent": "reviewer",
        "approved": approve,
        "summary": "Reviewer approval" if approve else "Reviewer requests changes",
    }


def _mock_verifier_report(task: dict[str, Any], reviewer_report: dict[str, Any]) -> dict[str, Any]:
    approved = not reviewer_report["approved"] if "force-escalate-disagreement" in task["objective"] else reviewer_report["approved"]
    return {
        "agent": "verifier",
        "approved": approved,
        "summary": "Verifier validation complete",
    }


class OverseerGraph:
    def __init__(self, codex_store: CodexStore, task_store: TaskStore, human_api: HumanAPI) -> None:
        self.codex_store = codex_store
        self.task_store = task_store
        self.human_api = human_api
        self.policy = TerminationPolicy.from_codex(codex_store.codex_root)
        self.run_log_path = codex_store.codex_root / "08_TELEMETRY" / "RUN_LOG.jsonl"

    def compile(self):
        workflow = StateGraph(RunState)
        workflow.add_node("plan_task", self.plan_task)
        workflow.add_node("run_builder", self.run_builder)
        workflow.add_node("run_reviewer", self.run_reviewer)
        workflow.add_node("run_verifier", self.run_verifier)
        workflow.add_node("decide_merge_or_escalate", self.decide_merge_or_escalate)
        workflow.add_node("update_codex", self.update_codex)

        workflow.add_edge(START, "plan_task")
        workflow.add_edge("plan_task", "run_builder")
        workflow.add_edge("run_builder", "run_reviewer")
        workflow.add_edge("run_reviewer", "run_verifier")
        workflow.add_edge("run_verifier", "decide_merge_or_escalate")
        workflow.add_conditional_edges(
            "decide_merge_or_escalate",
            lambda state: state["decision"],
            {"continue": "run_builder", "merge": "update_codex", "escalate": "update_codex"},
        )
        workflow.add_edge("update_codex", END)
        return workflow.compile()

    def _write_worker_note(self, role: str, task_id: str, message: str) -> None:
        path = self.codex_store.codex_root / "11_WORKERS" / role / "NOTES.md"
        self.codex_store.assert_write_allowed(role, path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"- {task_id}: {message}\n")

    def plan_task(self, state: RunState) -> RunState:
        return {
            **state,
            "status": "running",
            "cycle_count": state.get("cycle_count", 0),
            "verifier_disputes": state.get("verifier_disputes", 0),
            "test_failures_without_progress": state.get("test_failures_without_progress", 0),
        }

    def run_builder(self, state: RunState) -> RunState:
        report = _mock_builder_report(state["task"], state)
        self._write_worker_note("builder", state["task"]["id"], report["summary"])

        fail_count = report["tests"]["failing"]
        no_progress_failures = state.get("test_failures_without_progress", 0)
        if fail_count > 0 and not report["progress"]:
            no_progress_failures += 1
        elif report["progress"]:
            no_progress_failures = 0

        return {
            **state,
            "builder_report": report,
            "last_failing_tests": fail_count,
            "test_failures_without_progress": no_progress_failures,
        }

    def run_reviewer(self, state: RunState) -> RunState:
        report = _mock_reviewer_report(state["task"], state)
        self._write_worker_note("reviewer", state["task"]["id"], report["summary"])
        return {**state, "reviewer_report": report}

    def run_verifier(self, state: RunState) -> RunState:
        verifier = _mock_verifier_report(state["task"], state["reviewer_report"])
        self._write_worker_note("verifier", state["task"]["id"], verifier["summary"])

        disputes = state.get("verifier_disputes", 0)
        if verifier["approved"] != state["reviewer_report"]["approved"]:
            disputes += 1
        return {**state, "verifier_report": verifier, "verifier_disputes": disputes}

    def decide_merge_or_escalate(self, state: RunState) -> RunState:
        cycle_count = state.get("cycle_count", 0) + 1
        state = {**state, "cycle_count": cycle_count}

        if state["verifier_disputes"] >= self.policy.max_verifier_disputes:
            return {**state, "decision": "escalate", "status": "escalated", "escalation_reason": "reviewer/verifier disagreement threshold reached"}
        if state["test_failures_without_progress"] >= self.policy.max_test_failures_without_progress:
            return {**state, "decision": "escalate", "status": "escalated", "escalation_reason": "tests failed twice without progress"}
        if cycle_count >= self.policy.max_review_cycles:
            return {**state, "decision": "escalate", "status": "escalated", "escalation_reason": "max review cycles reached"}

        tests_ok = state["builder_report"]["tests"]["failing"] == 0
        approved = state["reviewer_report"]["approved"] and state["verifier_report"]["approved"]
        if tests_ok and approved:
            return {**state, "decision": "merge", "status": "done"}
        return {**state, "decision": "continue", "status": "running"}

    def update_codex(self, state: RunState) -> RunState:
        task = self.task_store.update_status(state["task"]["id"], state["status"])
        if state["status"] == "escalated":
            self.human_api.append_request(task, state["escalation_reason"])

        entry = {
            "task_id": task["id"],
            "status": state["status"],
            "cycle_count": state["cycle_count"],
            "verifier_disputes": state["verifier_disputes"],
            "timestamp": datetime.now(UTC).isoformat(),
            "reports": {
                "builder": state["builder_report"],
                "reviewer": state["reviewer_report"],
                "verifier": state["verifier_report"],
            },
        }
        self.codex_store.assert_write_allowed("overseer", self.run_log_path)
        with self.run_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        return {**state, "task": task}

    def run_task(self, task_id: str) -> dict[str, Any]:
        self.task_store.update_status(task_id, "running")
        graph = self.compile()
        return graph.invoke({"task": self.task_store.get_task(task_id)})
