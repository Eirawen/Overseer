# Overseer — ROADMAP
(Last updated: 2026-02-17)

This roadmap is intentionally verbose. The point is to prevent “uh oh, I forgot where we were” and to make it easy to spawn agents against clearly-bounded chunks.

## North Star (what “done” means)
**“Done” = I can use Overseer (as the interface) to run real work on a real repo (e.g., Vetinari thonk), while I keep chatting with Overseer, and I can handle escalations via a Human Queue UI.**
- I am always “talking to Overseer.”
- Overseer can spawn sub-tasks (runs) without blocking the chat thread.
- I can see run status + logs + artifacts.
- Escalations land in a queue with a strict schema, and my replies unblock runs.

Non-goals (explicit, to avoid tarpit):
- Competing with Codex/Claude/Jules on being a coding agent.
- “Secret stories” / elaborate narrative memory beyond summaries + raw logs.
- Perfect formatting / repo-wide black cleanup during core feature work (do later sweep).

---

## Current State Snapshot (from recent work)
These are “as believed true” from the Integrator-02/03 reports and your notes.

### Already in place (or merged)
- [x] Codex scaffolding + canonical codex structure (01_PROJECT, 02_MEMORY, 03_WORK, 04_HUMAN_API, 05_AGENTS, 08_TELEMETRY, 10_OVERSEER, 11_WORKERS)
- [x] Termination policy file + parsing (max cycles, dispute threshold, test-fail-without-progress threshold)
- [x] Human API schema + queue file creation
- [x] Execution abstraction (Local-first, Celery-shaped interface)
- [x] Per-run telemetry directory (stdout/stderr/meta)
- [x] Git-root enforcement (`git rev-parse --show-toplevel`) — Overseer assumes it’s in a git repo
- [x] Worktree-per-run under `codex/10_OVERSEER/worktrees/<run_id>`
- [x] Runtime Codex CLI detection and escalation when missing (with doc link)
- [x] First-launch environment docs (`docs/CONFIGURING_OVERSEER.md`)
- [x] Test suite expanded substantially (you mentioned ~72 tests)

### Known friction / known risks
- [ ] Workers not consistently writing progress notes under `codex/11_WORKERS/<role>/...` (this *will* bite us)
- [ ] “Sometimes Codex asks for permissions / updates” — need preflight + UX handling, not brittle assumptions
- [ ] Concurrency and multi-run-per-task semantics require explicit guarantees + tests (you already did some QA here; we should codify it)

---

## Architectural Invariants (do not drift)
These are the “shape constraints” we keep stacking on.

### Product invariants
- [ ] **Single conversation surface:** user talks to Overseer; Overseer delegates (never “go talk to builder” as primary UI)
- [ ] **Async-first UX:** starting a run should not block chat; chat continues while runs execute
- [ ] **Human Queue is authoritative:** escalations are durable, structured, and resolvable by the human

### Engineering invariants
- [ ] **Provider agnostic:** do not hard-wire OpenAI-only assumptions into the orchestration layer
- [ ] **Agent-agnostic:** we integrate with existing coding agents (Codex CLI first). We do not build “our own coder.”
- [ ] **Git-native:** every run happens in an isolated worktree (or equivalent isolation) with explicit repo-root validation
- [ ] **Local-first, Celery-ready:** local backend exists now; Celery backend later should be a drop-in behind the same interface
- [ ] **Text is cheap:** persist raw logs + artifacts; store summaries as convenience, not as the primary truth

---

## Milestones
### M0 — Stabilize the execution core (make Integrator-02 “boring”)
Goal: execution/run lifecycle is correct, concurrency-safe, observable, and test-covered.

#### M0.1 Run model + lifecycle semantics
- [ ] Define and document:
  - [x] Run states: queued, running, canceling, done, failed, canceled
  - [ ] Run identity: `run_id` uniqueness and idempotency expectations
  - [ ] Relationship: Task ↔ Runs (multiple runs per task allowed; task status should not be “single-run implied”)
  - [ ] Status transitions allowed/disallowed (state machine table)
  - [x] requested -> canceling -> canceled transition is emitted as events and reflected in run status
  - [x] cancel request returns `canceling` for active workers and only transitions to `canceled` on cooperative worker acknowledgment
