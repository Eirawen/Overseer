# Decision Log

Append-only. Each entry must be crisp.

Template:
- Date
- Decision
- Rationale
- Alternatives considered
- Consequences / follow-ups
- Reversal conditions (if any)

- Date: 2026-02-23
- Decision: Treat in-process `LocalBackend.run_worker()` test execution as a special cancel path: `cancel()` must not send `SIGTERM` to `os.getpid()`, and `run_worker()` must finalize `canceling -> canceled` after child process termination.
- Rationale: `tests/test_concurrency.py` runs `run_worker()` in a thread inside the pytest process. The backend stored `worker_pid = os.getpid()`, so `cancel()` signaling that PID killed the test runner (`Terminated`). This also exposed a second issue where canceled runs could remain stuck in `canceling`.
- Alternatives considered: (1) Skip/xfail concurrency tests in constrained environments (rejected: masks real bug). (2) Change tests to avoid in-process worker threads (rejected for now: useful coverage pattern). (3) Add a separate "thread worker" marker/PID model (overkill for current backend design).
- Consequences / follow-ups: Keep concurrency tests enabled; future changes to cancel/worker lifecycle should preserve in-process test safety. Also avoid nested locking in tests around backend methods that already acquire the same file lock (`_read_record` / `_write_record`) to prevent self-deadlocks.
- Reversal conditions (if any): If the backend is redesigned so worker identity is never the current process in tests (or cancellation signaling is abstracted per-worker transport), the `worker_pid != os.getpid()` guard can be revisited.
