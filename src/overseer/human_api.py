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

    def append_request(self, task: dict, reason: str, diagnosis_packet: dict | None = None) -> str:
        self.ensure_queue()
        diagnosis_packet = diagnosis_packet or {}
        diff_summary = diagnosis_packet.get("diff_summary", {})
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
            "DIAGNOSIS_PACKET:\n"
            f"  - last_exit_code: {diagnosis_packet.get('last_exit_code', 'unknown')}\n"
            f"  - codex_log_tail_200: {diagnosis_packet.get('codex_log_tail', '(missing)')}\n"
            f"  - git_status_short: {diagnosis_packet.get('git_status_short', '(missing)')}\n"
            f"  - diff_changed_files: {diff_summary.get('changed_files', 0)}\n"
            f"  - diff_stat: {diff_summary.get('stat', '(missing)')}\n"
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
