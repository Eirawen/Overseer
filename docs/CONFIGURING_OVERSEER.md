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

# Start persistent chat server (local only)
overseer --repo-root . serve --host 127.0.0.1 --port 8765
```

## Local UI MVP scaffold

Start the daemon API first:

```bash
overseer --repo-root . serve --host 127.0.0.1 --port 8765
```

In a second terminal, run the UI with a single command:

```bash
./scripts/run-ui.sh
```

Open `http://127.0.0.1:5173` in your browser.

Recommended UI validation checks:

```bash
npm --prefix ui run typecheck
npm --prefix ui run test
npm --prefix ui run build
```

## Telemetry layout

Each run writes logs to:

- `codex/08_TELEMETRY/runs/<run_id>/stdout.log`
- `codex/08_TELEMETRY/runs/<run_id>/stderr.log`
- `codex/08_TELEMETRY/runs/<run_id>/meta.json`

If Codex CLI is missing, Overseer appends a HumanAPI request in `codex/04_HUMAN_API/HUMAN_QUEUE.md` with install/configuration guidance.

## Execution backend (Celery default)

Overseer now defaults to Celery-backed execution.

- Set `REDIS_URL` to point at your broker/backend (example: `redis://127.0.0.1:6379/0`).
- Run a Celery worker in a separate terminal:

```bash
celery -A overseer.execution.celery_app:celery_app worker --loglevel=INFO
```

- To force local subprocess mode for debugging, set:

```bash
export OVERSEER_EXECUTION_BACKEND=local
```

## Codex CLI prompts and escalations

- Codex CLI may open a browser for ChatGPT login during execution.
- Codex CLI may prompt for repository permissions or CLI updates.
- Overseer surfaces blocked prompts as HumanAPI escalations; resolve these manually in your terminal session and then re-run.

## Custom Human task types

Overseer reads request-type presets from:

- `codex/04_HUMAN_API/HUMAN_TASK_TYPES.json`

This file is repo-owned so each project can define domain-specific human escalations (for example,
game-asset requests vs CLI product-direction requests).

Each entry in `types` must include:

- `id`
- `description`
- `default_type` (must match `REQUEST_SCHEMA.md` TYPE enum)
- `default_urgency` (must match `REQUEST_SCHEMA.md` URGENCY enum)
- `required_fields` (non-empty checklist)
- `when_to_use`
- `examples` (optional)

Validate and inspect config via CLI:

```bash
overseer --repo-root . human-types validate
overseer --repo-root . human-types list
```

If a task type is not provided, Overseer defaults to the `decision` entry.

## Durable run persistence

Overseer persists run state in SQLite at:

- `codex/08_TELEMETRY/overseer.sqlite`

This store is shared by local and Celery execution backends, so run metadata survives process restarts.

You can reconcile stale running jobs with:

```bash
overseer --repo-root . runs reconcile --stale-after-seconds 300
```
