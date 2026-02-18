from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

EMPTY_HUMAN_QUEUE = """# Human Queue

## Pending Requests

- (empty)
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


class CodexStore:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.codex_root = repo_root / "codex"
        self.layout = CodexLayout(root=self.codex_root)

    def ensure_codex_root(self) -> None:
        if not self.codex_root.exists() or not self.codex_root.is_dir():
            raise FileNotFoundError("Missing required codex directory")

    def init_structure(self) -> None:
        """Create missing structure only; never overwrite canonical numbered docs."""
        self.ensure_codex_root()
        for directory in self.layout.required_dirs:
            directory.mkdir(parents=True, exist_ok=True)

        self._ensure_file("01_PROJECT/OPERATING_MODE.md", "# Operating Mode\n")
        self._ensure_file("02_MEMORY/DECISION_LOG.md", "# Decision Log\n")
        self._ensure_file("03_WORK/TASK_GRAPH.jsonl", "")
        self._ensure_file("04_HUMAN_API/REQUEST_SCHEMA.md", "# Human Request Schema (strict)\n\nHUMAN_REQUEST:\nTYPE: {design_direction | decision | external_action | clarification | review}\nURGENCY: {low | medium | high | interrupt_now}\nTIME_REQUIRED_MIN: <int>\nCONTEXT: <short>\nOPTIONS:\n  - <option A>\n  - <option B>\nRECOMMENDATION: <one of options or custom>\nWHY: <1-3 bullets>\nUNBLOCKS: <what changes after you answer>\nREPLY_FORMAT: <exact expected reply>\n")
        self._ensure_file(
            "04_HUMAN_API/HUMAN_TASK_TYPES.json",
            '{\n'
            '  "types": [\n'
            '    {\n'
            '      "id": "decision",\n'
            '      "description": "General tradeoff and approval decisions.",\n'
            '      "default_type": "decision",\n'
            '      "default_urgency": "high",\n'
            '      "required_fields": ["CONTEXT", "OPTIONS", "RECOMMENDATION", "UNBLOCKS", "REPLY_FORMAT"],\n'
            '      "when_to_use": "Use when the agent reaches a true fork and needs a human choice to proceed.",\n'
            '      "examples": ["Pick between architecture A vs B", "Approve rollout strategy"]\n'
            '    },\n'
            '    {\n'
            '      "id": "game_asset_request",\n'
            '      "description": "Requests for externally provided game or media assets.",\n'
            '      "default_type": "external_action",\n'
            '      "default_urgency": "medium",\n'
            '      "required_fields": ["asset_name", "target_format", "style_constraints", "deadline"],\n'
            '      "when_to_use": "Use when the task is blocked on a human creating/procuring an asset.",\n'
            '      "examples": ["Need icon set for inventory UI", "Need voice line recording for tutorial"]\n'
            '    },\n'
            '    {\n'
            '      "id": "cli_direction",\n'
            '      "description": "Repo policy and behavior decisions for CLI or backend tooling.",\n'
            '      "default_type": "design_direction",\n'
            '      "default_urgency": "high",\n'
            '      "required_fields": ["target_command", "expected_behavior", "compat_constraints"],\n'
            '      "when_to_use": "Use when command semantics are ambiguous and product intent is required.",\n'
            '      "examples": ["Should command default to dry-run?", "How should flags interact?"]\n'
            '    }\n'
            '  ]\n'
            '}\n',
        )
        self._ensure_file("04_HUMAN_API/HUMAN_QUEUE.md", EMPTY_HUMAN_QUEUE)
        self._ensure_file("05_AGENTS/TERMINATION.md", "# Termination & Recursion Rules\n")
        self._ensure_file("08_TELEMETRY/RUN_LOG.jsonl", "")

        self._ensure_file("10_OVERSEER/.gitkeep", "")
        self._ensure_file("11_WORKERS/builder/.gitkeep", "")
        self._ensure_file("11_WORKERS/reviewer/.gitkeep", "")
        self._ensure_file("11_WORKERS/verifier/.gitkeep", "")

    def _ensure_file(self, relative_path: str, content: str) -> None:
        path = self.codex_root / relative_path
        if not path.exists():
            path.write_text(content, encoding="utf-8")

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
