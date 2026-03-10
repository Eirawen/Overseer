from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

RunState = Literal["queued", "running", "canceling", "done", "failed", "canceled"]


@dataclass(frozen=True)
class RunRequest:
    task_id: str
    objective: str
    run_id: str | None = None
    instructions_payload: str | None = None
    prompt_metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class RunResult:
    run_id: str
    task_id: str
    status: RunState
    exit_code: int | None = None
    error: str | None = None


class BaseIntegrator(Protocol):
    def submit(self, request: RunRequest) -> str: ...

    def status(self, run_id: str) -> RunResult: ...

    def runs(self) -> list[RunResult]: ...

    def cancel(self, run_id: str) -> RunResult: ...
