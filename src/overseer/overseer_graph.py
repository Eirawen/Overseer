from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from overseer.codex_store import CodexStore
from overseer.execution.backend import TERMINAL_STATUSES, ExecutionBackend
from overseer.human_api import HumanAPI
from overseer.integrators.base import RunRequest
from overseer.integrators.codex import CodexIntegrator
from overseer.llm import LLMAdapter, Message
from overseer.locks import file_lock
from overseer.session_store import SessionStore
from overseer.task_store import TaskStore

Mode = Literal[
    "conversation",
    "planning",
    "executing",
    "waiting",
    "reviewing",
    "escalated",
    "idle",
]


class PlanStep(TypedDict, total=False):
    id: str
    title: str
    status: str
    task_id: str


class RunMeta(TypedDict, total=False):
    run_id: str
    step_id: str
    kind: str
    task_id: str
    status: str


class OverseerState(TypedDict, total=False):
    session_id: str
    mode: Mode
    conversation_turns: list[dict[str, str]]
    plan: list[PlanStep]
    active_runs: dict[str, RunMeta]
    last_user_message: str
    pending_human_requests: list[str]
    next_actions: list[str]
    autonomy_dial: str
    termination_policy: str
    latest_response: str
    command: str
    should_plan: bool
    selected_step_id: str | None
    escalation_reason: str | None


