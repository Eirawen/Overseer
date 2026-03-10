from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from overseer.codex_store import CodexStore
from overseer.fs import atomic_write_text
from overseer.locks import file_lock


class SessionStore:
    def __init__(self, codex_store: CodexStore) -> None:
        self.codex_store = codex_store
        self.sessions_root = codex_store.codex_root / "10_OVERSEER" / "sessions"
        self.lock_root = codex_store.codex_root / "10_OVERSEER" / "locks"
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self.lock_root.mkdir(parents=True, exist_ok=True)

    def _session_dir(self, session_id: str) -> Path:
        return self.sessions_root / session_id

    def _state_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "state.json"

    def _transcript_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "transcript.jsonl"

    def _lock_path(self, session_id: str) -> Path:
        return self.lock_root / f"session-{session_id}.lock"

    def create_session(self) -> str:
        session_id = f"sess-{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        state = {
            "session_id": session_id,
            "mode": "conversation",
            "conversation_turns": [],
            "plan": [],
            "active_runs": {},
            "pending_human_requests": [],
            "last_user_message": "",
            "next_actions": [],
            "created_at": now,
            "updated_at": now,
        }
        self.save_session(state)
        return session_id

    def load_session(self, session_id: str) -> dict[str, Any]:
        state_path = self._state_path(session_id)
        if not state_path.exists():
            raise FileNotFoundError(f"unknown session: {session_id}")
        with file_lock(self._lock_path(session_id)):
            return json.loads(state_path.read_text(encoding="utf-8"))

    def save_session(self, state: dict[str, Any]) -> None:
        session_id = state["session_id"]
        state = {**state, "updated_at": datetime.now(timezone.utc).isoformat()}
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        state_path = self._state_path(session_id)
        transcript_path = self._transcript_path(session_id)

        self.codex_store.assert_write_allowed("overseer", state_path)
        self.codex_store.assert_write_allowed("overseer", transcript_path)

        with file_lock(self._lock_path(session_id)):
            atomic_write_text(state_path, json.dumps(state, indent=2, sort_keys=True) + "\n")
            if state.get("conversation_turns"):
                lines = [json.dumps(turn, sort_keys=True) for turn in state["conversation_turns"]]
                atomic_write_text(transcript_path, "\n".join(lines) + "\n")

    def save_session_as_owner(self, state: dict[str, Any], owner_instance_id: str) -> None:
        lease_store = self._lease_store()
        lease_store.assert_primary_owner(state["session_id"], owner_instance_id)
        self.save_session(state)

    def load_session_with_lease(self, session_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        state = self.load_session(session_id)
        lease_store = self._lease_store()
        try:
            lease = lease_store.read_lease(session_id)
            lease_payload: dict[str, Any] = lease.__dict__
        except FileNotFoundError:
            lease_payload = {"session_id": session_id, "missing": True}
        return state, lease_payload

    def ensure_session_lease(self, session_id: str, owner_instance_id: str):
        lease_store = self._lease_store()
        return lease_store.ensure_lease(session_id, owner_instance_id)

    def assert_primary_session_owner(self, session_id: str, owner_instance_id: str) -> None:
        lease_store = self._lease_store()
        lease_store.assert_primary_owner(session_id, owner_instance_id)

    def list_sessions(self) -> list[str]:
        if not self.sessions_root.exists():
            return []
        sessions: list[str] = []
        for path in sorted(self.sessions_root.iterdir()):
            if path.is_dir() and (path / "state.json").exists():
                sessions.append(path.name)
        return sessions

    def _lease_store(self):
        from overseer.handoff.lease import SessionLeaseStore

        return SessionLeaseStore(self.codex_store)