- [ ] Tests:
  - [ ] Transition legality tests (table-driven)
  - [x] Cancellation semantics tests (cancel queued vs running)
  - [ ] Failure propagation tests (codex crash / nonzero exit / missing binary)

#### M0.2 Concurrency guarantees (the part that forced tiny changes everywhere)
- [ ] Decide concurrency policy in writing (short but explicit):
  - [ ] Multiple runs per task allowed
  - [ ] Worktree isolation per run is mandatory
  - [ ] Shared resources that require locking:
    - [ ] task graph writes / run registry writes
    - [ ] per-task “workspace” lock (if any shared path exists)
    - [ ] Human Queue append
- [ ] Implement or confirm:
  - [ ] Cross-platform best-effort file lock behavior (Linux/WSL first-class, Windows best effort)
  - [ ] Lock acquisition timeouts + escalation behavior (avoid deadlocks)
  - [ ] Atomic writes for meta.json updates (write temp then rename)
- [ ] Tests (must be “mean” tests):
  - [ ] Spawn N concurrent run submissions; assert all runs persist and none overwrite each other
  - [ ] Concurrent Human Queue appends remain well-formed and non-interleaved
  - [ ] Concurrent TASK_GRAPH writes remain valid JSONL / authoritative snapshot stays consistent
  - [ ] Concurrency “hammer test” behind a slow marker (optional nightly)

#### M0.3 Observability: telemetry + summaries without “storytime”
- [ ] Define telemetry layout contract:
  - [ ] `codex/08_TELEMETRY/runs/<run_id>/meta.json`
  - [ ] stdout/stderr logs
  - [x] optional: events.jsonl for structured events (recommended)
- [x] Add structured events (minimal):
  - [ ] run_created
  - [ ] worktree_created
  - [ ] codex_started
  - [ ] codex_finished (exit code)
  - [x] run_state_changed
  - [x] escalation_created (with reason)
  - [x] append-only `events.jsonl` per run with reducer-derived `meta.json` cache
  - [x] deterministic replay tests (including restart recovery)
  - [x] concurrent event append locking tests to prevent JSONL corruption
- [ ] Summary generation:
  - [ ] Store summary alongside raw logs (but raw remains canonical)
  - [ ] Summaries are optional; absence is not failure

#### M0.4 Worker note discipline (stop the drift now)
- [ ] Add an explicit rule doc: `codex/05_AGENTS/WORKER_NOTES.md` (or add to OPERATING_MODE)
  - [ ] Every worker writes:
    - [ ] what they changed
    - [ ] why
    - [ ] what to look at
    - [ ] tests run
    - [ ] known risks
- [ ] Enforce via tooling:
  - [ ] On run completion, require presence of worker note entry (or overseer writes a synthesized note)
  - [ ] CI-ish test: “worker notes updated during run simulation” (lightweight)
- [ ] Update the “always insert” agent prompt header to include:
  - [ ] “read TODO/ARCHITECTURE + relevant codex docs”
  - [ ] “write notes to codex/11_WORKERS/<role>/NOTES.md”
  - [ ] “update ROADMAP checkbox if you complete an item”

---

### M1 — “Overseer Chat” (Integrator-03) becomes the real interface
Goal: chat loop exists, doesn’t block, and can spawn runs + report status.

#### M1.1 Conversation model (minimal, durable)
- [ ] Define conversation store:
  - [ ] A conversation has messages (user/overseer/system/events)
  - [ ] Runs can be attached to conversation context (run references)
- [ ] Persistence (local-first):
  - [ ] Store in files under `codex/` or a lightweight sqlite (pick one; sqlite is fine even local-first)
  - [ ] Must survive process restart
- [ ] Tests:
  - [ ] Conversation append + reload
  - [ ] Run reference stored + can be displayed

