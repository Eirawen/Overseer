# Codex Index (Overseer Project)

This /codex directory is the durable identity of the Overseer project.

Two layers:
1) Deterministic state: objectives, tasks, decisions, protocols.
2) Continuity layer: compressed conversational context ("we talked yesterday") and handoff packets.

If an Overseer instance is replaced, it MUST:
- read CHARTER, OPERATING_MODE, OBJECTIVES
- skim DECISION_LOG and RISKS
- read the latest CONTEXT_STREAM snapshot
- perform the HANDOFF_PROTOCOL ceremony (if possible)

As for workers, workers *MUST* update 
-their worker/type directory corresponding to the type of worker they are.
