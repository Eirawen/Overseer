# Workers Memory Area

This directory is a scratchpad space for worker agents.

Purpose:
- Local reasoning
- Internal TODO tracking
- Draft thoughts
- Temporary notes

Rules:
1. Nothing in this directory is canonical.
2. Only TASK_GRAPH.jsonl determines task lifecycle.
3. Workers must not mutate OBJECTIVES, TASK_GRAPH, or DECISION_LOG directly.
4. Workers must communicate proposals via AGENT_REPORT.

Overseer may promote useful insights into canonical state.
