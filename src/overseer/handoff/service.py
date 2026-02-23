from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from overseer.codex_store import CodexStore
from overseer.fs import atomic_write_text
from overseer.handoff.checkpoint import HandoffCheckpoint, HandoffCheckpointStore
from overseer.handoff.lease import SessionLease, SessionLeaseStore
from overseer.handoff.pressure import PressureAssessment, PressureInputs, PressurePolicy, assess_pressure
from overseer.locks import file_lock
from overseer.session_store import SessionStore


@dataclass(frozen=True)
class HandoffRecommendation:
    session_id: str
    lease_epoch: int
    band: str
    assessment: PressureAssessment
    reason: str


@dataclass(frozen=True)
class HandoffStatus:
    session_id: str
    instance_id: str
    lease: dict[str, object]
    latest_assessment: dict[str, object] | None
    active_handoff: dict[str, object] | None


class HandoffService:
    def __init__(self, codex_store: CodexStore, session_store: SessionStore, instance_id: str | None = None) -> None:
        self.codex_store = codex_store
        self.session_store = session_store
        self.instance_id = instance_id or f"ovr-{uuid4().hex[:12]}"
        self.lease_store = SessionLeaseStore(codex_store)
        self.checkpoints = HandoffCheckpointStore(codex_store)
        self._policy = self._load_policy()

    def _load_policy(self) -> dict[str, object]:
        path = self.codex_store.codex_root / "10_OVERSEER" / "HANDOFF_POLICY.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @property
    def pressure_policy(self) -> PressurePolicy:
        pressure = dict(self._policy.get("pressure", {})) if isinstance(self._policy.get("pressure"), dict) else {}
        return PressurePolicy(
            state_bytes_budget=int(pressure.get("state_bytes_budget", 96_000)),
            conversation_turn_budget=int(pressure.get("conversation_turn_budget", 120)),
            conversation_bytes_budget=int(pressure.get("conversation_bytes_budget", 64_000)),
            observe_threshold=float(pressure.get("observe_threshold", 0.65)),
            switch_threshold=float(pressure.get("switch_threshold", 0.85)),
        )

    def _checkpoint_policy(self) -> dict[str, int]:
        checkpoint = dict(self._policy.get("checkpoint", {})) if isinstance(self._policy.get("checkpoint"), dict) else {}
        return {
            "tail_turns": int(checkpoint.get("tail_turns", 12)),
            "max_latest_response_chars": int(checkpoint.get("max_latest_response_chars", 300)),
            "max_plan_items": int(checkpoint.get("max_plan_items", 20)),
            "max_active_runs": int(checkpoint.get("max_active_runs", 20)),
        }

    def ensure_lease(self, session_id: str, owner_instance_id: str | None = None) -> SessionLease:
        return self.lease_store.ensure_lease(session_id, owner_instance_id or self.instance_id)

    def assert_primary_owner(self, session_id: str, owner_instance_id: str | None = None) -> None:
        self.lease_store.assert_primary_owner(session_id, owner_instance_id or self.instance_id)

    def assess_pressure(self, session_id: str) -> PressureAssessment:
        state = self.session_store.load_session(session_id)
        state_text = json.dumps(state, sort_keys=True)
        turns = list(state.get("conversation_turns", []))
        conversation_text = json.dumps(turns, sort_keys=True)
        inputs = PressureInputs(
            session_state_bytes=len(state_text.encode("utf-8")),
            conversation_turn_count=len(turns),
            conversation_bytes=len(conversation_text.encode("utf-8")),
            active_run_count=len(state.get("active_runs", {})),
            plan_step_count=len(state.get("plan", [])),
        )
        assessment = assess_pressure(inputs, self.pressure_policy)
        self._write_latest_assessment(session_id, assessment)
        return assessment

    def recommend_handoff(self, session_id: str) -> HandoffRecommendation | None:
        lease = self.ensure_lease(session_id, self.instance_id)
        assessment = self.assess_pressure(session_id)
        if assessment.band == "normal":
            return None
        if assessment.band == "observe_recommended" and lease.active_handoff_id:
            return None
        if assessment.band == "switch_recommended":
            if not lease.active_handoff_id or not lease.observer_instance_ids:
                return None
        if not self._claim_recommendation_marker(session_id, lease.lease_epoch, assessment.band):
            return None
        return HandoffRecommendation(
            session_id=session_id,
            lease_epoch=lease.lease_epoch,
            band=assessment.band,
            assessment=assessment,
            reason=", ".join(assessment.trigger_reasons),
        )

    def prepare_handoff(self, session_id: str, owner_instance_id: str) -> HandoffCheckpoint:
        lease = self.lease_store.ensure_lease(session_id, owner_instance_id)
        if lease.owner_instance_id != owner_instance_id:
            raise PermissionError(f"session lease owned by {lease.owner_instance_id}")
        handoff_id = lease.active_handoff_id or f"handoff-{uuid4().hex[:12]}"
        lease = self.lease_store.set_handoff_prepared(session_id, handoff_id, owner_instance_id)
        checkpoint = self._write_checkpoint(session_id, handoff_id, owner_instance_id, phase="prepared", lease=lease)
        self.checkpoints.append_event(
            session_id, handoff_id, "handoff_prepared", {"owner_instance_id": owner_instance_id, "lease_epoch": lease.lease_epoch}
        )
        self._append_session_event(session_id, "handoff_prepared", {"handoff_id": handoff_id, "owner_instance_id": owner_instance_id})
        return checkpoint

    def register_observer(self, session_id: str, handoff_id: str, observer_instance_id: str) -> HandoffCheckpoint:
        lease = self.lease_store.register_observer(session_id, handoff_id, observer_instance_id)
        checkpoint = self._write_checkpoint(
            session_id, handoff_id, lease.owner_instance_id, phase="observing", lease=lease
        )
        self.checkpoints.append_event(
            session_id, handoff_id, "handoff_observer_registered", {"observer_instance_id": observer_instance_id}
        )
        self._append_session_event(
            session_id, "handoff_observer_registered", {"handoff_id": handoff_id, "observer_instance_id": observer_instance_id}
        )
        return checkpoint

    def switch_handoff(
        self, session_id: str, handoff_id: str, from_owner_instance_id: str, to_owner_instance_id: str
    ) -> HandoffCheckpoint:
        lease = self.lease_store.transfer_lease(session_id, handoff_id, from_owner_instance_id, to_owner_instance_id)
        checkpoint = self._write_checkpoint(session_id, handoff_id, to_owner_instance_id, phase="switched", lease=lease)
        self.lease_store.mark_advisor(session_id, from_owner_instance_id, handoff_id)
        self.checkpoints.append_event(
            session_id,
            handoff_id,
            "handoff_switch_completed",
            {
                "from_owner_instance_id": from_owner_instance_id,
                "to_owner_instance_id": to_owner_instance_id,
                "lease_epoch": lease.lease_epoch,
            },
        )
        self._append_session_event(
            session_id,
            "handoff_switch_completed",
            {"handoff_id": handoff_id, "from_owner_instance_id": from_owner_instance_id, "to_owner_instance_id": to_owner_instance_id},
        )
        return checkpoint

    def append_observer_note(self, session_id: str, handoff_id: str, observer_instance_id: str, text: str) -> None:
        lease = self.lease_store.read_lease(session_id)
        if observer_instance_id not in lease.observer_instance_ids:
            raise PermissionError(f"observer not registered: {observer_instance_id}")
        self.checkpoints.append_note(session_id, handoff_id, "observer", observer_instance_id, text)
        self.checkpoints.append_event(
            session_id, handoff_id, "handoff_observer_note", {"observer_instance_id": observer_instance_id}
        )
        self._append_session_event(session_id, "handoff_observer_note", {"handoff_id": handoff_id, "observer_instance_id": observer_instance_id})

    def append_advisor_note(self, session_id: str, handoff_id: str, prior_owner_instance_id: str, text: str) -> None:
        self.checkpoints.append_note(session_id, handoff_id, "advisor", prior_owner_instance_id, text)
        self.checkpoints.append_event(
            session_id, handoff_id, "handoff_advisor_note", {"prior_owner_instance_id": prior_owner_instance_id}
        )
        self._append_session_event(
            session_id, "handoff_advisor_note", {"handoff_id": handoff_id, "prior_owner_instance_id": prior_owner_instance_id}
        )

    def abort_handoff(self, session_id: str, handoff_id: str, owner_instance_id: str) -> HandoffCheckpoint:
        lease = self.lease_store.abort_handoff(session_id, handoff_id, owner_instance_id)
        checkpoint = self._write_checkpoint(session_id, handoff_id, owner_instance_id, phase="closed", lease=lease)
        self.checkpoints.append_event(session_id, handoff_id, "handoff_aborted", {"owner_instance_id": owner_instance_id})
        self._append_session_event(session_id, "handoff_aborted", {"handoff_id": handoff_id, "owner_instance_id": owner_instance_id})
        return checkpoint

    def status(self, session_id: str) -> HandoffStatus:
        self.session_store.load_session(session_id)
        lease = self.lease_store.ensure_lease(session_id, self.instance_id)
        latest_assessment = self._read_latest_assessment(session_id)
        active_handoff = None
        if lease.active_handoff_id:
            try:
                active_handoff = self.checkpoints.load_checkpoint(session_id, lease.active_handoff_id)
            except FileNotFoundError:
                active_handoff = {"handoff_id": lease.active_handoff_id, "missing": True}
        return HandoffStatus(
            session_id=session_id,
            instance_id=self.instance_id,
            lease=asdict(lease),
            latest_assessment=latest_assessment,
            active_handoff=active_handoff,
        )

    def _write_latest_assessment(self, session_id: str, assessment: PressureAssessment) -> None:
        path = self._handoff_session_root(session_id) / "latest_pressure_assessment.json"
        self.codex_store.assert_write_allowed("overseer", path)
        atomic_write_text(path, json.dumps(asdict(assessment), indent=2, sort_keys=True) + "\n")

    def _read_latest_assessment(self, session_id: str) -> dict[str, object] | None:
        path = self._handoff_session_root(session_id) / "latest_pressure_assessment.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _recommendations_marker_path(self, session_id: str) -> Path:
        return self._handoff_session_root(session_id) / "recommendation_markers.json"

    def _claim_recommendation_marker(self, session_id: str, lease_epoch: int, band: str) -> bool:
        path = self._recommendations_marker_path(session_id)
        lock = self.codex_store.codex_root / "10_OVERSEER" / "locks" / f"handoff-reco-{session_id}.lock"
        self.codex_store.assert_write_allowed("overseer", path)
        path.parent.mkdir(parents=True, exist_ok=True)
        key = f"{lease_epoch}:{band}"
        with file_lock(lock):
            markers: dict[str, str] = {}
            if path.exists():
                markers = json.loads(path.read_text(encoding="utf-8"))
            if key in markers:
                return False
            markers[key] = self.instance_id
            atomic_write_text(path, json.dumps(markers, indent=2, sort_keys=True) + "\n")
            return True

    def _handoff_session_root(self, session_id: str) -> Path:
        return self.codex_store.codex_root / "08_TELEMETRY" / "sessions" / session_id

    def _write_checkpoint(
        self,
        session_id: str,
        handoff_id: str,
        created_by_instance_id: str,
        *,
        phase: str,
        lease: SessionLease,
    ) -> HandoffCheckpoint:
        state = self.session_store.load_session(session_id)
        assessment = self.assess_pressure(session_id)
        policy = self._checkpoint_policy()
        turns = list(state.get("conversation_turns", []))
        tail_turns = turns[-policy["tail_turns"] :]
        older_count = max(0, len(turns) - len(tail_turns))
        older_bytes = len(json.dumps(turns[:-policy["tail_turns"]], sort_keys=True).encode("utf-8")) if older_count else 0
        active_runs = state.get("active_runs", {})
        active_runs_items = sorted(active_runs.items())[: policy["max_active_runs"]]
        plan_items = list(state.get("plan", []))[: policy["max_plan_items"]]
        latest_response = str(state.get("latest_response", ""))
        warnings: list[str] = []
        if len(str(state.get("latest_response", ""))) > policy["max_latest_response_chars"]:
            latest_response = latest_response[: policy["max_latest_response_chars"]]
            warnings.append("latest_response truncated")
        artifact_paths = self._collect_artifact_paths(session_id, active_runs_items)
        recommended_next_actions = self._recommended_next_actions(lease, assessment)
        payload: dict[str, object] = {
            "protocol_version": 1,
            "handoff_id": handoff_id,
            "session_id": session_id,
            "created_by_instance_id": created_by_instance_id,
            "phase": phase,
            "pressure_assessment": asdict(assessment),
            "session_snapshot": {
                "mode": state.get("mode"),
                "selected_step_id": state.get("selected_step_id"),
                "pending_human_requests": list(state.get("pending_human_requests", [])),
                "plan": [
                    {"id": s.get("id"), "status": s.get("status"), "title": s.get("title")}
                    for s in plan_items
                ],
                "active_runs": [
                    {"run_id": rid, **(meta if isinstance(meta, dict) else {})}
                    for rid, meta in active_runs_items
                ],
                "latest_response": latest_response,
            },
            "conversation_summary": {
                "tail_turns": tail_turns,
                "older_turn_count": older_count,
                "older_turn_bytes": older_bytes,
                "total_turn_count": len(turns),
            },
            "artifact_paths": artifact_paths,
            "recommended_next_actions": recommended_next_actions,
            "warnings": warnings,
        }
        return self.checkpoints.create_or_update(session_id=session_id, handoff_id=handoff_id, payload=payload, lease=lease)

    def _collect_artifact_paths(self, session_id: str, active_runs_items: list[tuple[str, object]]) -> list[str]:
        paths = [
            str(Path("codex/10_OVERSEER/sessions") / session_id / "state.json"),
            str(Path("codex/10_OVERSEER/sessions") / session_id / "transcript.jsonl"),
            str(Path("codex/08_TELEMETRY/sessions") / session_id / "events.jsonl"),
        ]
        # include prompt-pack paths for active runs when present in state
        for run_id, _meta in active_runs_items:
            paths.append(str(Path("codex/08_TELEMETRY/runs") / run_id / "prompt_pack.md"))
            paths.append(str(Path("codex/08_TELEMETRY/runs") / run_id / "prompt_pack.json"))
        return sorted(dict.fromkeys(paths))

    def _recommended_next_actions(self, lease: SessionLease, assessment: PressureAssessment) -> list[str]:
        if assessment.band == "switch_recommended":
            if lease.active_handoff_id and lease.observer_instance_ids:
                return [f"Switch handoff {lease.active_handoff_id} to a registered observer"]
            return ["Prepare handoff and register an observer Overseer"]
        if assessment.band == "observe_recommended":
            if lease.active_handoff_id:
                return [f"Register observer for handoff {lease.active_handoff_id}"]
            return ["Prepare a handoff checkpoint for a successor Overseer observer"]
        return []

    def _append_session_event(self, session_id: str, event_type: str, payload: dict[str, object]) -> None:
        path = self.codex_store.codex_root / "08_TELEMETRY" / "sessions" / session_id / "events.jsonl"
        lock = self.codex_store.codex_root / "10_OVERSEER" / "locks" / f"session-events-{session_id}.lock"
        self.codex_store.assert_write_allowed("overseer", path)
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "payload": payload,
        }
        with file_lock(lock):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
