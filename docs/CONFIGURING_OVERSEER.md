# Configuring Overseer on first launch

## Prerequisites

1. Run Overseer from inside a git repository.
2. Ensure a `codex/` directory exists at repo root.
3. Install Codex CLI and make sure `codex` is on `PATH`.

## First launch

```bash
overseer --repo-root . init
```

Then create and execute a task:

```bash
overseer --repo-root . add-task "my objective"
overseer --repo-root . run-agent --task <task_id>
overseer --repo-root . run-status --run <run_id>
```

## Telemetry layout

Each run writes logs to:

- `codex/08_TELEMETRY/runs/<run_id>/stdout.log`
- `codex/08_TELEMETRY/runs/<run_id>/stderr.log`
- `codex/08_TELEMETRY/runs/<run_id>/meta.json`

If Codex CLI is missing, Overseer appends a HumanAPI request in `codex/04_HUMAN_API/HUMAN_QUEUE.md` with install/configuration guidance.
