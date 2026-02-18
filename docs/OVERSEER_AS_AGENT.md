# Overseer as a Persistent Agent

`OverseerCoreGraph` turns Overseer from a single chat call into a persistent orchestration agent built on LangGraph.

## Why this graph exists

The core graph is designed to:
- keep user conversation responsive while coding runs execute asynchronously,
- orchestrate Builder -> Reviewer -> Verifier-style execution using `CodexIntegrator`,
- persist state so sessions can stop/start without losing context,
- keep artifacts, telemetry, and escalation history in codex.

## State model and modes

Planning artifacts are stored under `codex/03_WORK/ROADMAP.md` and session-local plan files.

Session state is stored under:
- `codex/10_OVERSEER/sessions/<session_id>/state.json`
- `codex/10_OVERSEER/sessions/<session_id>/transcript.jsonl`
- `codex/10_OVERSEER/sessions/<session_id>/plan.json`

Modes:
- `conversation`
- `planning`
- `executing`
- `waiting`
- `reviewing`
- `escalated`
- `idle`

The graph stores conversation turns, plan steps, active run metadata, next actions, pending human requests, and loaded autonomy/termination policy text.

## Main transitions

1. `ingest_user_message`
2. `converse`
3. `maybe_transition_to_planning`
4. `plan_project`
5. `select_next_step`
6. `spawn_builder_run`
7. `poll_runs`
8. `spawn_review_runs`
9. `decide_merge_retry_escalate`
10. `persist_state`
11. `emit_response`

All transitions append structured session events in `codex/08_TELEMETRY/sessions/<session_id>/events.jsonl`.

## Running chat mode

```bash
overseer chat
```

REPL commands:
- `/new` create a fresh session
- `/resume <id>` resume an existing session
- `/status` show mode, active run count, pending human requests
- `/plan` print current plan
- `/tick` poll/advance graph (useful for explicit polling backends)
- `/exit`

## Persistence/resume behavior

The graph uses a file-backed `SessionStore` and best-effort file locks so concurrent writes are less likely to corrupt session state. The persistence layer is isolated so a Redis checkpointer can be introduced later without rewriting graph node logic.

## Worker notes rule

**Workers must append progress notes to `codex/11_WORKERS/<role>/NOTES.md` (append-only).**

Overseer now writes worker notes when it spawns builder/reviewer/verifier runs and when it creates plans.
