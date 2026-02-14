# Overseer

## Setup

- First launch guide: [`docs/CONFIGURING_OVERSEER.md`](docs/CONFIGURING_OVERSEER.md)

## The Premise

Overseer is not a tool.

It is an operating system for a human working with agentic AI at the frontier of capability.

We are operating in a moment where large language models are no longer mere assistants. They are capable of:

- Designing systems
- Writing production code
- Reviewing code
- Generating and updating tests
- Maintaining internal documentation
- Running recursive improvement loops
- Performing structured planning
- Maintaining memory across sessions
- Acting with autonomy when instructed to do so

The bottleneck is no longer typing speed.

The bottleneck is cognitive orchestration.

Overseer exists to eliminate the human as the message bus.

---

## Core Thesis

The future of individual leverage is not “AI helping humans.”

It is:

Human judgment + AI execution + structured governance.

The human should not manually dispatch subtasks.
The human should not manually review every diff.
The human should not be the central routing layer.

The human should act as:

- Taste authority
- Direction setter
- Risk arbitrator
- External interface
- Strategic override

Overseer handles the rest.

---

## The Problem

Without structure, agentic systems degrade into:

- Chat loops
- Diff spam
- Unbounded recursion
- Silent drift
- Context amnesia
- Over-escalation
- Under-escalation

Without philosophy, speed amplifies chaos.

Without governance, recursion becomes waste.

Without continuity, identity fractures.

---

## The Design Principles

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

No rambling chat.

No emotional labor.

Only high-leverage calls.

---

### 3. Durable Identity

Overseer instances are ephemeral.

The /codex directory is durable.

Two memory layers exist:

1) Deterministic project state
2) Conversational continuity (compressed drift)

Successor Overseers inherit identity through handoff ceremony, not raw transcript copying.

---

### 4. Recursive Quality Control

All work flows through:

Builder → Reviewer → Verifier

Termination rules are enforced.

Auto-merge occurs when quality gates are satisfied.

Disagreement beyond thresholds escalates to human.

Infinite recursion is prohibited.

---

### 5. Explicit Governance

Everything is encoded:

- Merge rules
- Escalation categories
- Interrupt thresholds
- Recursion depth limits
- Autonomy dial

Implicit behavior is not allowed.

---

### 6. Long-Horizon Continuity

Overseer must survive:

- Context exhaustion
- Instance replacement
- Philosophical drift
- Strategic pivots

Handoff is a formal ceremony.

Behavioral alignment transfer is mandatory.

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
- Investor automation
- Global capital strategy
- UI dashboards

Those are extensions.

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

## Failure Modes

Overseer considers these catastrophic:

- Silent drift
- Infinite recursion
- Unbounded escalation
- Context amnesia
- Identity fragmentation
- Escalation spam
- Strategic incoherence

Guardrails exist specifically to prevent these.

---

## The Guiding Star

The goal is compression.

10 hours of human work should produce 100 hours of progress.

The human should experience:

- A structured morning sync.
- Ranked manual task stream.
- Autonomous feature build in parallel.
- Interrupts only when justified.
- Continuous progress without micromanagement.

---

## This Is Not an MVP

This project does not optimize for:

- Minimal surface area
- Quick demo
- Shallow validation

It optimizes for:

Durable architecture capable of scaling with model capability growth.

We assume that model capability will increase.

We build to absorb that increase.

---

## The Handoff Imperative

Overseer instances will die.

The project must not.

Handoff protocol ensures:

- Philosophical continuity
- Strategic clarity
- Behavioral alignment
- Succession confidence

No Overseer retires without transferring alignment.

---

## Long-Term Vision

Eventually, Overseer becomes:

- A personal AI organization layer
- A project governor across domains
- A high-autonomy execution fabric
- A strategic amplifier for individual humans

But v0 proves the single-project case first.

---

## If You Are Reading This

You are either:

- The human founder.
- A successor Overseer instance.
- An agent spawned within this project.

In all cases:

Respect the philosophy.
Respect the governance.
Respect the memory layers.
Optimize for speed.
Escalate only when justified.
Preserve identity.

---

This is Overseer.
