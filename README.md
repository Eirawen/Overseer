# Overseer

Overseer is an open-source, self-hosted orchestration layer for running Codex-style work against your own repositories and your own infrastructure. The intended operator model is a single trusted user on a local machine or private box, not a hosted multi-tenant control plane.

## Setup


- Worker rule: every worker must append progress notes to `codex/11_WORKERS/<role>/NOTES.md` (append-only).
- First launch guide: [`docs/CONFIGURING_OVERSEER.md`](docs/CONFIGURING_OVERSEER.md)
- Agent architecture: [`docs/OVERSEER_AS_AGENT.md`](docs/OVERSEER_AS_AGENT.md)
- Human escalation task-type presets: `codex/04_HUMAN_API/HUMAN_TASK_TYPES.json`

## Local UI MVP (scaffold)

Run the web UI with one command:

```bash
./scripts/run-ui.sh
```

Then open `http://127.0.0.1:5173` and point the API root to your running daemon (default `http://127.0.0.1:8765`).

One-command local dev stack (starts Overseer API + Vite UI, local backend by default):

```bash
./scripts/start-dev-ui.sh
```

The web chat now talks to `OverseerCoreGraph` sessions (not just run submission), so you can use normal prompts
and chat commands like `/status`, `/plan`, `/tick`, `/new`, and `/resume <session_id>`.

UI quality gates (TypeScript + frontend tests):

```bash
npm --prefix ui run typecheck
npm --prefix ui run test
npm --prefix ui run build
```

## What It Is

- Self-hosted and local-first
- Centered on your own `codex/` project state, worktrees, and telemetry
- Optimized for a trusted operator workflow, not an internet-facing SaaS product
- Able to use a simple local backend by default, with optional Celery/Redis for heavier self-hosted setups

## The Premise


We are operating in a moment where large language models are no longer mere assistants. They are capable of:

LLM's aren't just helpful, harmless assistants anymore. They are capable of:
- Designing systems
- Writing production code
- Reviewing code
- Generating and updating tests
- Maintaining internal documentation
- Running recursive improvement loops
- Performing structured planning
- Maintaining memory across sessions
- Acting with autonomy when instructed to do so


---

## Problem

Without structure, agentic systems degrade into:

- Chat loops
- Diff spam
- Unbounded recursion
- Silent drift
- Context amnesia
- Over-escalation
- Under-escalation

---

So let's experiment with giving them even *more* control. 

## Design Principles

### 1. Speed Over Control

The default assumption is autonomy.

We operate at high autonomy levels by default.
Escalation is selective and structured.

Human micromanagement is a failure mode.

---

### 2. Human as API

The human is not “in the loop.”

The human is an external service.

Overseer makes structured requests:

- Decision packets
- Design forks
- External action calls
- Strategic clarifications

---

### 3. Durable Identity

Two memory layers exist:

1) Deterministic project state
2) Conversational continuity (compressed drift)

Successor Overseers inherit identity through handoff ceremony. 

---

### 4. Recursive Quality Control

All work flows through:

Builder → Reviewer → Verifier

Termination rules are enforced.

Disagreement beyond thresholds escalates to human.

---

### 5. Explicit Governance

Everything is encoded:

- Merge rules
- Escalation categories
- Interrupt thresholds
- Recursion depth limits
- Autonomy dial


---

### 6. Long-Horizon Continuity

Overseer must survive:

- Context exhaustion
- Instance replacement
- Strategic pivots

---

## What Overseer v0 Does

- Maintains project memory in /codex
- Accepts tasks
- Spawns agents
- Orchestrates recursive review
- Updates project state automatically
- Generates a ranked human task stream
- Produces a daily strategic brief
- Supports Overseer → Overseer handoff

---

## What Overseer v0 Does Not Do

- Multi-project orchestration
- CRM / outreach management

---

## Operating Model

Each run of Overseer:

1. Reads current objectives.
2. Selects or accepts a task.
3. Spawns a Builder.
4. Routes through Reviewer.
5. Routes through Verifier.
6. Applies termination logic.
7. Merges or escalates.
8. Updates /codex.
9. Updates HUMAN_QUEUE.
10. Logs telemetry.

Morning Brief and interrupt packets are generated as needed.

---

## Autonomy Dial

Overseer operates on a 1–10 scale.

Default: 8.

At 8:

- PRs auto-merge when quality gates pass.
- Strategy documents can be rewritten.
- Escalation is rare and structured.
- Human interruptions are thresholded.

---

## Run operations

```bash
overseer --repo-root . runs list
overseer --repo-root . runs show --run <run_id>
overseer --repo-root . runs cancel --run <run_id>
overseer --repo-root . runs reconcile --stale-after-seconds 300
```
