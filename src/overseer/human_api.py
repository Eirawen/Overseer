from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from overseer.codex_store import CodexStore, EMPTY_HUMAN_QUEUE
from overseer.locks import file_lock

_REQUEST_ID_PATTERN = re.compile(r"^hr-[0-9a-f]{12}$")
_SCHEMA_ENUM_PATTERN = re.compile(r"^(TYPE|URGENCY):\s*\{([^}]+)\}\s*$", flags=re.MULTILINE)
_ALLOWED_STATUSES = {"pending", "resolved"}
_REQUIRED_SCHEMA_KEYS = {
    "HUMAN_REQUEST:",
    "TYPE:",
    "URGENCY:",
    "TIME_REQUIRED_MIN:",
    "CONTEXT:",
    "OPTIONS:",
    "RECOMMENDATION:",
    "WHY:",
    "UNBLOCKS:",
    "REPLY_FORMAT:",
}


@dataclass(frozen=True)
class HumanRequest:
    request_id: str
    request_type: str
    urgency: str
    time_required_min: int
    context: str
    options: list[str]
    recommendation: str
    why: list[str]
    unblocks: str
    reply_format: str
    task_id: str | None
    run_id: str | None
    status: str
    created_at: str
    request_path: Path
    resolution_path: Path | None


@dataclass(frozen=True)
class HumanRequestSchema:
    allowed_types: set[str]
    allowed_urgencies: set[str]