#### M1.2 Non-blocking run spawning from chat
- [ ] Chat command surface (initial):
  - [ ] “run <task_id> …” or “do <objective>”
  - [ ] “status <run_id>”
  - [ ] “tail <run_id>”
  - [x] “cancel <run_id>”
- [ ] Implementation:
  - [ ] Chat handler calls execution backend to submit run
  - [ ] Returns immediately with run_id
  - [ ] Background polling or event subscription updates chat with run progress
- [ ] Tests:
  - [ ] Submitting from chat returns immediately
  - [ ] Status updates appear without blocking input handling

#### M1.3 Escalation -> Human Queue loop is tight
- [ ] When escalated:
  - [x] Human request is appended with strict schema
  - [ ] Chat shows: “escalated, see queue item X”
- [ ] Human replies:
  - [x] Are captured and linked to the escalation
  - [x] Can trigger “resume run” or spawn a follow-up run (resume stub event)
- [x] Human Queue CLI coverage: list/show/resolve with explicit validation errors
- [ ] Tests:
  - [x] Escalation creates queue entry
  - [x] Reply updates escalation status + unblocks follow-up
- [x] Idempotency policy: resolving the same Human Queue item twice is blocked with a clear error
- [x] Schema hardening: validate required schema keys and enforce request status/WHY constraints

#### M1.4 Codex auth + permissions prompts (make it robust)
- [ ] Preflight step before run:
  - [ ] Validate codex binary exists
  - [ ] Validate “logged in” state if detectable
  - [ ] Detect “permission prompt” risks (best-effort)
- [ ] UX handling:
  - [ ] If codex requests repo access/update:
    - [ ] escalate with clear steps + exact reply format
    - [ ] do not brick the whole run system
- [ ] Docs:
  - [ ] Add section “Codex authentication & trust prompts” to CONFIGURING_OVERSEER.md
- [ ] Tests:
  - [ ] Simulate missing auth state -> escalation path (mock)
  - [ ] Simulate “needs update” -> escalation path (mock)

---

### M2 — UI time (replace CLI pain with the real product)
Goal: the UI you described: left = “talk to Overseer” (optionally voice), right = Human Work Queue.

We will build text-first UI, then optionally add voice polish. Voice is presentation; async orchestration is substance.

#### M2.1 Choose UI shell (pragmatic)
- [ ] Decide one:
  - [ ] Tauri (Rust + web UI)
  - [ ] Electron
  - [ ] Pure local web app (FastAPI + React) opened in browser
- [ ] Criteria:
  - [ ] Fast iteration
  - [ ] Easy streaming logs
  - [ ] Local-first storage access
  - [ ] Packaging later is possible

#### M2.2 Core UI views
- [ ] Chat pane (left):
  - [ ] Send message
  - [ ] Show run cards (status, duration, links to logs, worktree path)
  - [ ] Streaming updates / polling
- [ ] Human Queue pane (right):
  - [ ] List requests
  - [ ] Click into request detail (strict schema rendering)
  - [ ] Reply box that enforces REPLY_FORMAT
  - [ ] Mark resolved
- [ ] Runs pane (optional but useful):
  - [ ] List runs
  - [ ] Filter by task / status
  - [ ] Open logs

#### M2.3 UI ↔ backend bridge
- [ ] Provide a local API surface:
  - [ ] `POST /chat/send`
  - [ ] `POST /runs/submit`
  - [ ] `GET /runs/:id`
  - [ ] `GET /runs/:id/logs`
  - [ ] `GET /human-queue`
  - [ ] `POST /human-queue/:id/reply`
- [ ] Or, if staying fully local-process, a direct Python binding is OK initially—but API tends to simplify UI.

#### M2.4 Voice polish (optional, after text UX is correct)
- [ ] “Soft glowy voice thing” visualization
- [ ] Optional STT/TTS hooks
- [ ] Must not be required to use the product
- [ ] Defer model/provider choices until needed (keep provider-agnostic)

---

### M3 — Integrations (without confusing the agents)
Goal: keep the door open without mentioning future integrations in current agent prompts.

