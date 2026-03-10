from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from overseer.codex_store import CodexStore
from overseer.fs import atomic_write_text
from overseer.locks import file_lock

LeaseStatus = Literal["active", "handoff_prepared", "handoff_offered", "transferred"]
OwnerMode = Literal["primary", "observer", "advisor"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SessionLease:
    session_id: str
    lease_epoch: int
    owner_instance_id: str
    owner_mode: OwnerMode
    status: LeaseStatus
    active_handoff_id: str | None
    observer_instance_ids: list[str]
    created_at: str
    updated_at: str
    last_transfer_at: str | None = None


class SessionLeaseStore:
    def __init__(self, codex_store: CodexStore) -> None:
        self.codex_store = codex_store
        self._locks_root = codex_store.codex_root / "10_OVERSEER" / "locks"
        self._locks_root.mkdir(parents=True, exist_ok=True)

    def _lease_path(self, session_id: str) -> Path:
        return self.codex_store.codex_root / "10_OVERSEER" / "sessions" / session_id / "lease.json"

    def _lock_path(self, session_id: str) -> Path:
        return self._locks_root / f"session-lease-{session_id}.lock"

    def read_lease(self, session_id: str) -> SessionLease:
        path = self._lease_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"lease not found for session: {session_id}")
        with file_lock(self._lock_path(session_id)):
            payload = json.loads(path.read_text(encoding="utf-8"))
        return SessionLease(**payload)

    def ensure_lease(self, session_id: str, owner_instance_id: str) -> SessionLease:
        path = self._lease_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.codex_store.assert_write_allowed("overseer", path)
        with file_lock(self._lock_path(session_id)):
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                return SessionLease(**payload)
            now = _utc_now()
            lease = SessionLease(
                session_id=session_id,
                lease_epoch=0,
                owner_instance_id=owner_instance_id,
                owner_mode="primary",
                status="active",
                active_handoff_id=None,
                observer_instance_ids=[],
                created_at=now,
                updated_at=now,
                last_transfer_at=None,
            )
            atomic_write_text(path, json.dumps(asdict(lease), indent=2, sort_keys=True) + "\n")
            return lease

    def assert_primary_owner(self, session_id: str, owner_instance_id: str) -> None:
        lease = self.read_lease(session_id)
        if lease.owner_instance_id != owner_instance_id:
            raise PermissionError(f"session lease owned by {lease.owner_instance_id}")
        if lease.owner_mode != "primary":
            raise PermissionError(f"session lease owner {lease.owner_instance_id} is not primary")

    def register_observer(self, session_id: str, handoff_id: str, observer_instance_id: str) -> SessionLease:
        return self._mutate(session_id, lambda lease: self._register_observer_mut(lease, handoff_id, observer_instance_id))

    def transfer_lease(
        self, session_id: str, handoff_id: str, from_owner_instance_id: str, to_owner_instance_id: str
    ) -> SessionLease:
        return self._mutate(
            session_id,
            lambda lease: self._transfer_mut(lease, handoff_id, from_owner_instance_id, to_owner_instance_id),
        )

    def mark_advisor(self, session_id: str, prior_owner_instance_id: str, handoff_id: str) -> None:
        marker = self.codex_store.codex_root / "08_TELEMETRY" / "sessions" / session_id / "handoffs" / handoff_id / "advisor_marker.json"
        self.codex_store.assert_write_allowed("overseer", marker)
        payload = {
            "prior_owner_instance_id": prior_owner_instance_id,
            "handoff_id": handoff_id,
            "recorded_at": _utc_now(),
        }
        atomic_write_text(marker, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def abort_handoff(self, session_id: str, handoff_id: str, owner_instance_id: str) -> SessionLease:
        return self._mutate(session_id, lambda lease: self._abort_mut(lease, handoff_id, owner_instance_id))

    def set_handoff_prepared(self, session_id: str, handoff_id: str, owner_instance_id: str) -> SessionLease:
        return self._mutate(session_id, lambda lease: self._prepared_mut(lease, handoff_id, owner_instance_id))

    def _mutate(self, session_id: str, fn) -> SessionLease:
        path = self._lease_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.codex_store.assert_write_allowed("overseer", path)
        with file_lock(self._lock_path(session_id)):
            if not path.exists():
                raise FileNotFoundError(f"lease not found for session: {session_id}")
            lease = SessionLease(**json.loads(path.read_text(encoding="utf-8")))
            updated = fn(lease)
            atomic_write_text(path, json.dumps(asdict(updated), indent=2, sort_keys=True) + "\n")
            return updated

    def _prepared_mut(self, lease: SessionLease, handoff_id: str, owner_instance_id: str) -> SessionLease:
        if lease.owner_instance_id != owner_instance_id:
            raise PermissionError(f"session lease owned by {lease.owner_instance_id}")
        if lease.active_handoff_id and lease.active_handoff_id != handoff_id:
            raise ValueError(f"handoff already active: {lease.active_handoff_id}")
        return SessionLease(
            **{
                **asdict(lease),
                "status": "handoff_prepared",
                "active_handoff_id": handoff_id,
                "updated_at": _utc_now(),
            }
        )

    def _register_observer_mut(self, lease: SessionLease, handoff_id: str, observer_instance_id: str) -> SessionLease:
        if lease.active_handoff_id != handoff_id:
            raise ValueError(f"handoff not active: {handoff_id}")
        observers = list(lease.observer_instance_ids)
        if observer_instance_id not in observers:
            observers.append(observer_instance_id)
        return SessionLease(
            **{
                **asdict(lease),
                "observer_instance_ids": sorted(observers),
                "status": "handoff_offered",
                "updated_at": _utc_now(),
            }
        )

    def _transfer_mut(
        self,
        lease: SessionLease,
        handoff_id: str,
        from_owner_instance_id: str,
        to_owner_instance_id: str,
    ) -> SessionLease:
        if lease.active_handoff_id != handoff_id:
            raise ValueError(f"handoff not active: {handoff_id}")
        if lease.owner_instance_id != from_owner_instance_id:
            raise PermissionError(f"session lease owned by {lease.owner_instance_id}")
        if to_owner_instance_id not in lease.observer_instance_ids:
            raise ValueError(f"observer not registered: {to_owner_instance_id}")
        now = _utc_now()
        return SessionLease(
            **{
                **asdict(lease),
                "lease_epoch": lease.lease_epoch + 1,
                "owner_instance_id": to_owner_instance_id,
                "owner_mode": "primary",
                "status": "active",
                "active_handoff_id": None,
                "observer_instance_ids": [],
                "updated_at": now,
                "last_transfer_at": now,
            }
        )

    def _abort_mut(self, lease: SessionLease, handoff_id: str, owner_instance_id: str) -> SessionLease:
        if lease.owner_instance_id != owner_instance_id:
            raise PermissionError(f"session lease owned by {lease.owner_instance_id}")
        if lease.active_handoff_id != handoff_id:
            raise ValueError(f"handoff not active: {handoff_id}")
        return SessionLease(
            **{
                **asdict(lease),
                "status": "active",
                "active_handoff_id": None,
                "observer_instance_ids": [],
                "updated_at": _utc_now(),
            }
        )
