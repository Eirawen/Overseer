from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from overseer.codex_store import CodexStore


class TaskStore:
    def __init__(self, codex_store: CodexStore) -> None:
        self.codex_store = codex_store
        self.task_file = codex_store.codex_root / "03_WORK" / "TASK_GRAPH.jsonl"

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
        with self.task_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(task) + "\n")

    def load_tasks(self) -> list[dict]:
        if not self.task_file.exists():
            return []
        tasks: list[dict] = []
        with self.task_file.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    tasks.append(json.loads(line))
        return tasks

    def get_task(self, task_id: str) -> dict:
        for task in self.load_tasks():
            if task["id"] == task_id:
                return task
        raise KeyError(f"Task not found: {task_id}")

    def update_status(self, task_id: str, status: str) -> dict:
        tasks = self.load_tasks()
        updated: dict | None = None
        for task in tasks:
            if task["id"] == task_id:
                task["status"] = status
                task["updated_at"] = datetime.now(UTC).isoformat()
                updated = task
                break
        if updated is None:
            raise KeyError(f"Task not found: {task_id}")

        self.codex_store.assert_write_allowed("overseer", self.task_file)
        with self.task_file.open("w", encoding="utf-8") as handle:
            for task in tasks:
                handle.write(json.dumps(task) + "\n")
        return updated
