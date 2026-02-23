from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from overseer.codex_store import CodexStore
from overseer.fs import atomic_write_text
from overseer.handoff.lease import SessionLease
from overseer.locks import file_lock


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class HandoffCheckpoint:
    handoff_id: str
    session_id: str
    root: Path
    checkpoint_json_path: Path
    handoff_brief_path: Path
    observer_notes_path: Path
    advisor_notes_path: Path
    events_path: Path
    lease_snapshot_path: Path
    payload: dict[str, object]


class HandoffCheckpointStore:
    def __init__(self, codex_store: CodexStore) -> None:
        self.codex_store = codex_store
        self._locks_root = codex_store.codex_root / "10_OVERSEER" / "locks"
        self._locks_root.mkdir(parents=True, exist_ok=True)

    def handoff_root(self, session_id: str, handoff_id: str) -> Path:
        return self.codex_store.codex_root / "08_TELEMETRY" / "sessions" / session_id / "handoffs" / handoff_id

    def create_or_update(
        self,
        *,
        session_id: str,
        handoff_id: str,
        payload: dict[str, object],
        lease: SessionLease,
    ) -> HandoffCheckpoint:
        root = self.handoff_root(session_id, handoff_id)
        checkpoint_json_path = root / "checkpoint.json"
        handoff_brief_path = root / "handoff_brief.md"
        observer_notes_path = root / "observer_notes.md"
        advisor_notes_path = root / "advisor_notes.md"
        events_path = root / "events.jsonl"
        lease_snapshot_path = root / "lease_snapshot.json"
        for path in [
            checkpoint_json_path,
            handoff_brief_path,
            observer_notes_path,
            advisor_notes_path,
            events_path,
            lease_snapshot_path,
        ]:
            self.codex_store.assert_write_allowed("overseer", path)
        root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(checkpoint_json_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        atomic_write_text(handoff_brief_path, self._render_brief(payload))
        if not observer_notes_path.exists():
            atomic_write_text(observer_notes_path, "# Observer Notes\n")
        if not advisor_notes_path.exists():
            atomic_write_text(advisor_notes_path, "# Advisor Notes\n")
        atomic_write_text(lease_snapshot_path, json.dumps(lease.__dict__, indent=2, sort_keys=True) + "\n")
        return HandoffCheckpoint(
            handoff_id=handoff_id,
            session_id=session_id,
            root=root,
            checkpoint_json_path=checkpoint_json_path,
            handoff_brief_path=handoff_brief_path,
            observer_notes_path=observer_notes_path,
            advisor_notes_path=advisor_notes_path,
            events_path=events_path,
            lease_snapshot_path=lease_snapshot_path,
            payload=payload,
        )

    def append_event(self, session_id: str, handoff_id: str, event_type: str, payload: dict[str, object]) -> None:
        path = self.handoff_root(session_id, handoff_id) / "events.jsonl"
        lock = self._locks_root / f"handoff-events-{session_id}-{handoff_id}.lock"
        self.codex_store.assert_write_allowed("overseer", path)
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {"at": _utc_now(), "type": event_type, "payload": payload}
        with file_lock(lock):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")

    def append_note(self, session_id: str, handoff_id: str, role: str, author_instance_id: str, text: str) -> Path:
        if role not in {"observer", "advisor"}:
            raise ValueError("role must be observer or advisor")
        path = self.handoff_root(session_id, handoff_id) / f"{role}_notes.md"
        lock = self._locks_root / f"handoff-note-{session_id}-{handoff_id}-{role}.lock"
        self.codex_store.assert_write_allowed("overseer", path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            atomic_write_text(path, f"# {role.title()} Notes\n")
        line = f"\n- [{_utc_now()}] {author_instance_id}: {text.strip()}\n"
        with file_lock(lock):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        return path

    def load_checkpoint(self, session_id: str, handoff_id: str) -> dict[str, object]:
        path = self.handoff_root(session_id, handoff_id) / "checkpoint.json"
        if not path.exists():
            raise FileNotFoundError(f"handoff checkpoint not found: {handoff_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _render_brief(self, payload: dict[str, object]) -> str:
        lines = [
            "# Handoff Brief",
            "",
            f"- Session: {payload.get('session_id')}",
            f"- Handoff ID: {payload.get('handoff_id')}",
            f"- Phase: {payload.get('phase')}",
            "",
            "## Pressure",
            "",
            f"```json\n{json.dumps(payload.get('pressure_assessment', {}), indent=2, sort_keys=True)}\n```",
            "",
            "## Session Snapshot",
            "",
            f"```json\n{json.dumps(payload.get('session_snapshot', {}), indent=2, sort_keys=True)}\n```",
            "",
            "## Recommended Next Actions",
        ]
        for item in payload.get("recommended_next_actions", []):  # type: ignore[union-attr]
            lines.append(f"- {item}")
        if not payload.get("recommended_next_actions"):
            lines.append("- (none)")
        return "\n".join(lines).rstrip("\n") + "\n"
