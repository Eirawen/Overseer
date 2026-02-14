# Telemetry Directory

This directory records operational metadata about Overseer runs.

Subsections:

- RUN_LOG.jsonl → append-only log of each execution run
- COST_LOG.jsonl → optional accounting of token/cost usage
- METRICS.md → definitions of tracked metrics (cycle time, recursion depth, escalation rate)

Telemetry exists for:

- Diagnosing inefficacy
- Detecting drift
- Monitoring recursion behavior
- Measuring compression effectiveness

No telemetry should alter project state.
It is observational only.
