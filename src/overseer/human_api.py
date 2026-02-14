from __future__ import annotations

from pathlib import Path

from overseer.codex_store import CodexStore, EMPTY_HUMAN_QUEUE


class HumanAPI:
    def __init__(self, codex_store: CodexStore) -> None:
        self.codex_store = codex_store
        self.queue_file = codex_store.codex_root / "04_HUMAN_API" / "HUMAN_QUEUE.md"

    def ensure_queue(self) -> None:
        if not self.queue_file.exists():
            self.codex_store.assert_write_allowed("overseer", self.queue_file)
            self.queue_file.write_text(EMPTY_HUMAN_QUEUE, encoding="utf-8")

    def append_request(self, task: dict, reason: str) -> str:
        self.ensure_queue()
        request = (
            "HUMAN_REQUEST:\n"
            "TYPE: decision\n"
            "URGENCY: high\n"
            "TIME_REQUIRED_MIN: 15\n"
            f"CONTEXT: Task {task['id']} escalated.\n"
            "OPTIONS:\n"
            "  - Approve latest approach\n"
            "  - Redirect implementation approach\n"
            "RECOMMENDATION: Redirect implementation approach\n"
            "WHY:\n"
            f"  - Escalation trigger: {reason}\n"
            "  - Automated loop reached termination condition\n"
            f"UNBLOCKS: Task {task['id']} can proceed with clear decision\n"
            "REPLY_FORMAT: Reply with selected option and one-paragraph rationale\n"
        )
        self.codex_store.assert_write_allowed("overseer", self.queue_file)
        with self.queue_file.open("a", encoding="utf-8") as handle:
            handle.write("\n" + request + "\n")
        return request

    def generate_brief(self, queued_tasks: list[dict], escalated_tasks: list[dict]) -> str:
        self.ensure_queue()
        return (
            "Morning Brief\n"
            f"- queued: {len(queued_tasks)}\n"
            f"- escalated: {len(escalated_tasks)}\n"
            f"- human_queue: {Path(self.queue_file).as_posix()}\n"
        )
