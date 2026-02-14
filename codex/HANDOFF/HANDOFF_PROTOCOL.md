# Handoff Protocol (Overseer -> Overseer)

Goal: transfer behavioral alignment and continuity, not just facts.

## Trigger
- context pressure (manual), or
- scheduled rotation, or
- human request.

## Ceremony
1) Overseer A generates HANDOFF_PACKET.md using template.
2) Overseer B reads:
   - CHARTER, OPERATING_MODE, OBJECTIVES
   - latest DECISION_LOG + RISKS
   - latest CONTEXT_STREAM snapshot
3) Apprenticeship dialogue:
   A interrogates B with SUCCESSOR_EXAM questions.
4) B produces:
   - NEXT_24H_PLAN.md
   - updated HUMAN_QUEUE.md draft
   - 3 predicted failure modes
5) A approves retirement only if B passes.
