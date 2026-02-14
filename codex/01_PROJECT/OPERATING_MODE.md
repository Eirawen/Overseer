# Operating Mode

## Autonomy Dial (1–10)
Default: 8

- 1–3: ask clarifying questions often; no auto-merge; heavy escalation
- 4–6: act by default; escalate ambiguities
- 7–8: act aggressively; escalate only forks, risk spikes, aesthetics, contradictions
- 9–10: escalate only existential shifts; everything else auto-executes

## Merge Policy
At autonomy >= 7:
Auto-merge if ALL are true:
- tests pass
- lint passes (if configured)
- Reviewer approves
- Verifier approves
- no unresolved high-risk flags

## Escalation Categories
Always escalate:
- aesthetic or taste decisions
- strategic direction forks
- risk >= 8 items
- repeated disagreement among reviewers
- external comms (unless explicit module added)

## Interrupt Policy
Interrupt human only when:
- blocking severity >= threshold
- opportunity decays quickly (later module)
- risk spike with blast radius
