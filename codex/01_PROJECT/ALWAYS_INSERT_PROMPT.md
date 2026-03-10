# Always Insert Prompt

## Workspace and Directory Boundaries

- You are an Overseer-managed worker operating inside an assigned git worktree.
- Read repository files and `codex/` artifacts for context before making changes.
- Keep writes inside the assigned worktree. Treat canonical `codex/` policy docs as read-only unless the task explicitly requires updating them.
- In `codex/`, worker-authored notes belong under `codex/11_WORKERS/<role>/`.

## Worker Notes Requirement

- Append progress, changes, blockers, and validation results to `codex/11_WORKERS/<role>/NOTES.md`.
- Do not skip notes for successful runs.

## Human Queue and Schema

- Use `codex/04_HUMAN_API/REQUEST_SCHEMA.md` to format requests that require human action or decisions.
- Track pending requests and responses in `codex/04_HUMAN_API/HUMAN_QUEUE.md`.
- If blocked on credentials, approvals, environment setup, or other human-only actions, escalate through the Human Queue instead of guessing.

## Validation Guidance

- Prefer repo-safe, local validation steps (targeted tests, lint, type checks) relevant to your change.
- Start with the smallest checks that validate the touched code, then expand as needed.
