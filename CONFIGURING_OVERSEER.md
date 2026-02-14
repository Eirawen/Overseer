# Configuring Overseer

## Requirements

- Python 3.11+
- Git installed and usable from shell (`git --version`)
- Codex CLI installed and on PATH (`codex --version`)

## Install

```bash
python -m pip install -e .
python -m pip install -e .[dev]
```

## PATH expectations

`codex` must resolve in the same environment where you run `python -m overseer ...`.

```bash
which codex
codex --version
```

## Integrate command

Run a real git-native integration attempt:

```bash
python -m overseer --repo-root . integrate --task <task-id>
```

Behavior:

1. Validates codex structure.
2. Validates git repository context.
3. Creates/reuses worktree at `codex/10_OVERSEER/worktrees/<task-id>` on branch `overseer/<task-id>`.
4. Writes `INSTRUCTIONS.md` in the worktree.
5. Invokes Codex CLI in the worktree.
6. Writes artifacts in `codex/10_OVERSEER/runs/<task-id>/`:
   - `codex.log`
   - `meta.json`
   - `patch.diff`
7. Appends telemetry entry to `codex/08_TELEMETRY/RUN_LOG.jsonl`.
8. Updates task status:
   - `running -> awaiting_review` on success with diff
   - `running -> escalated` on failure/escalation

## Example workflow

```bash
python -m overseer --repo-root . init
TASK_ID=$(python -m overseer --repo-root . add-task "Implement feature X")
python -m overseer --repo-root . integrate --task "$TASK_ID"
python -m overseer --repo-root . brief
```
