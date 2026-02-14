# Verifier notes

- **Concurrency QA (2026-02-14)**: Spec and tests added. Invariants: meta.json atomic + per-run locked; run status DAG enforced (no regression); TaskStore under task_graph.lock with atomic rewrite; worktree creation under git_worktree.lock, collision raises; execution lock per-run. Tests: concurrent meta reads, cancel-vs-worker status, TaskStore concurrent updates, hammer 25 runs, concurrent worktree create, worktree collision raises. All 12 project tests pass. See docs/CONCURRENCY_SPEC.md and tests/test_concurrency.py.
