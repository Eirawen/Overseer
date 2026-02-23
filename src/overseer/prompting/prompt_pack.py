from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from overseer.codex_store import CodexStore

ALWAYS_INSERT_SOURCE_PATH = "codex/01_PROJECT/ALWAYS_INSERT_PROMPT.md"
CONTEXT_SOURCE_PATHS: tuple[str, ...] = (
    "codex/01_PROJECT/OPERATING_MODE.md",
    "codex/05_AGENTS/TERMINATION.md",
    "codex/04_HUMAN_API/REQUEST_SCHEMA.md",
    "codex/04_HUMAN_API/HUMAN_TASK_TYPES.json",
)
SECTION_ORDER: list[str] = [
    "# System Instructions (Always Insert)",
    "# Project Context",
    "# Run Objective",
    "# Execution Constraints",
]

FALLBACK_ALWAYS_INSERT_PROMPT = """# Overseer Worker Baseline Instructions

You are running inside a git worktree created by Overseer for an assigned task.

## Directory and Write Boundaries

- Read the repository and the `codex/` directory for task context and operating rules.
- Keep changes inside the assigned worktree for this run.
- Treat `codex/` canonical policy files as read-only unless Overseer explicitly assigned a task that edits them.

## Worker Notes Requirement

- Maintain worker notes at `codex/11_WORKERS/<role>/NOTES.md`.
- Record what you changed, blockers, and validation results.

## Human Queue and Schema

- Human-only requests are defined by `codex/04_HUMAN_API/REQUEST_SCHEMA.md`.
- Pending/recorded human requests live in `codex/04_HUMAN_API/HUMAN_QUEUE.md`.
- When blocked by a human-only action (credentials, approvals, external setup), escalate through the Human Queue instead of guessing.

## Validation Guidance

- Prefer repo-local test and lint commands that are safe to run in the worktree.
- Run the smallest relevant checks first, then broaden if needed.
"""


@dataclass(frozen=True)
class PromptPolicy:
    always_insert_prompt: str
    always_insert_source_path: str
    always_insert_is_fallback: bool
    context_sections: list[dict[str, str]]
    warnings: list[str]

    @classmethod
    def from_codex(cls, codex_store: CodexStore) -> "PromptPolicy":
        warnings: list[str] = []
        context_sections: list[dict[str, str]] = []

        always_insert_path = codex_store.repo_root / ALWAYS_INSERT_SOURCE_PATH
        if always_insert_path.is_file():
            always_insert_prompt = always_insert_path.read_text(encoding="utf-8")
            always_insert_is_fallback = False
        else:
            always_insert_prompt = FALLBACK_ALWAYS_INSERT_PROMPT
            always_insert_is_fallback = True
            warnings.append(f"missing always-insert prompt: {ALWAYS_INSERT_SOURCE_PATH}; using fallback")

        for source_path in CONTEXT_SOURCE_PATHS:
            path = codex_store.repo_root / source_path
            if not path.is_file():
                warnings.append(f"missing context file: {source_path}; skipped")
                continue
            context_sections.append({"source_path": source_path, "content": path.read_text(encoding="utf-8")})

        return cls(
            always_insert_prompt=always_insert_prompt,
            always_insert_source_path=ALWAYS_INSERT_SOURCE_PATH,
            always_insert_is_fallback=always_insert_is_fallback,
            context_sections=context_sections,
            warnings=warnings,
        )


@dataclass(frozen=True)
class PromptPack:
    system_prompt: str
    project_context_sections: list[dict[str, str]]
    step_objective_prompt: str
    constraints_prompt: str
    composed_prompt: str
    metadata: dict[str, object]

    def to_audit_dict(self) -> dict[str, object]:
        return asdict(self)


class PromptPackBuilder:
    def __init__(self, policy: PromptPolicy, codex_store: CodexStore) -> None:
        self.policy = policy
        self.codex_store = codex_store

    def build_for_run(
        self,
        *,
        task_id: str,
        run_id: str,
        objective: str,
        worker_role: str = "builder",
    ) -> PromptPack:
        system_prompt = self.policy.always_insert_prompt.rstrip("\n")
        project_context_sections = [dict(section) for section in self.policy.context_sections]
        step_objective_prompt = (
            f"Task ID: {task_id}\n"
            f"Run ID: {run_id}\n"
            f"Worker Role: {worker_role}\n"
            f"Objective: {objective}"
        )
        constraints_prompt = self._build_constraints_prompt(worker_role=worker_role)
        project_context_prompt = self._build_project_context_prompt(project_context_sections)

        chunks = [
            f"{SECTION_ORDER[0]}\n\n{system_prompt}",
            f"{SECTION_ORDER[1]}\n\n{project_context_prompt}",
            f"{SECTION_ORDER[2]}\n\n{step_objective_prompt.rstrip()}",
            f"{SECTION_ORDER[3]}\n\n{constraints_prompt.rstrip()}",
        ]
        composed_prompt = "\n\n".join(chunk.rstrip("\n") for chunk in chunks) + "\n"

        metadata: dict[str, object] = {
            "task_id": task_id,
            "run_id": run_id,
            "worker_role": worker_role,
            "objective": objective,
            "warnings": list(self.policy.warnings),
            "always_insert_source_path": self.policy.always_insert_source_path,
            "always_insert_is_fallback": self.policy.always_insert_is_fallback,
            "context_source_paths": [section["source_path"] for section in project_context_sections],
            "project_context_section_count": len(project_context_sections),
            "section_order": list(SECTION_ORDER),
            "audit_paths": {
                "prompt_pack_md": str(Path("codex/08_TELEMETRY/runs") / run_id / "prompt_pack.md"),
                "prompt_pack_json": str(Path("codex/08_TELEMETRY/runs") / run_id / "prompt_pack.json"),
            },
        }
        return PromptPack(
            system_prompt=system_prompt,
            project_context_sections=project_context_sections,
            step_objective_prompt=step_objective_prompt,
            constraints_prompt=constraints_prompt,
            composed_prompt=composed_prompt,
            metadata=metadata,
        )

    def _build_project_context_prompt(self, project_context_sections: list[dict[str, str]]) -> str:
        if not project_context_sections:
            return "No project context snippets available."
        blocks: list[str] = []
        for section in project_context_sections:
            blocks.append(f"## {section['source_path']}\n\n{section['content'].rstrip()}")
        return "\n\n".join(blocks)

    def _build_constraints_prompt(self, *, worker_role: str) -> str:
        return (
            "Follow these execution constraints strictly:\n"
            "- Allowed writes: operate within your assigned git worktree; inside `codex/`, workers should write only to "
            f"`codex/11_WORKERS/{worker_role}/` and run telemetry paths unless Overseer explicitly assigns canonical file edits.\n"
            f"- Worker notes: append run notes to `codex/11_WORKERS/{worker_role}/NOTES.md` with changes, blockers, and validation.\n"
            "- Git/worktree: work in the assigned worktree for this run and avoid destructive git commands (for example `git reset --hard`, "
            "`git checkout --`, or history rewrites) unless explicitly instructed.\n"
            "- Human escalation: if blocked by human-only actions (credentials, approvals, external setup, missing access), escalate via the Human Queue "
            "using the documented schema rather than inventing values.\n"
        )
