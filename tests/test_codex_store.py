"""Tests for overseer.codex_store: init_structure, assert_write_allowed, layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from overseer.codex_store import CodexStore, CodexLayout, EMPTY_HUMAN_QUEUE


def test_codex_layout_required_dirs() -> None:
    layout = CodexLayout(root=Path("/codex"))
    dirs = layout.required_dirs
    assert any("01_PROJECT" in str(d) for d in dirs)
    assert any("08_TELEMETRY" in str(d) for d in dirs)
    assert any("11_WORKERS" in str(d) for d in dirs)
    assert any("builder" in str(d) for d in dirs)


def test_ensure_codex_root_raises_when_missing(tmp_path: Path) -> None:
    store = CodexStore(tmp_path)
    store.codex_root = tmp_path / "nonexistent"
    with pytest.raises(FileNotFoundError, match="Missing required codex directory"):
        store.ensure_codex_root()


def test_ensure_codex_root_passes_when_exists(tmp_path: Path) -> None:
    (tmp_path / "codex").mkdir(parents=True)
    store = CodexStore(tmp_path)
    store.ensure_codex_root()


def test_init_structure_creates_dirs_and_files(tmp_path: Path) -> None:
    (tmp_path / "codex").mkdir(parents=True)
    store = CodexStore(tmp_path)
    store.init_structure()
    assert (store.codex_root / "01_PROJECT" / "OPERATING_MODE.md").exists()
    assert (store.codex_root / "03_WORK" / "TASK_GRAPH.jsonl").exists()
    assert (store.codex_root / "04_HUMAN_API" / "HUMAN_QUEUE.md").exists()
    assert (store.codex_root / "08_TELEMETRY" / "RUN_LOG.jsonl").exists()
    assert (store.codex_root / "05_AGENTS" / "TERMINATION.md").exists()
    assert (store.codex_root / "11_WORKERS" / "builder" / ".gitkeep").exists()
    content = (store.codex_root / "04_HUMAN_API" / "HUMAN_QUEUE.md").read_text(encoding="utf-8")
    assert content == EMPTY_HUMAN_QUEUE


def test_init_structure_does_not_overwrite_existing_canonical(tmp_path: Path) -> None:
    (tmp_path / "codex").mkdir(parents=True)
    (tmp_path / "codex" / "03_WORK").mkdir(parents=True)
    (tmp_path / "codex" / "03_WORK" / "TASK_GRAPH.jsonl").write_text("existing line\n", encoding="utf-8")
    store = CodexStore(tmp_path)
    store.init_structure()
    assert (store.codex_root / "03_WORK" / "TASK_GRAPH.jsonl").read_text(encoding="utf-8") == "existing line\n"


def test_assert_write_allowed_telemetry_always_allowed(tmp_path: Path) -> None:
    (tmp_path / "codex" / "08_TELEMETRY").mkdir(parents=True)
    store = CodexStore(tmp_path)
    target = store.codex_root / "08_TELEMETRY" / "runs" / "run-1" / "meta.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    store.assert_write_allowed("any_actor", target)


def test_assert_write_allowed_overseer_can_write_anywhere_in_codex(tmp_path: Path) -> None:
    (tmp_path / "codex" / "03_WORK").mkdir(parents=True)
    store = CodexStore(tmp_path)
    target = store.codex_root / "03_WORK" / "TASK_GRAPH.jsonl"
    store.assert_write_allowed("overseer", target)


def test_assert_write_allowed_worker_can_write_own_notes(tmp_path: Path) -> None:
    (tmp_path / "codex" / "11_WORKERS" / "builder").mkdir(parents=True)
    store = CodexStore(tmp_path)
    target = store.codex_root / "11_WORKERS" / "builder" / "NOTES.md"
    store.assert_write_allowed("builder", target)


def test_assert_write_allowed_raises_outside_codex(tmp_path: Path) -> None:
    store = CodexStore(tmp_path)
    (tmp_path / "codex").mkdir(parents=True)
    outside = tmp_path / "other" / "file.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(PermissionError, match="only allowed inside codex"):
        store.assert_write_allowed("overseer", outside)


def test_assert_write_allowed_raises_non_overseer_canonical(tmp_path: Path) -> None:
    (tmp_path / "codex" / "03_WORK").mkdir(parents=True)
    store = CodexStore(tmp_path)
    target = store.codex_root / "03_WORK" / "TASK_GRAPH.jsonl"
    with pytest.raises(PermissionError, match="Only overseer may write"):
        store.assert_write_allowed("builder", target)
