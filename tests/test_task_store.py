"""Tests for overseer.task_store: add_task, load_tasks, get_task, update_status."""

from __future__ import annotations

from pathlib import Path

import pytest

from overseer.codex_store import CodexStore
from overseer.task_store import TaskStore


def _store_with_structure(tmp_path: Path) -> tuple[CodexStore, TaskStore]:
    codex = tmp_path / "codex"
    codex.mkdir(parents=True)
    (codex / "03_WORK").mkdir(parents=True)
    (codex / "10_OVERSEER" / "locks").mkdir(parents=True, exist_ok=True)
    store = CodexStore(tmp_path)
    store.codex_root = codex
    store.init_structure()
    return store, TaskStore(store)


def test_add_task_returns_task_with_id_and_queued(tmp_path: Path) -> None:
    _, ts = _store_with_structure(tmp_path)
    task = ts.add_task("build the feature")
    assert task["objective"] == "build the feature"
    assert task["status"] == "queued"
    assert task["id"].startswith("task-")
    assert len(task["id"]) == 17  # task- (5) + 12 hex
    assert "created_at" in task


def test_add_task_appends_to_file(tmp_path: Path) -> None:
    _, ts = _store_with_structure(tmp_path)
    ts.add_task("first")
    ts.add_task("second")
    tasks = ts.load_tasks()
    assert len(tasks) == 2
    assert {t["objective"] for t in tasks} == {"first", "second"}


def test_load_tasks_empty_when_no_file(tmp_path: Path) -> None:
    _, ts = _store_with_structure(tmp_path)
    # init_structure creates empty TASK_GRAPH.jsonl
    tasks = ts.load_tasks()
    assert tasks == []


def test_load_tasks_returns_all_tasks(tmp_path: Path) -> None:
    _, ts = _store_with_structure(tmp_path)
    ts.add_task("a")
    ts.add_task("b")
    tasks = ts.load_tasks()
    assert len(tasks) == 2


def test_get_task_returns_task_by_id(tmp_path: Path) -> None:
    _, ts = _store_with_structure(tmp_path)
    t1 = ts.add_task("first")
    t2 = ts.add_task("second")
    got = ts.get_task(t1["id"])
    assert got["id"] == t1["id"]
    assert got["objective"] == "first"
    got2 = ts.get_task(t2["id"])
    assert got2["objective"] == "second"


def test_get_task_raises_when_not_found(tmp_path: Path) -> None:
    _, ts = _store_with_structure(tmp_path)
    ts.add_task("only")
    with pytest.raises(KeyError, match="Task not found: task-nonexistent"):
        ts.get_task("task-nonexistent")


def test_update_status_changes_status(tmp_path: Path) -> None:
    _, ts = _store_with_structure(tmp_path)
    t = ts.add_task("do it")
    updated = ts.update_status(t["id"], "running")
    assert updated["status"] == "running"
    assert ts.get_task(t["id"])["status"] == "running"


def test_update_status_raises_when_not_found(tmp_path: Path) -> None:
    _, ts = _store_with_structure(tmp_path)
    ts.add_task("only")
    with pytest.raises(KeyError, match="Task not found: task-bad"):
        ts.update_status("task-bad", "running")


def test_update_status_extra_fields(tmp_path: Path) -> None:
    _, ts = _store_with_structure(tmp_path)
    t = ts.add_task("run")
    updated = ts.update_status(t["id"], "running", run_id="run-abc123")
    assert updated["status"] == "running"
    assert updated["run_id"] == "run-abc123"
    assert "updated_at" in updated
