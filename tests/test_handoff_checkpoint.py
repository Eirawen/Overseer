from __future__ import annotations

import json
from pathlib import Path

from overseer.codex_store import CodexStore
from overseer.handoff.service import HandoffService
from overseer.session_store import SessionStore


def _setup(tmp_path: Path) -> tuple[CodexStore, SessionStore, HandoffService]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / "codex").mkdir()
    codex = CodexStore(repo)
    codex.init_structure()
    sessions = SessionStore(codex)
    handoff = HandoffService(codex, sessions, instance_id="ovr-owner")
    return codex, sessions, handoff


def test_prepare_handoff_persists_checkpoint_artifacts(tmp_path: Path) -> None:
    codex, sessions, handoff = _setup(tmp_path)
    session_id = sessions.create_session()
    state = sessions.load_session(session_id)
    state["conversation_turns"] = [{"role": "user", "content": "hello"}]
    state["latest_response"] = "working"
    sessions.save_session(state)

    checkpoint = handoff.prepare_handoff(session_id, owner_instance_id="ovr-owner")
    assert checkpoint.checkpoint_json_path.exists()
    assert checkpoint.handoff_brief_path.exists()
    assert checkpoint.observer_notes_path.exists()
    assert checkpoint.advisor_notes_path.exists()
    assert checkpoint.lease_snapshot_path.exists()

    payload = json.loads(checkpoint.checkpoint_json_path.read_text(encoding="utf-8"))
    assert payload["session_id"] == session_id
    assert payload["phase"] == "prepared"
    assert payload["protocol_version"] == 1
    assert "pressure_assessment" in payload
    session_events = (codex.codex_root / "08_TELEMETRY" / "sessions" / session_id / "events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "handoff_prepared" in session_events


def test_observer_and_advisor_notes_are_append_only_artifacts(tmp_path: Path) -> None:
    _, sessions, handoff = _setup(tmp_path)
    session_id = sessions.create_session()
    prepared = handoff.prepare_handoff(session_id, owner_instance_id="ovr-owner")
    handoff_id = prepared.handoff_id
    handoff.register_observer(session_id, handoff_id, observer_instance_id="ovr-observer")
    handoff.append_observer_note(session_id, handoff_id, "ovr-observer", "observing")
    handoff.append_advisor_note(session_id, handoff_id, "ovr-owner", "advisor note")
    root = handoff.checkpoints.handoff_root(session_id, handoff_id)
    assert "observing" in (root / "observer_notes.md").read_text(encoding="utf-8")
    assert "advisor note" in (root / "advisor_notes.md").read_text(encoding="utf-8")
