from __future__ import annotations

from typing import Any, Protocol


class Integrator(Protocol):
    def run_task(self, task: dict[str, Any]) -> dict[str, Any]:
        ...
