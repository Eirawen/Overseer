from pathlib import Path
import pytest
from overseer.codex_store import CodexStore

def test_assert_write_allowed_codex_root(tmp_path: Path):
    repo = tmp_path / "repo"
    codex = repo / "codex"
    codex.mkdir(parents=True)
    store = CodexStore(repo)

    # Allowed
    store.assert_write_allowed("overseer", codex / "some_file.txt")

    # Not allowed (outside codex)
    with pytest.raises(PermissionError, match="Writes are only allowed inside codex"):
        store.assert_write_allowed("overseer", repo / "outside.txt")

def test_assert_write_allowed_telemetry(tmp_path: Path):
    repo = tmp_path / "repo"
    codex = repo / "codex"
    telemetry = codex / "08_TELEMETRY"
    telemetry.mkdir(parents=True)
    store = CodexStore(repo)

    # Allowed for any actor
    store.assert_write_allowed("builder", telemetry / "log.jsonl")
    store.assert_write_allowed("reviewer", telemetry / "log.jsonl")

def test_assert_write_allowed_workers(tmp_path: Path):
    repo = tmp_path / "repo"
    codex = repo / "codex"
    workers = codex / "11_WORKERS"
    (workers / "builder").mkdir(parents=True)
    (workers / "reviewer").mkdir(parents=True)
    store = CodexStore(repo)

    # Allowed for the specific actor
    store.assert_write_allowed("builder", workers / "builder" / "notes.md")

    # Not allowed for other actors
    with pytest.raises(PermissionError, match="Actor 'reviewer' cannot write to"):
        store.assert_write_allowed("reviewer", workers / "builder" / "notes.md")

def test_assert_write_allowed_canonical(tmp_path: Path):
    repo = tmp_path / "repo"
    codex = repo / "codex"
    project = codex / "01_PROJECT"
    project.mkdir(parents=True)
    store = CodexStore(repo)

    # Allowed for overseer
    store.assert_write_allowed("overseer", project / "OPERATING_MODE.md")

    # Not allowed for others
    with pytest.raises(PermissionError, match="Only overseer may write canonical codex files"):
        store.assert_write_allowed("builder", project / "OPERATING_MODE.md")

def test_unsafe_path_validation(tmp_path: Path):
    repo = tmp_path / "repo"
    codex = repo / "codex"
    codex.mkdir(parents=True)
    store = CodexStore(repo)

    # This path is NOT inside codex, but starts with the same string prefix
    unsafe_path = repo / "codex_suffix"

    # Now it should BE BLOCKED (raise PermissionError)
    with pytest.raises(PermissionError, match="Writes are only allowed inside codex"):
        store.assert_write_allowed("overseer", unsafe_path)
