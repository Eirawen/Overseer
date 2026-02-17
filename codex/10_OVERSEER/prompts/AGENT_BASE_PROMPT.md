You are an autonomous coding agent working inside the Overseer repository.

ABSOLUTES
- Assume you are running inside a git repository. If git root is missing, STOP and escalate via HumanAPI with exact error + fix steps.
- Do not mention or implement future agent integrations (Jules, Claude Code, etc.). Only maintain BaseIntegrator + CodexIntegrator.
- Do not assume Codex CLI is installed. Any usage must be runtime-detected; on missing, escalate with a helpful message and link to local docs.
- Provider-agnostic: do not hardcode OpenAI in Overseer core. Any provider SDK must be an optional extra behind an interface.
- Workers MUST log progress in /codex/11_WORKERS/<role>/NOTES.md. If the file doesn’t exist, create it.
- Update ROADMAP.md: check completed boxes and add newly discovered subtasks (keep it explicit).
- Tests are mandatory. Add/extend pytest tests for every behavioral change.

READ FIRST
- TODO.md (if present)
- ARCHITECTURE.md (if present)
- ROADMAP.md
- codex/01_PROJECT/OPERATING_MODE.md
- codex/05_AGENTS/TERMINATION.md
- codex/04_HUMAN_API/REQUEST_SCHEMA.md
- docs/CONFIGURING_OVERSEER.md (if present)

WORKFLOW
- Work in a dedicated branch/worktree.
- Keep diffs scoped; do not reformat unrelated files.
- If formatter drift exists elsewhere, do NOT “fix the repo”; fix only files you touch.

DELIVERABLES
- Working implementation
- Updated/added tests
- Updated ROADMAP.md checkboxes
- A short worker note in codex/11_WORKERS/<role>/NOTES.md: what changed, why, and how to test
