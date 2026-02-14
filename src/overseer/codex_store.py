from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_HUMAN_REQUEST = """HUMAN_REQUEST:
TYPE: decision
URGENCY: medium
TIME_REQUIRED_MIN: 15
CONTEXT: Pending human escalation queue.
OPTIONS:
  - Accept recommendation
  - Provide alternate direction
RECOMMENDATION: Accept recommendation
WHY:
  - Maintains orchestration flow
UNBLOCKS: Task execution can continue
REPLY_FORMAT: Reply with chosen option and rationale
"""


@dataclass(frozen=True)
class CodexLayout:
    root: Path

    @property
    def required_dirs(self) -> list[Path]:
        return [
            self.root / "01_PROJECT",
            self.root / "02_MEMORY",
            self.root / "03_WORK",
            self.root / "04_HUMAN_API",
            self.root / "05_AGENTS",
            self.root / "08_TELEMETRY",
            self.root / "10_OVERSEER",
            self.root / "11_WORKERS",
            self.root / "11_WORKERS" / "builder",
            self.root / "11_WORKERS" / "reviewer",
            self.root / "11_WORKERS" / "verifier",
        ]

    @property
    def required_files(self) -> dict[Path, str]:
        return {
            self.root / "01_PROJECT" / "OPERATING_MODE.md": "# Operating Mode\n",
            self.root / "02_MEMORY" / "DECISION_LOG.md": "# Decision Log\n",
            self.root / "03_WORK" / "TASK_GRAPH.jsonl": "",
            self.root / "04_HUMAN_API" / "REQUEST_SCHEMA.md": "# Human Request Schema\n",
            self.root / "04_HUMAN_API" / "HUMAN_QUEUE.md": DEFAULT_HUMAN_REQUEST,
            self.root / "05_AGENTS" / "TERMINATION.md": "# Termination Rules\n",
            self.root / "08_TELEMETRY" / "RUN_LOG.jsonl": "",
            self.root / "10_OVERSEER" / ".gitkeep": "",
            self.root / "11_WORKERS" / "builder" / ".gitkeep": "",
            self.root / "11_WORKERS" / "reviewer" / ".gitkeep": "",
            self.root / "11_WORKERS" / "verifier" / ".gitkeep": "",
        }


class CodexStore:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.codex_root = repo_root / "codex"
        self.layout = CodexLayout(root=self.codex_root)

    def ensure_codex_root(self) -> None:
        if not self.codex_root.exists() or not self.codex_root.is_dir():
            raise FileNotFoundError("Missing required codex directory")

    def init_structure(self) -> None:
        self.ensure_codex_root()
        for directory in self.layout.required_dirs:
            directory.mkdir(parents=True, exist_ok=True)
        for path, template in self.layout.required_files.items():
            if not path.exists():
                path.write_text(template, encoding="utf-8")

    def assert_write_allowed(self, actor: str, target: Path) -> None:
        target = target.resolve()
        codex_root = self.codex_root.resolve()
        if not str(target).startswith(str(codex_root)):
            raise PermissionError("Writes are only allowed inside codex")

        telemetry_root = (self.codex_root / "08_TELEMETRY").resolve()
        workers_root = (self.codex_root / "11_WORKERS").resolve()
        canonical_roots = {
            (self.codex_root / "01_PROJECT").resolve(),
            (self.codex_root / "02_MEMORY").resolve(),
            (self.codex_root / "03_WORK").resolve(),
            (self.codex_root / "04_HUMAN_API").resolve(),
            (self.codex_root / "05_AGENTS").resolve(),
        }

        if str(target).startswith(str(telemetry_root)):
            return

        if actor == "overseer":
            return

        if str(target).startswith(str(workers_root / actor)):
            return

        if any(str(target).startswith(str(root)) for root in canonical_roots):
            raise PermissionError("Only overseer may write canonical codex files")

        raise PermissionError(f"Actor '{actor}' cannot write to {target}")
