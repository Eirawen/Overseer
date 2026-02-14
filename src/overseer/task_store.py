from __future__ import annotations

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Generator, TextIO
from uuid import uuid4

from overseer.codex_store import CodexStore


class TaskStore:
    def __init__(self, codex_store: CodexStore) -> None:
        self.codex_store = codex_store
        self.task_file = codex_store.codex_root / "03_WORK" / "TASK_GRAPH.jsonl"

    @contextmanager
    def _lock(self, mode: str, exclusive: bool = False) -> Generator[TextIO, None, None]:
        with self.task_file.open(mode, encoding="utf-8") as handle:
            if fcntl:
                op = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(handle, op)
            try:
                yield handle
            finally:
                if fcntl:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def add_task(self, objective: str) -> dict:
        task = {
            "id": f"task-{uuid4().hex[:12]}",
            "objective": objective,
            "status": "queued",
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._append_task(task)
        return task

    def _append_task(self, task: dict) -> None:
        self.codex_store.assert_write_allowed("overseer", self.task_file)
        with self._lock("a", exclusive=True) as handle:
            handle.write(json.dumps(task) + "\n")

    def load_tasks(self) -> list[dict]:
        if not self.task_file.exists():
            return []
        tasks_map: dict[str, dict] = {}
        with self._lock("r", exclusive=False) as handle:
            for line in handle:
                line = line.strip()
                if line:
                    task = json.loads(line)
                    tasks_map[task["id"]] = task
        return list(tasks_map.values())

    def get_task(self, task_id: str) -> dict:
        for task in self.load_tasks():
            if task["id"] == task_id:
                return task
        raise KeyError(f"Task not found: {task_id}")

    def update_status(self, task_id: str, status: str) -> dict:
        self.codex_store.assert_write_allowed("overseer", self.task_file)
        with self._lock("a+", exclusive=True) as handle:
            handle.seek(0)
            tasks_map: dict[str, dict] = {}
            for line in handle:
                line = line.strip()
                if line:
                    task = json.loads(line)
                    tasks_map[task["id"]] = task

            if task_id not in tasks_map:
                raise KeyError(f"Task not found: {task_id}")

            updated = tasks_map[task_id]
            updated["status"] = status
            updated["updated_at"] = datetime.now(UTC).isoformat()

            handle.seek(0, 2)
            handle.write(json.dumps(updated) + "\n")
            return updated
