from __future__ import annotations

from pathlib import Path

import pytest

from overseer.codex_store import CodexStore
from overseer.handoff.lease import SessionLeaseStore


def _store(tmp_path: Path) -> tuple[CodexStore, SessionLeaseStore]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / "codex").mkdir()
    codex = CodexStore(repo)
    codex.init_structure()
    return codex, SessionLeaseStore(codex)


def test_lease_created_and_primary_owner_enforced(tmp_path: Path) -> None:
    _, leases = _store(tmp_path)
    lease = leases.ensure_lease("sess-1", "ovr-a")
    assert lease.owner_instance_id == "ovr-a"
    leases.assert_primary_owner("sess-1", "ovr-a")
    with pytest.raises(PermissionError, match="owned by ovr-a"):
        leases.assert_primary_owner("sess-1", "ovr-b")


def test_register_observer_and_transfer(tmp_path: Path) -> None:
    _, leases = _store(tmp_path)
    leases.ensure_lease("sess-1", "ovr-a")
    prepared = leases.set_handoff_prepared("sess-1", "handoff-1", "ovr-a")
    assert prepared.active_handoff_id == "handoff-1"
    offered = leases.register_observer("sess-1", "handoff-1", "ovr-b")
    assert "ovr-b" in offered.observer_instance_ids
    transferred = leases.transfer_lease("sess-1", "handoff-1", "ovr-a", "ovr-b")
    assert transferred.owner_instance_id == "ovr-b"
    assert transferred.lease_epoch == 1


def test_transfer_requires_registered_observer(tmp_path: Path) -> None:
    _, leases = _store(tmp_path)
    leases.ensure_lease("sess-1", "ovr-a")
    leases.set_handoff_prepared("sess-1", "handoff-1", "ovr-a")
    with pytest.raises(ValueError, match="observer not registered"):
        leases.transfer_lease("sess-1", "handoff-1", "ovr-a", "ovr-z")