#### M3.1 Integrator contract hardening
- [ ] BaseIntegrator interface remains stable
- [ ] CodexIntegrator is the only concrete integrator referenced in canonical docs for now
- [ ] Ensure “integrator registry” exists but is minimal:
  - [ ] `CodexIntegrator`
  - [ ] placeholder for future integrators (but not documented loudly)

#### M3.2 Future: Jules/Claude Code integration (deferred)
- [ ] Not in prompts
- [ ] Not in user-facing docs
- [ ] Only in roadmap as a future milestone

---

### M4 — Celery/Redis readiness (design now, implement when stable)
Goal: local backend remains default, but we can swap in Celery with minimal refactor.

#### M4.1 Backend interface audit
- [ ] Ensure ExecutionBackend protocol includes:
  - [ ] submit_run
  - [ ] get_status
  - [ ] cancel
  - [ ] stream_logs (or tail)
  - [ ] list_runs
- [ ] Ensure run metadata format is backend-agnostic

#### M4.2 Celery backend plan (write the design doc)
- [ ] `docs/CELERY_BACKEND_PLAN.md`
  - [ ] queue naming
  - [ ] task payload schema (run_id, repo_root, integrator, args)
  - [ ] artifact/log persistence (still filesystem under codex, or blob store later)
  - [ ] retries + idempotency rules
  - [ ] failure modes + escalation triggers
- [ ] Stub implementation behind a feature flag (optional)

#### M4.3 Implement Celery backend (later)
- [ ] Add redis + celery deps
- [ ] Worker process
- [ ] Integration tests in CI

---

### M5 — Hardening + “usable on Vetinari thonk”
Goal: prove it works on a real repo and real workflow.

#### M5.1 Real repo smoke tests
- [ ] Use Overseer to:
  - [ ] create tasks
  - [ ] run codex changes in worktrees
  - [ ] pass tests in the target repo
  - [ ] handle at least one escalation end-to-end
- [ ] Capture learnings in Decision Log

#### M5.2 Repo hygiene passes (scheduled, not blocking core)
- [ ] Formatting sweep (black / ruff / etc.)
- [ ] Dependency pin sanity
- [ ] Remove dead code / deprecated runtime modules
- [ ] Tighten CI (optional)

#### M5.3 Security + safety boundaries (minimum viable)
- [ ] Ensure writes are constrained (already partially via CodexStore)
- [ ] Validate no accidental secret logging
- [ ] Document “do not paste secrets into chat” + safe patterns

---

## Backlog (nice-to-haves, explicitly not required for “done”)
- [ ] Rich run diff viewer in UI (changes, tests, risk)
- [ ] Summaries cached in redis (later)
- [ ] DB-backed persistence with migrations
- [ ] Multi-repo workspace support
- [ ] Role-based policies beyond “overseer vs workers”
- [ ] Plugin marketplace nonsense (no)

---

## Immediate Next Steps (the next 5 things I would spawn agents for)
(These are sequenced to minimize rework.)

1) [ ] **Write/refresh `ROADMAP.md` (this file) into repo and link it from README**
2) [ ] **Worker notes enforcement** (policy + minimal enforcement + prompt header)
3) [ ] **M1.2 Non-blocking spawn from chat** (prove async loop works end-to-end)
4) [ ] **M2 UI shell decision + skeleton UI** (chat + queue panes with stub data)
5) [ ] **Codex auth/permission preflight handling** (robust dev experience)

---

## Agent Prompt Header (to be used as “always insert”)
(Add this to whatever your “always insert” system is; it is part of the roadmap because drift starts here.)

- Read:
  - TODO.md, ARCHITECTURE.md (if present)
  - codex/01_PROJECT/OPERATING_MODE.md
  - codex/05_AGENTS/TERMINATION.md
  - ROADMAP.md (and check off items you complete)
- Write progress notes:
  - codex/11_WORKERS/<role>/NOTES.md (append-only)
  - include: summary, files touched, tests run, risks
- Never assume:
  - Codex CLI is installed (must detect + escalate with docs)
- Always assume:
  - You are inside a git repo; use worktrees for runs
- Don’t mention future integrations (Jules/Claude) unless the task explicitly asks