class HumanAPI:
    def __init__(self, codex_store: CodexStore) -> None:
        self.codex_store = codex_store
        self.human_api_root = codex_store.codex_root / "04_HUMAN_API"
        self.queue_file = self.human_api_root / "HUMAN_QUEUE.md"
        self.schema_file = self.human_api_root / "REQUEST_SCHEMA.md"
        self.requests_dir = self.human_api_root / "requests"
        self._queue_lock = codex_store.codex_root / "10_OVERSEER" / "locks" / "human_queue.lock"

    def ensure_queue(self) -> None:
        if not self.queue_file.exists():
            self.codex_store.assert_write_allowed("overseer", self.queue_file)
            self.queue_file.write_text(EMPTY_HUMAN_QUEUE, encoding="utf-8")
        self.requests_dir.mkdir(parents=True, exist_ok=True)

    def _load_schema(self) -> HumanRequestSchema:
        if not self.schema_file.exists():
            raise ValueError(f"missing request schema: {self.schema_file}")

        text = self.schema_file.read_text(encoding="utf-8")
        missing_schema_keys = [key for key in sorted(_REQUIRED_SCHEMA_KEYS) if key not in text]
        if missing_schema_keys:
            raise ValueError(
                "request schema missing required keys: " + ", ".join(missing_schema_keys)
            )

        enums: dict[str, set[str]] = {}
        for enum_name, enum_values in _SCHEMA_ENUM_PATTERN.findall(text):
            enums[enum_name] = {item.strip() for item in enum_values.split("|") if item.strip()}

        allowed_types = enums.get("TYPE", set())
        allowed_urgencies = enums.get("URGENCY", set())
        if not allowed_types or not allowed_urgencies:
            raise ValueError("request schema must define TYPE and URGENCY enums")

        return HumanRequestSchema(allowed_types=allowed_types, allowed_urgencies=allowed_urgencies)

    def append_request(
        self,
        task: dict,
        reason: str,
        diagnosis_packet: dict | None = None,
        run_id: str | None = None,
    ) -> str:
        self.ensure_queue()
        diagnosis_packet = diagnosis_packet or {}
        diff_summary = diagnosis_packet.get("diff_summary", {})
        request_id = f"hr-{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        task_id = task.get("id")

        request = (
            f"REQUEST_ID: {request_id}\n"
            f"TASK_ID: {task_id}\n"
            f"RUN_ID: {run_id or ''}\n"
            "STATUS: pending\n"
            f"CREATED_AT: {now}\n"
            "HUMAN_REQUEST:\n"
            "TYPE: decision\n"
            "URGENCY: high\n"
            "TIME_REQUIRED_MIN: 15\n"
            f"CONTEXT: Task {task_id} escalated.\n"
            "OPTIONS:\n"
            "  - Approve latest approach\n"
            "  - Redirect implementation approach\n"
            "RECOMMENDATION: Redirect implementation approach\n"
            "WHY:\n"
            f"  - Escalation trigger: {reason}\n"
            "  - Automated loop reached termination condition\n"
            f"UNBLOCKS: Task {task_id} can proceed with clear decision\n"
            "DIAGNOSIS_PACKET:\n"
            f"  - last_exit_code: {diagnosis_packet.get('last_exit_code', 'unknown')}\n"
            f"  - codex_log_tail_200: {diagnosis_packet.get('codex_log_tail', '(missing)')}\n"
            f"  - git_status_short: {diagnosis_packet.get('git_status_short', '(missing)')}\n"
            f"  - diff_changed_files: {diff_summary.get('changed_files', 0)}\n"
            f"  - diff_stat: {diff_summary.get('stat', '(missing)')}\n"
            "REPLY_FORMAT: Reply with selected option and one-paragraph rationale\n"
        )

        request_path = self.requests_dir / f"{request_id}.md"
        self.codex_store.assert_write_allowed("overseer", request_path)
        self.codex_store.assert_write_allowed("overseer", self.queue_file)
        with file_lock(self._queue_lock):
            request_path.write_text(request + "\n", encoding="utf-8")
            with self.queue_file.open("a", encoding="utf-8") as handle:
                compact_reason = reason.replace("\n", " ").strip()
                handle.write(
                    f"\n- [pending] {request_id} task={task_id} run={run_id or '-'} reason={compact_reason}\n"
                )
        return request

    def _extract_block(self, lines: list[str], key: str) -> tuple[list[str], int]:
        values: list[str] = []
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            if line.startswith("  - "):
                values.append(line[4:].strip())
                idx += 1
                continue
            break
        if not values:
            raise ValueError(f"{key} requires at least one bullet item")
        return values, idx

    def parse_request(self, request_path: Path) -> HumanRequest:
        schema = self._load_schema()
        lines = request_path.read_text(encoding="utf-8").splitlines()
        payload: dict[str, str] = {}

        idx = 0
        while idx < len(lines):
            line = lines[idx].strip()
            idx += 1
            if not line:
                continue
            if line in {"HUMAN_REQUEST:", "DIAGNOSIS_PACKET:"}:
                continue
            if line.startswith("OPTIONS:"):
                options, consumed = self._extract_block(lines[idx:], "OPTIONS")
                payload["OPTIONS"] = "\n".join(options)
                idx += consumed
                continue
            if line.startswith("WHY:"):
                why, consumed = self._extract_block(lines[idx:], "WHY")
                payload["WHY"] = "\n".join(why)
                idx += consumed
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                payload[key.strip()] = value.strip()

        required = [
            "REQUEST_ID",
            "TYPE",
            "URGENCY",
            "TIME_REQUIRED_MIN",
            "CONTEXT",
            "OPTIONS",
            "RECOMMENDATION",
            "WHY",
            "UNBLOCKS",
            "REPLY_FORMAT",
            "STATUS",
            "CREATED_AT",
        ]
        missing = [key for key in required if key not in payload or not payload[key]]
        if missing:
            raise ValueError(f"invalid request {request_path.name}: missing fields: {', '.join(missing)}")

        request_id = payload["REQUEST_ID"]
        if not _REQUEST_ID_PATTERN.match(request_id):
            raise ValueError(f"invalid request id format: {request_id}")

        request_type = payload["TYPE"]
        if request_type not in schema.allowed_types:
            raise ValueError(f"invalid TYPE '{request_type}', expected one of {sorted(schema.allowed_types)}")

        urgency = payload["URGENCY"]
        if urgency not in schema.allowed_urgencies:
            raise ValueError(f"invalid URGENCY '{urgency}', expected one of {sorted(schema.allowed_urgencies)}")

        try:
            time_required_min = int(payload["TIME_REQUIRED_MIN"])
        except ValueError as exc:
            raise ValueError("TIME_REQUIRED_MIN must be an integer") from exc
        if time_required_min < 0:
            raise ValueError("TIME_REQUIRED_MIN must be non-negative")

        status = payload["STATUS"]
        if status not in _ALLOWED_STATUSES:
            raise ValueError(f"invalid STATUS '{status}', expected one of {sorted(_ALLOWED_STATUSES)}")

        options = [line for line in payload["OPTIONS"].splitlines() if line]
        if len(options) < 2:
            raise ValueError("OPTIONS must include at least two choices")

        why = [line for line in payload["WHY"].splitlines() if line]
        if not 1 <= len(why) <= 3:
            raise ValueError("WHY must include 1-3 bullets")

        recommendation = payload["RECOMMENDATION"]
        if not recommendation:
            raise ValueError("RECOMMENDATION cannot be empty")

        resolution_path = request_path.with_suffix(".resolution.json")
        return HumanRequest(
            request_id=request_id,
            request_type=request_type,
            urgency=urgency,
            time_required_min=time_required_min,
            context=payload["CONTEXT"],
            options=options,
            recommendation=recommendation,
            why=why,
            unblocks=payload["UNBLOCKS"],
            reply_format=payload["REPLY_FORMAT"],
            task_id=payload.get("TASK_ID") or None,
            run_id=payload.get("RUN_ID") or None,
            status=status,
            created_at=payload["CREATED_AT"],
            request_path=request_path,
            resolution_path=resolution_path if resolution_path.exists() else None,
        )

    def list_requests(self) -> list[HumanRequest]:
        self.ensure_queue()
        return [self.parse_request(path) for path in sorted(self.requests_dir.glob("hr-*.md"))]

    def show_request(self, request_id: str) -> HumanRequest:
        request_path = self.requests_dir / f"{request_id}.md"
        if not request_path.exists():
            raise ValueError(f"request not found: {request_id}")
        return self.parse_request(request_path)

    def resolve_request(
        self,
        request_id: str,
        choice: str,
        rationale: str,
        artifact_path: str | None = None,
    ) -> Path:
        request = self.show_request(request_id)
        if request.status == "resolved" or request.resolution_path is not None:
            raise ValueError(f"request already resolved: {request_id}")
        if choice not in request.options:
            raise ValueError(f"choice must be one of: {request.options}")
        if not rationale.strip():
            raise ValueError("rationale cannot be empty")
        if artifact_path is not None and not Path(artifact_path).exists():
            raise ValueError(f"artifact path does not exist: {artifact_path}")

        resolved_at = datetime.now(timezone.utc).isoformat()
        resolution_path = request.request_path.with_suffix(".resolution.json")
        payload = {
            "request_id": request.request_id,
            "task_id": request.task_id,
            "run_id": request.run_id,
            "choice": choice,
            "rationale": rationale,
            "artifact_path": artifact_path,
            "resolved_at": resolved_at,
        }

        with file_lock(self._queue_lock):
            resolution_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            lines = request.request_path.read_text(encoding="utf-8").splitlines()
            status_index = next((i for i, line in enumerate(lines) if line.startswith("STATUS:")), -1)
            if status_index == -1:
                raise ValueError(f"invalid request {request_id}: missing STATUS line")
            lines[status_index] = "STATUS: resolved"
            request.request_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.queue_file.open("a", encoding="utf-8") as handle:
                handle.write(f"- [resolved] {request_id} choice={choice}\n")

        if request.run_id:
            self._emit_resolution_event(request.run_id, payload)
            self._maybe_resume_run(request.run_id)
        return resolution_path

    def _emit_resolution_event(self, run_id: str, payload: dict[str, object]) -> None:
        run_dir = self.codex_store.codex_root / "08_TELEMETRY" / "runs" / run_id
        events_path = run_dir / "events.jsonl"
        if not events_path.exists():
            return
        event = {
            "type": "human_resolved",
            "at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        lock_path = run_dir / "events.lock"
        with file_lock(lock_path):
            with events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")

    def _maybe_resume_run(self, run_id: str) -> None:
        policy_file = self.codex_store.repo_root / ".overseer_resume_policy"
        policy = policy_file.read_text(encoding="utf-8").strip() if policy_file.exists() else "manual"
        if policy != "auto":
            return

        run_dir = self.codex_store.codex_root / "08_TELEMETRY" / "runs" / run_id
        events_path = run_dir / "events.jsonl"
        if not events_path.exists():
            return
        event = {
            "type": "resume_requested",
            "at": datetime.now(timezone.utc).isoformat(),
            "payload": {"reason": "human request resolved", "mode": "stub"},
        }
        lock_path = run_dir / "events.lock"
        with file_lock(lock_path):
            with events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")

    def generate_brief(self, queued_tasks: list[dict], escalated_tasks: list[dict]) -> str:
        self.ensure_queue()
        return (
            "Morning Brief\n"
            f"- queued: {len(queued_tasks)}\n"
            f"- escalated: {len(escalated_tasks)}\n"
            f"- human_queue: {Path(self.queue_file).as_posix()}\n"
        )
