from __future__ import annotations

from pathlib import Path

from overseer.execution.backend import LocalBackend
from overseer.execution.celery_app import celery_app


@celery_app.task(name="overseer.execution.celery_worker.execute_run")
def execute_run(run_id: str, codex_root: str) -> int:
    backend = LocalBackend(Path(codex_root))
    return backend.run_worker(run_id)
