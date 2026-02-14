# Termination & Recursion Rules

Hard limits:
- max review cycles per task: 3
- max reviewer-of-reviewer cycles: 2
- if Reviewer and Verifier disagree twice => escalate to human
- if tests fail twice without progress => escalate to human with diagnosis packet

Definition of progress:
- failing tests count decreases OR
- failing tests change meaningfully with an explained reason OR
- scope is reduced with documented rationale

Auto-merge gating (autonomy >= 7):
- tests pass
- reviewer approves
- verifier approves
- risk < 8
