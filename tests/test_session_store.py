from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from overseer.codex_store import CodexStore
from overseer.session_store import SessionStore


@pytest.fixture
def codex_store(tmp_path: Path) -> CodexStore:
    (tmp_path / "codex").mkdir()
    return CodexStore(tmp_path)


@pytest.fixture
def session_store(codex_store: CodexStore) -> SessionStore:
    return SessionStore(codex_store)


def test_init_creates_directories(codex_store: CodexStore) -> None:
    SessionStore(codex_store)
    sessions_root = codex_store.codex_root / "10_OVERSEER" / "sessions"
    lock_root = codex_store.codex_root / "10_OVERSEER" / "locks"
    assert sessions_root.exists()
    assert sessions_root.is_dir()
    assert lock_root.exists()
    assert lock_root.is_dir()


def test_create_session(session_store: SessionStore) -> None:
    session_id = session_store.create_session()
    assert session_id.startswith("sess-")
    assert len(session_id) > 5

    state = session_store.load_session(session_id)
    assert state["session_id"] == session_id
    assert state["mode"] == "conversation"
    assert "created_at" in state
    assert "updated_at" in state


def test_list_sessions(session_store: SessionStore) -> None:
    assert session_store.list_sessions() == []

    id1 = session_store.create_session()
    id2 = session_store.create_session()

    sessions = session_store.list_sessions()
    assert len(sessions) == 2
    assert id1 in sessions
    assert id2 in sessions
    assert sessions == sorted([id1, id2])


def test_save_and_load_session(session_store: SessionStore) -> None:
    session_id = session_store.create_session()
    state = session_store.load_session(session_id)

    state["mode"] = "plan"
    state["plan"] = ["step 1", "step 2"]
    session_store.save_session(state)

    loaded = session_store.load_session(session_id)
    assert loaded["mode"] == "plan"
    assert loaded["plan"] == ["step 1", "step 2"]
    assert loaded["updated_at"] >= state["updated_at"]


def test_save_session_transcript(session_store: SessionStore) -> None:
    session_id = session_store.create_session()
    state = session_store.load_session(session_id)

    turns = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    state["conversation_turns"] = turns
    session_store.save_session(state)

    transcript_path = session_store.sessions_root / session_id / "transcript.jsonl"
    assert transcript_path.exists()
    lines = transcript_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0]) == turns[0]
    assert json.loads(lines[1]) == turns[1]


def test_load_session_not_found(session_store: SessionStore) -> None:
    with pytest.raises(FileNotFoundError, match="unknown session"):
        session_store.load_session("nonexistent")
