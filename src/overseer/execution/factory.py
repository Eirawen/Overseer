from __future__ import annotations

import os
from pathlib import Path

from overseer.execution.backend import CeleryBackend, ExecutionBackend, LocalBackend
from overseer.human_api import HumanAPI


def build_backend(
    codex_root: Path,
    human_api: HumanAPI | None = None,
    worker_role: str = "builder",
) -> ExecutionBackend:
    backend_kind = os.getenv("OVERSEER_EXECUTION_BACKEND", "celery").strip().lower()
    if backend_kind == "local":
        return LocalBackend(codex_root=codex_root, human_api=human_api, worker_role=worker_role)
    if backend_kind == "celery":
        return CeleryBackend(codex_root=codex_root, human_api=human_api, worker_role=worker_role)
    raise RuntimeError(
        "Unknown OVERSEER_EXECUTION_BACKEND value. Use 'celery' (default) or 'local'."
    )
