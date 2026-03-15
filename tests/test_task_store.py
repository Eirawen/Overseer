from __future__ import annotations

from pathlib import Path

import pytest

from overseer.codex_store import CodexStore
from overseer.task_store import TaskStore


@pytest.fixture
def task_store(tmp_path: Path) -> TaskStore:
    codex_root = tmp_path / "codex"
    codex_root.mkdir()
    codex_store = CodexStore(tmp_path)
    codex_store.init_structure()
    return TaskStore(codex_store)


def test_get_task_success(task_store: TaskStore) -> None:
    task = task_store.add_task("test objective")
    retrieved = task_store.get_task(task["id"])
    assert retrieved == task


def test_get_task_not_found_raises_key_error(task_store: TaskStore) -> None:
    with pytest.raises(KeyError, match="Task not found: non-existent-id"):
        task_store.get_task("non-existent-id")


def test_get_task_empty_store_raises_key_error(task_store: TaskStore) -> None:
    # Ensure task file exists but is empty (init_structure does this)
    assert task_store.task_file.exists()
    with pytest.raises(KeyError, match="Task not found: some-id"):
        task_store.get_task("some-id")


def test_get_task_missing_file_raises_key_error(task_store: TaskStore) -> None:
    # Delete the task file
    task_store.task_file.unlink()
    with pytest.raises(KeyError, match="Task not found: some-id"):
        task_store.get_task("some-id")
