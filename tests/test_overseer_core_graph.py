from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from overseer.codex_store import CodexStore
from overseer.execution.backend import ExecutionBackend, ExecutionRecord
from overseer.human_api import HumanAPI
from overseer.integrators.base import RunRequest, RunResult
from overseer.llm import FakeLLM
from overseer.overseer_graph import OverseerCoreGraph
from overseer.session_store import SessionStore
from overseer.task_store import TaskStore


class DummyBackend(ExecutionBackend):
    def submit(self, request):
        return request.run_id

    def status(self, run_id: str):
        raise NotImplementedError

    def list_runs(self):
        return []

    def cancel(self, run_id: str):
        raise NotImplementedError

    def reconcile(self, stale_after_seconds: int):
        return []


class FakeIntegrator:
    def __init__(self, fail_submit: bool = False) -> None:
        self.fail_submit = fail_submit
        self._counter = 0
        self._runs: dict[str, dict[str, str | int]] = {}
        self.submitted_requests: list[RunRequest] = []

    def submit(self, request: RunRequest) -> str:
        if self.fail_submit:
            raise RuntimeError("codex CLI not installed or not on PATH")
        self.submitted_requests.append(request)
        if request.run_id:
            run_id = request.run_id
        else:
            self._counter += 1
            run_id = f"run-fake-{self._counter}"
        self._runs[run_id] = {"task_id": request.task_id, "status": "queued", "calls": 0}
        return run_id

    def status(self, run_id: str) -> RunResult:
        run = self._runs[run_id]
        run["calls"] = int(run["calls"]) + 1
        if int(run["calls"]) >= 3:
            run["status"] = "done"
        elif int(run["calls"]) == 2:
            run["status"] = "running"
        return RunResult(run_id=run_id, task_id=str(run["task_id"]), status=str(run["status"]))

    def runs(self) -> list[RunResult]:
        return [
            RunResult(run_id=run_id, task_id=str(meta["task_id"]), status=str(meta["status"]))
            for run_id, meta in self._runs.items()
        ]

    def cancel(self, run_id: str) -> RunResult:
        self._runs[run_id]["status"] = "canceled"
        run = self._runs[run_id]
        return RunResult(run_id=run_id, task_id=str(run["task_id"]), status="canceled")


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "codex").mkdir(parents=True, exist_ok=True)


def _build_graph(tmp_path: Path, fail_submit: bool = False) -> tuple[OverseerCoreGraph, CodexStore]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_repo(repo)
    store = CodexStore(repo)
    store.init_structure()
    task_store = TaskStore(store)
    human_api = HumanAPI(store)
    graph = OverseerCoreGraph.build(
        codex_store=store,
        task_store=task_store,
        human_api=human_api,
        backend=DummyBackend(),
        integrator=FakeIntegrator(fail_submit=fail_submit),
        llm=FakeLLM(responses={"build": "Let me plan and execute this."}),
    )
    return graph, store


def test_session_create_message_and_persistence(tmp_path: Path) -> None:
    graph, store = _build_graph(tmp_path)
    session_id = graph.create_session()

    state = graph.submit_user_message(session_id, "hello there")
    assert state["session_id"] == session_id
    assert state["conversation_turns"]

    persisted = SessionStore(store).load_session(session_id)
    assert persisted["session_id"] == session_id
    assert (store.codex_root / "10_OVERSEER" / "sessions" / session_id / "state.json").exists()


def test_transition_to_planning_and_artifacts_written(tmp_path: Path) -> None:
    graph, store = _build_graph(tmp_path)
    session_id = graph.create_session()

    state = graph.submit_user_message(session_id, "please build feature x")
    assert state["plan"]
    assert state["mode"] in {"waiting", "executing", "reviewing"}

    plan_path = store.codex_root / "10_OVERSEER" / "sessions" / session_id / "plan.json"
    assert plan_path.exists()
    roadmap = (store.codex_root / "03_WORK" / "ROADMAP.md").read_text(encoding="utf-8")
    assert f"Session {session_id}" in roadmap


