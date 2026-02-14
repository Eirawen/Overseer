from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from overseer.codex_store import CodexStore
from overseer.fs import test_delay_taskstore_after_read
from overseer.locks import file_lock


class TaskStore:
    def __init__(self, codex_store: CodexStore) -> None:
        self.codex_store = codex_store
        self.task_file = codex_store.codex_root / "03_WORK" / "TASK_GRAPH.jsonl"
        self._task_graph_lock = codex_store.codex_root / "10_OVERSEER" / "locks" / "task_graph.lock"

    def add_task(self, objective: str) -> dict:
        task = {
            "id": f"task-{uuid4().hex[:12]}",
            "objective": objective,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._append_task(task)
        return task

    def _append_task(self, task: dict) -> None:
        self.codex_store.assert_write_allowed("overseer", self.task_file)
        with file_lock(Path(self._task_graph_lock)):
            with self.task_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(task) + "\n")

    def load_tasks(self) -> list[dict]:
        with file_lock(Path(self._task_graph_lock)):
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

    def update_status(self, task_id: str, status: str, **extra_fields: object) -> dict:
        with file_lock(Path(self._task_graph_lock)):
            if not self.task_file.exists():
                raise KeyError(f"Task not found: {task_id}")
            tasks: list[dict] = []
            with self.task_file.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        tasks.append(json.loads(line))
            test_delay_taskstore_after_read()
            updated: dict | None = None
            for task in tasks:
                if task["id"] == task_id:
                    task["status"] = status
                    task.update(extra_fields)
                    task["updated_at"] = datetime.now(timezone.utc).isoformat()
                    updated = task
                    break
            if updated is None:
                raise KeyError(f"Task not found: {task_id}")

            self.codex_store.assert_write_allowed("overseer", self.task_file)
            lines = [json.dumps(t) + "\n" for t in tasks]
            tmp = self.task_file.with_suffix(".jsonl.tmp")
            try:
                tmp.write_text("".join(lines), encoding="utf-8")
                os.replace(tmp, self.task_file)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
        return updated