@dataclass
class OverseerCoreGraph:
    codex_store: CodexStore
    task_store: TaskStore
    human_api: HumanAPI
    backend: ExecutionBackend
    integrator: CodexIntegrator
    llm: LLMAdapter
    session_store: SessionStore

    def __post_init__(self) -> None:
        self._events_root = self.codex_store.codex_root / "08_TELEMETRY" / "sessions"
        self._events_root.mkdir(parents=True, exist_ok=True)
        self.graph = self._compile()

    @classmethod
    def build(
        cls,
        codex_store: CodexStore,
        task_store: TaskStore,
        human_api: HumanAPI,
        backend: ExecutionBackend,
        integrator: CodexIntegrator,
        llm: LLMAdapter,
    ) -> "OverseerCoreGraph":
        return cls(
            codex_store=codex_store,
            task_store=task_store,
            human_api=human_api,
            backend=backend,
            integrator=integrator,
            llm=llm,
            session_store=SessionStore(codex_store),
        )

    def create_session(self) -> str:
        return self.session_store.create_session()

    def list_sessions(self) -> list[str]:
        return self.session_store.list_sessions()

    def load_state(self, session_id: str) -> OverseerState:
        state = self.session_store.load_session(session_id)
        state.setdefault("autonomy_dial", self._read_file("01_PROJECT/OPERATING_MODE.md"))
        state.setdefault("termination_policy", self._read_file("05_AGENTS/TERMINATION.md"))
        return state

    def submit_user_message(self, session_id: str, text: str) -> OverseerState:
        state = self.load_state(session_id)
        state["command"] = "message"
        state["last_user_message"] = text
        return self.graph.invoke(state)

    def tick(self, session_id: str) -> OverseerState:
        state = self.load_state(session_id)
        state["command"] = "tick"
        state["last_user_message"] = ""
        return self.graph.invoke(state)

    def _compile(self):
        workflow = StateGraph(OverseerState)
        workflow.add_node("ingest_user_message", self.ingest_user_message)
        workflow.add_node("converse", self.converse)
        workflow.add_node("maybe_transition_to_planning", self.maybe_transition_to_planning)
        workflow.add_node("plan_project", self.plan_project)
        workflow.add_node("select_next_step", self.select_next_step)
        workflow.add_node("spawn_builder_run", self.spawn_builder_run)
        workflow.add_node("poll_runs", self.poll_runs)
        workflow.add_node("spawn_review_runs", self.spawn_review_runs)
        workflow.add_node("decide_merge_retry_escalate", self.decide_merge_retry_escalate)
        workflow.add_node("persist_state", self.persist_state)
        workflow.add_node("emit_response", self.emit_response)

        workflow.add_edge(START, "ingest_user_message")
        workflow.add_edge("ingest_user_message", "converse")
        workflow.add_edge("converse", "maybe_transition_to_planning")
        workflow.add_conditional_edges(
            "maybe_transition_to_planning",
            self._route_after_planning_check,
            {
                "plan": "plan_project",
                "execute": "select_next_step",
                "poll": "poll_runs",
            },
        )
        workflow.add_edge("plan_project", "select_next_step")
        workflow.add_conditional_edges(
            "select_next_step",
            self._route_next_step,
            {
                "spawn": "spawn_builder_run",
                "poll": "poll_runs",
                "emit": "persist_state",
            },
        )
        workflow.add_edge("spawn_builder_run", "persist_state")
        workflow.add_edge("poll_runs", "spawn_review_runs")
        workflow.add_edge("spawn_review_runs", "decide_merge_retry_escalate")
        workflow.add_edge("decide_merge_retry_escalate", "persist_state")
        workflow.add_edge("persist_state", "emit_response")
        workflow.add_edge("emit_response", END)
        return workflow.compile()

    def ingest_user_message(self, state: OverseerState) -> OverseerState:
        turns = list(state.get("conversation_turns", []))
        if state.get("command") == "message" and state.get("last_user_message"):
            turns.append({"role": "user", "content": state["last_user_message"]})
        return {
            **state,
            "conversation_turns": turns,
            "autonomy_dial": state.get("autonomy_dial", self._read_file("01_PROJECT/OPERATING_MODE.md")),
            "termination_policy": state.get("termination_policy", self._read_file("05_AGENTS/TERMINATION.md")),
        }

    def converse(self, state: OverseerState) -> OverseerState:
        message = state.get("last_user_message", "")
        if not message:
            text = "Tick processed: polling active runs and pending reviews."
        else:
            text = self.llm.generate(
                "You are Overseer. Be concise and actionable.",
                [Message(role=t["role"], content=t["content"]) for t in state.get("conversation_turns", [])],
            )
        turns = list(state.get("conversation_turns", []))
        turns.append({"role": "assistant", "content": text})
        return {**state, "conversation_turns": turns, "latest_response": text}

    def maybe_transition_to_planning(self, state: OverseerState) -> OverseerState:
        message = state.get("last_user_message", "").lower()
        should_plan = any(token in message for token in ["plan", "start", "build", "implement"])
        if state.get("command") == "tick":
            should_plan = False
        mode: Mode = state.get("mode", "conversation")
        if should_plan and mode in {"conversation", "idle"}:
            mode = "planning"
        elif state.get("active_runs"):
            mode = "waiting"
        return {**state, "should_plan": should_plan, "mode": mode}

    def plan_project(self, state: OverseerState) -> OverseerState:
        if state.get("plan"):
            return state
        objective = state.get("last_user_message", "No objective provided")
        steps = [
            {"id": "step-1", "title": f"Implement: {objective}", "status": "pending"},
            {"id": "step-2", "title": "Review and verify changes", "status": "pending"},
        ]
        self._write_plan_artifacts(state["session_id"], steps)
        self._append_worker_note("builder", state["session_id"], "created initial implementation plan")
        self._emit_event(state["session_id"], "plan_created", {"steps": steps})
        return {**state, "plan": steps, "mode": "executing"}

    def select_next_step(self, state: OverseerState) -> OverseerState:
        if state.get("active_runs"):
            return {**state, "selected_step_id": None}
        for step in state.get("plan", []):
            if step["status"] == "pending":
                step["status"] = "in_progress"
                return {**state, "selected_step_id": step["id"], "mode": "executing"}
        return {**state, "mode": "idle", "selected_step_id": None}

    def spawn_builder_run(self, state: OverseerState) -> OverseerState:
        step_id = state.get("selected_step_id")
        if not step_id:
            return state
        plan_step = next(s for s in state["plan"] if s["id"] == step_id)
        task = self.task_store.add_task(plan_step["title"])
        plan_step["task_id"] = task["id"]
        try:
            run_id = self.integrator.submit(RunRequest(task_id=task["id"], objective=plan_step["title"]))
        except RuntimeError as exc:
            self._escalate(state, f"builder spawn failed: {exc}", task=task)
            return {**state, "mode": "escalated", "escalation_reason": str(exc)}
        active = dict(state.get("active_runs", {}))
        active[run_id] = {
            "run_id": run_id,
            "step_id": step_id,
            "task_id": task["id"],
            "kind": "builder",
            "status": "queued",
        }
        self.task_store.update_status(task["id"], "running", run_id=run_id)
        self._append_worker_note("builder", task["id"], f"spawned builder run {run_id}")
        self._emit_event(state["session_id"], "run_spawned", active[run_id])
        return {
            **state,
            "active_runs": active,
            "mode": "waiting",
            "next_actions": ["poll run status with /tick"],
            "latest_response": f"Spawned builder run {run_id}. I will keep chatting while it runs.",
        }

    def poll_runs(self, state: OverseerState) -> OverseerState:
        active = dict(state.get("active_runs", {}))
        for run_id, run_meta in list(active.items()):
            try:
                status = self.backend.status(run_id).status
            except (NotImplementedError, FileNotFoundError):
                status = self.integrator.status(run_id).status
            run_meta["status"] = status
            if status in TERMINAL_STATUSES:
                self._emit_event(state["session_id"], "run_terminal", {"run_id": run_id, "status": status})
        return {**state, "active_runs": active}

    def spawn_review_runs(self, state: OverseerState) -> OverseerState:
        active = dict(state.get("active_runs", {}))
        for run in list(active.values()):
            if run.get("kind") != "builder" or run.get("status") != "done":
                continue
            step_id = run["step_id"]
            existing_review = [r for r in active.values() if r.get("step_id") == step_id and r.get("kind") != "builder"]
            if existing_review:
                continue
            for role in ("reviewer", "verifier"):
                task = self.task_store.add_task(f"{role} validation for {step_id}")
                try:
                    review_run = self.integrator.submit(RunRequest(task_id=task["id"], objective=f"{role} validate {step_id}"))
                except RuntimeError as exc:
                    self._escalate(state, f"{role} spawn failed: {exc}", task=task)
                    return {**state, "mode": "escalated", "escalation_reason": str(exc)}
                active[review_run] = {
                    "run_id": review_run,
                    "step_id": step_id,
                    "task_id": task["id"],
                    "kind": role,
                    "status": "queued",
                }
                self._append_worker_note(role, task["id"], f"spawned {role} run {review_run}")
                self._emit_event(state["session_id"], "run_spawned", active[review_run])
            state["mode"] = "reviewing"
        return {**state, "active_runs": active}

    def decide_merge_retry_escalate(self, state: OverseerState) -> OverseerState:
        active = dict(state.get("active_runs", {}))
        plan = state.get("plan", [])
        pending_human = list(state.get("pending_human_requests", []))

        for step in plan:
            step_runs = [r for r in active.values() if r.get("step_id") == step["id"]]
            if not step_runs:
                continue
            if any(r.get("status") in {"queued", "running", "canceling"} for r in step_runs):
                continue
            if any(r.get("status") in {"failed", "canceled"} for r in step_runs):
                self._escalate(state, f"step {step['id']} had failed run(s)")
                pending_human = [req.request_id for req in self.human_api.list_requests() if req.status == "pending"]
                step["status"] = "escalated"
                return {
                    **state,
                    "mode": "escalated",
                    "plan": plan,
                    "pending_human_requests": pending_human,
                    "latest_response": "Escalated to human queue due to failed runs.",
                }
            if all(r.get("status") == "done" for r in step_runs):
                step["status"] = "done"
                for run_id in [r["run_id"] for r in step_runs]:
                    active.pop(run_id, None)
                self._append_memory(f"Session {state['session_id']} completed {step['id']}: {step['title']}")
                self._emit_event(state["session_id"], "step_done", {"step_id": step["id"]})

        pending_human = [req.request_id for req in self.human_api.list_requests() if req.status == "pending"]
        mode: Mode = "idle" if plan and all(s["status"] == "done" for s in plan) else "waiting"
        return {**state, "plan": plan, "active_runs": active, "mode": mode, "pending_human_requests": pending_human}

    def persist_state(self, state: OverseerState) -> OverseerState:
        self.session_store.save_session(state)
        self._emit_event(
            state["session_id"],
            "state_saved",
            {
                "mode": state.get("mode"),
                "active_run_count": len(state.get("active_runs", {})),
                "pending_human_requests": state.get("pending_human_requests", []),
            },
        )
        return state

    def emit_response(self, state: OverseerState) -> OverseerState:
        if state.get("pending_human_requests"):
            suffix = f" Pending human requests: {', '.join(state['pending_human_requests'])}."
        else:
            suffix = ""
        response = state.get("latest_response", "State updated.") + suffix
        turns = list(state.get("conversation_turns", []))
        if not turns or turns[-1].get("role") != "assistant" or turns[-1].get("content") != response:
            turns.append({"role": "assistant", "content": response})
        return {**state, "conversation_turns": turns, "latest_response": response}

    def _route_after_planning_check(self, state: OverseerState) -> str:
        if state.get("command") == "tick":
            return "poll"
        if state.get("should_plan"):
            return "plan"
        if state.get("active_runs"):
            return "poll"
        return "execute"

    def _route_next_step(self, state: OverseerState) -> str:
        if state.get("selected_step_id"):
            return "spawn"
        if state.get("active_runs"):
            return "poll"
        return "emit"

    def _read_file(self, relative: str) -> str:
        path = self.codex_store.codex_root / relative
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")[:2000]

    def _emit_event(self, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
        path = self._events_root / session_id / "events.jsonl"
        lock = self.codex_store.codex_root / "10_OVERSEER" / "locks" / f"session-events-{session_id}.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.codex_store.assert_write_allowed("overseer", path)
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "payload": payload,
        }
        with file_lock(lock):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")

    def _append_worker_note(self, role: str, task_id: str, text: str) -> None:
        path = self.codex_store.codex_root / "11_WORKERS" / role / "NOTES.md"
        self.codex_store.assert_write_allowed(role, path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"- {task_id}: {text}\n")

    def _append_memory(self, line: str) -> None:
        path = self.codex_store.codex_root / "02_MEMORY" / "DECISION_LOG.md"
        self.codex_store.assert_write_allowed("overseer", path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"- {line}\n")

    def _write_plan_artifacts(self, session_id: str, steps: list[PlanStep]) -> None:
        roadmap = self.codex_store.codex_root / "03_WORK" / "ROADMAP.md"
        self.codex_store.assert_write_allowed("overseer", roadmap)
        rendered = [f"## Session {session_id}", ""]
        for step in steps:
            rendered.append(f"- [{step['status']}] {step['id']}: {step['title']}")
        with roadmap.open("a", encoding="utf-8") as handle:
            handle.write("\n" + "\n".join(rendered) + "\n")

        session_plan = self.codex_store.codex_root / "10_OVERSEER" / "sessions" / session_id / "plan.json"
        session_plan.parent.mkdir(parents=True, exist_ok=True)
        self.codex_store.assert_write_allowed("overseer", session_plan)
        session_plan.write_text(json.dumps(steps, indent=2) + "\n", encoding="utf-8")

    def _escalate(self, state: OverseerState, reason: str, task: dict[str, Any] | None = None) -> None:
        selected = task or {"id": f"session-{state['session_id']}"}
        self.human_api.append_request(selected, reason)
        self._emit_event(state["session_id"], "escalated", {"reason": reason})