def test_execution_spawns_run_non_blocking(tmp_path: Path) -> None:
    graph, store = _build_graph(tmp_path)
    session_id = graph.create_session()

    start = time.perf_counter()
    state = graph.submit_user_message(session_id, "build this now")
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0
    assert state["active_runs"]
    run_id = next(iter(state["active_runs"]))
    run_root = store.codex_root / "08_TELEMETRY" / "runs" / run_id
    assert (run_root / "prompt_pack.md").exists()
    assert (run_root / "prompt_pack.json").exists()

    prompt_pack_json = json.loads((run_root / "prompt_pack.json").read_text(encoding="utf-8"))
    metadata = prompt_pack_json["metadata"]
    assert metadata["run_id"] == run_id
    assert "warnings" in metadata
    assert metadata["audit_paths"]["prompt_pack_json"].endswith(f"{run_id}/prompt_pack.json")
    composed_prompt = prompt_pack_json["composed_prompt"]
    assert composed_prompt.index("# System Instructions (Always Insert)") < composed_prompt.index("# Project Context")
    assert composed_prompt.index("# Project Context") < composed_prompt.index("# Run Objective")
    assert composed_prompt.index("# Run Objective") < composed_prompt.index("# Execution Constraints")


def test_graph_passes_prompt_metadata_and_composed_prompt_to_integrator(tmp_path: Path) -> None:
    graph, _ = _build_graph(tmp_path)
    assert isinstance(graph.integrator, FakeIntegrator)
    session_id = graph.create_session()

    graph.submit_user_message(session_id, "build metadata handoff")

    assert graph.integrator.submitted_requests
    request = graph.integrator.submitted_requests[0]
    assert request.instructions_payload is not None
    assert "# System Instructions (Always Insert)" in request.instructions_payload
    assert request.prompt_metadata is not None
    assert request.prompt_metadata["task_id"] == request.task_id
    assert request.prompt_metadata["run_id"] == request.run_id


def test_review_runs_also_persist_prompt_packs(tmp_path: Path) -> None:
    graph, store = _build_graph(tmp_path)
    session_id = graph.create_session()
    graph.submit_user_message(session_id, "build review prompt packs")

    state = graph.load_state(session_id)
    for _ in range(6):
        state = graph.tick(session_id)
        kinds = {meta["kind"] for meta in state.get("active_runs", {}).values()}
        if "reviewer" in kinds and "verifier" in kinds:
            break

    review_runs = [meta for meta in state.get("active_runs", {}).values() if meta.get("kind") in {"reviewer", "verifier"}]
    assert review_runs
    for run_meta in review_runs:
        run_root = store.codex_root / "08_TELEMETRY" / "runs" / str(run_meta["run_id"])
        assert (run_root / "prompt_pack.md").exists()
        assert (run_root / "prompt_pack.json").exists()


def test_graph_escalates_on_integrator_run_id_mismatch(tmp_path: Path) -> None:
    class MismatchIntegrator(FakeIntegrator):
        def submit(self, request: RunRequest) -> str:
            super().submit(request)
            return "run-mismatch"

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_repo(repo)
    store = CodexStore(repo)
    store.init_structure()
    task_store = TaskStore(store)
    human_api = HumanAPI(store)
    graph = OverseerCoreGraph.build(
        codex_store=store,
        task_store=task_store,
        human_api=human_api,
        backend=DummyBackend(),
        integrator=MismatchIntegrator(),
        llm=FakeLLM(responses={"build": "Plan it"}),
    )

    session_id = graph.create_session()
    state = graph.submit_user_message(session_id, "build mismatch")
    assert state["mode"] == "escalated"
    assert "unexpected run id" in str(state["escalation_reason"])


