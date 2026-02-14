# Project Charter: Overseer

## Mission
Build a single-project Overseer system that:
- maintains durable project memory in /codex
- spawns agents for tasks (build/review/verify)
- routes only high-leverage decisions to the human via a structured Human API
- supports overseer->overseer handoff with apprenticeship

## Non-goals (v0)
- multi-project orchestration
- CRM, outreach automation
- polished UI dashboards

## Definition of Done (v0)
A run can execute end-to-end:
1) ingest a task
2) spawn a Builder to implement changes
3) spawn Reviewer + Verifier recursively until pass/fail/esc
4) update /codex state automatically
5) produce a ranked HUMAN_QUEUE
6) generate a Morning Brief

## Philosophy
Speed over control. Human is an API for taste, direction, and external actions.
