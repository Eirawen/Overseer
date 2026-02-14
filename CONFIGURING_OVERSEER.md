# Configuring Overseer

This guide explains the local environment expected by Overseer and shows an end-to-end flow from initialization to integration output review.

## Supported Python Version

Overseer requires **Python 3.11+**.

- Project metadata declares `requires-python = ">=3.11"`.
- Tooling is also pinned to Python 3.11 targets (`ruff`, `black`, and `mypy` settings).

## Installation

From the repository root:

```bash
python -m pip install -e .
```

For development tooling (tests/lint/type-check):

```bash
python -m pip install -e .[dev]
```

## Git Repository Requirement

Overseer is intended to run inside a project repository (typically at the repo root), because it manages a durable `./codex` workspace alongside your code and expects task/integration work to map to real repository changes.

Practical expectation:

1. You run commands from the root of a Git checkout.
2. `./codex` is created/updated in that checkout.
3. Generated task IDs and worker notes correspond to work you can inspect, commit, and review in Git.

## Codex CLI Requirement and PATH Setup

Overseer workflows assume you have the **Codex CLI** available in your shell.

Verify:

```bash
codex --version
```

If the command is not found, ensure the directory containing the `codex` executable is on `PATH`.

Examples:

```bash
# macOS/Linux (bash/zsh)
export PATH="$HOME/.local/bin:$PATH"
```

```powershell
# Windows PowerShell
$env:Path = "$HOME\\.local\\bin;" + $env:Path
```

Persist the change in your shell profile (`~/.bashrc`, `~/.zshrc`, PowerShell profile, etc.) so `codex` is available in new sessions.

## `python -m overseer integrate --task <task-id>` Behavior

Integration runs the task orchestration pipeline for a specific task ID:

- Builder produces an implementation note/output.
- Reviewer evaluates builder output.
- Verifier confirms quality gates.
- Termination policy decides whether to merge or escalate.
- Overseer updates task status, appends telemetry, and (when needed) appends human escalation items.

Resulting artifacts are written under `./codex`, including:

- `codex/03_WORK/TASK_GRAPH.jsonl` (task state/history)
- `codex/08_TELEMETRY/RUN_LOG.jsonl` (run log)
- `codex/11_WORKERS/*/NOTES.md` (builder/reviewer/verifier notes)
- `codex/04_HUMAN_API/HUMAN_QUEUE.md` (if escalation is triggered)

> Note: In this codebase revision, the callable subcommand is `run --task <task-id>`. If your local CLI wrapper exposes `integrate`, it should map to this same orchestration behavior.

## End-to-End Example

From repository root:

1. Initialize codex scaffolding:

   ```bash
   python -m overseer init
   ```

2. Add a task:

   ```bash
   python -m overseer add-task "implement telemetry sanity check"
   ```

   Save the printed task ID (for example: `task-20260214-001`).

3. Integrate the task:

   ```bash
   python -m overseer integrate --task task-20260214-001
   ```

   If your installed CLI does not include `integrate`, use:

   ```bash
   python -m overseer run --task task-20260214-001
   ```

4. Review outputs/artifacts:

   ```bash
   cat codex/03_WORK/TASK_GRAPH.jsonl
   cat codex/08_TELEMETRY/RUN_LOG.jsonl
   cat codex/11_WORKERS/builder/NOTES.md
   cat codex/11_WORKERS/reviewer/NOTES.md
   cat codex/11_WORKERS/verifier/NOTES.md
   cat codex/04_HUMAN_API/HUMAN_QUEUE.md
   ```

5. Optionally generate a human brief:

   ```bash
   python -m overseer brief
   ```