def test_prompt_pack_persisted_before_submit(tmp_path: Path) -> None:
    class AuditingIntegrator:
        def __init__(self, store: CodexStore) -> None:
            self.store = store
            self.checked = False
            self._runs: dict[str, dict[str, Any]] = {}

        def submit(self, request: RunRequest) -> str:
            assert request.run_id is not None
            run_root = self.store.codex_root / "08_TELEMETRY" / "runs" / request.run_id
            assert (run_root / "prompt_pack.md").exists()
            assert (run_root / "prompt_pack.json").exists()
            self.checked = True
            self._runs[request.run_id] = {"task_id": request.task_id, "status": "queued", "calls": 0}
            return request.run_id

        def status(self, run_id: str) -> RunResult:
            return RunResult(run_id=run_id, task_id=str(self._runs[run_id]["task_id"]), status="queued")

        def runs(self) -> list[RunResult]:
            return []

        def cancel(self, run_id: str) -> RunResult:
            return RunResult(run_id=run_id, task_id=str(self._runs[run_id]["task_id"]), status="canceled")

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_repo(repo)
    store = CodexStore(repo)
    store.init_structure()
    task_store = TaskStore(store)
    human_api = HumanAPI(store)
    integrator = AuditingIntegrator(store)
    graph = OverseerCoreGraph.build(
        codex_store=store,
        task_store=task_store,
        human_api=human_api,
        backend=DummyBackend(),
        integrator=integrator,  # type: ignore[arg-type]
        llm=FakeLLM(responses={"build": "Plan it"}),
    )

    session_id = graph.create_session()
    graph.submit_user_message(session_id, "build pre submit audit")
    assert integrator.checked is True


def test_polling_completes_runs_and_decides(tmp_path: Path) -> None:
    graph, _ = _build_graph(tmp_path)
    session_id = graph.create_session()
    graph.submit_user_message(session_id, "build backend change")

    for _ in range(8):
        state = graph.tick(session_id)

    assert state["mode"] in {"idle", "waiting", "reviewing"}
    assert any(step["status"] == "done" for step in state["plan"])


def test_escalation_writes_human_queue(tmp_path: Path) -> None:
    graph, store = _build_graph(tmp_path, fail_submit=True)
    session_id = graph.create_session()

    state = graph.submit_user_message(session_id, "build with missing codex")
    assert state["mode"] == "escalated"

    queue = (store.codex_root / "04_HUMAN_API" / "HUMAN_QUEUE.md").read_text(encoding="utf-8")
    assert "HUMAN_REQUEST:" in queue or "[pending]" in queue


def test_session_resume_and_worker_notes_written(tmp_path: Path) -> None:
    graph, store = _build_graph(tmp_path)
    session_id = graph.create_session()
    graph.submit_user_message(session_id, "build resume flow")

    reloaded = graph.load_state(session_id)
    assert reloaded["session_id"] == session_id
    assert reloaded["plan"]

    notes = (store.codex_root / "11_WORKERS" / "builder" / "NOTES.md").read_text(encoding="utf-8")
    assert "created initial implementation plan" in notes


def test_poll_uses_backend_status(tmp_path: Path) -> None:
    class CountingBackend(DummyBackend):
        def __init__(self) -> None:
            self.calls = 0

        def status(self, run_id: str):
            self.calls += 1
            return ExecutionRecord(
                run_id=run_id,
                task_id="task-x",
                status="done",
                command=[],
                cwd=".",
                stdout_log="",
                stderr_log="",
                meta_path="",
                lock_path="",
                created_at="now",
            )

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_repo(repo)
    store = CodexStore(repo)
    store.init_structure()
    task_store = TaskStore(store)
    human_api = HumanAPI(store)
    backend = CountingBackend()
    graph = OverseerCoreGraph.build(
        codex_store=store,
        task_store=task_store,
        human_api=human_api,
        backend=backend,
        integrator=FakeIntegrator(),
        llm=FakeLLM(responses={"build": "Plan it"}),
    )

    session_id = graph.create_session()
    graph.submit_user_message(session_id, "build backend polling")
    graph.tick(session_id)
    assert backend.calls >= 1
