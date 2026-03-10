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
_ALLOWED_WHO_CAN_DO_IT = {"human", "agent"}
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
_DEFAULT_TASK_TYPE_ID = "decision"


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


@dataclass(frozen=True)
class HumanTaskType:
    id: str
    category: str
    description: str
    default_type: str
    default_urgency: str
    who_can_do_it: list[str]
    required_fields: list[str]
    when_to_use: str
    examples: list[str]


@dataclass(frozen=True)
class HumanTaskRoutingRule:
    id: str
    task_type_id: str
    reason_contains: list[str]
    objective_contains: list[str]


@dataclass(frozen=True)
class HumanTaskTypeCatalog:
    task_types: list[HumanTaskType]
    routing_rules: list[HumanTaskRoutingRule]
    fallback_task_type_id: str
    warnings: list[str]


class HumanAPI:
    def __init__(self, codex_store: CodexStore) -> None:
        self.codex_store = codex_store
        self.human_api_root = codex_store.codex_root / "04_HUMAN_API"
        self.queue_file = self.human_api_root / "HUMAN_QUEUE.md"
        self.schema_file = self.human_api_root / "REQUEST_SCHEMA.md"
        self.task_types_file = self.human_api_root / "HUMAN_TASK_TYPES.json"
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

    def _builtin_fallback_catalog(self, warning: str | None = None) -> HumanTaskTypeCatalog:
        warnings = [warning] if warning else []
        return HumanTaskTypeCatalog(
            task_types=[
                HumanTaskType(
                    id=_DEFAULT_TASK_TYPE_ID,
                    category="clarification",
                    description="General tradeoff and approval decisions.",
                    default_type="decision",
                    default_urgency="high",
                    who_can_do_it=["human"],
                    required_fields=[
                        "CONTEXT",
                        "OPTIONS",
                        "RECOMMENDATION",
                        "UNBLOCKS",
                        "REPLY_FORMAT",
                    ],
                    when_to_use="Use when the agent reaches a true fork and needs a human choice to proceed.",
                    examples=["Pick between architecture A vs B", "Approve rollout strategy"],
                )
            ],
            routing_rules=[],
            fallback_task_type_id=_DEFAULT_TASK_TYPE_ID,
            warnings=warnings,
        )

    def _load_task_type_catalog(self, strict: bool = True) -> HumanTaskTypeCatalog:
        if not strict:
            try:
                return self._load_task_type_catalog(strict=True)
            except ValueError as exc:
                return self._builtin_fallback_catalog(
                    f"{self.task_types_file.name} invalid; using built-in fallback. {exc}"
                )

        schema = self._load_schema()
        if not self.task_types_file.exists():
            raise ValueError(
                f"missing human task types config: {self.task_types_file} "
                f"(run `overseer init` to create {self.task_types_file.name})"
            )

        try:
            raw = json.loads(self.task_types_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {self.task_types_file.name}: {exc.msg}") from exc

        if not isinstance(raw, dict):
            raise ValueError(f"{self.task_types_file.name}: root must be an object")
        is_v2 = "task_types" in raw
        if "version" in raw:
            version = raw.get("version")
            if not isinstance(version, int):
                raise ValueError(f"{self.task_types_file.name}: 'version' must be an integer")
            if is_v2 and version != 2:
                raise ValueError(
                    f"{self.task_types_file.name}: v2 config must set 'version' to 2 (got {version})"
                )
        raw_types = raw.get("task_types") if is_v2 else raw.get("types")
        if not isinstance(raw_types, list) or not raw_types:
            expected_key = "task_types" if is_v2 else "types"
            raise ValueError(f"{self.task_types_file.name}: '{expected_key}' must be a non-empty array")

        allowed_type_values = schema.allowed_types
        allowed_urgency_values = schema.allowed_urgencies
        seen_ids: set[str] = set()
        validated: list[HumanTaskType] = []

        type_loc_prefix = "task_types" if is_v2 else "types"
        for idx, entry in enumerate(raw_types):
            loc = f"{type_loc_prefix}[{idx}]"
            if not isinstance(entry, dict):
                raise ValueError(f"{loc}: each item must be an object")

            def _required_text(key: str) -> str:
                value = entry.get(key)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{loc}.{key}: must be a non-empty string")
                return value.strip()

            task_type_id = _required_text("id")
            if task_type_id in seen_ids:
                raise ValueError(f"{loc}.id: duplicate id '{task_type_id}'")
            seen_ids.add(task_type_id)

            category_raw = entry.get("category")
            if category_raw is None and not is_v2:
                category = str(entry.get("default_type") or "clarification").strip()
                category = category or "clarification"
            else:
                category = _required_text("category")

            default_type = _required_text("default_type")
            if default_type not in allowed_type_values:
                raise ValueError(
                    f"{loc}.default_type: invalid value '{default_type}', expected one of {sorted(allowed_type_values)}"
                )

            default_urgency = _required_text("default_urgency")
            if default_urgency not in allowed_urgency_values:
                raise ValueError(
                    f"{loc}.default_urgency: invalid value '{default_urgency}', expected one of {sorted(allowed_urgency_values)}"
                )

            required_fields = entry.get("required_fields")
            if (
                not isinstance(required_fields, list)
                or not required_fields
                or any(not isinstance(item, str) or not item.strip() for item in required_fields)
            ):
                raise ValueError(
                    f"{loc}.required_fields: must be a non-empty array of non-empty strings"
                )

            examples = entry.get("examples", [])
            if not isinstance(examples, list) or any(
                not isinstance(item, str) or not item.strip() for item in examples
            ):
                raise ValueError(f"{loc}.examples: must be an array of non-empty strings")

            who_can_do_it = entry.get("who_can_do_it", ["human"] if not is_v2 else None)
            if (
                not isinstance(who_can_do_it, list)
                or not who_can_do_it
                or any(not isinstance(item, str) or not item.strip() for item in who_can_do_it)
            ):
                raise ValueError(f"{loc}.who_can_do_it: must be a non-empty array of non-empty strings")
            normalized_who = [item.strip() for item in who_can_do_it]
            invalid_who = [item for item in normalized_who if item not in _ALLOWED_WHO_CAN_DO_IT]
            if invalid_who:
                raise ValueError(
                    f"{loc}.who_can_do_it: invalid value(s) {sorted(set(invalid_who))}, "
                    f"expected only {sorted(_ALLOWED_WHO_CAN_DO_IT)}"
                )

            validated.append(
                HumanTaskType(
                    id=task_type_id,
                    category=category,
                    description=_required_text("description"),
                    default_type=default_type,
                    default_urgency=default_urgency,
                    who_can_do_it=normalized_who,
                    required_fields=[field.strip() for field in required_fields],
                    when_to_use=_required_text("when_to_use"),
                    examples=[example.strip() for example in examples],
                )
            )

        defaults = raw.get("defaults", {})
        if defaults is None:
            defaults = {}
        if not isinstance(defaults, dict):
            raise ValueError(f"{self.task_types_file.name}: 'defaults' must be an object")
        fallback_task_type_id = defaults.get("fallback_task_type_id", _DEFAULT_TASK_TYPE_ID)
        if not isinstance(fallback_task_type_id, str) or not fallback_task_type_id.strip():
            raise ValueError(
                f"{self.task_types_file.name}: defaults.fallback_task_type_id must be a non-empty string"
            )
        fallback_task_type_id = fallback_task_type_id.strip()

        if _DEFAULT_TASK_TYPE_ID not in seen_ids:
            raise ValueError(
                f"{self.task_types_file.name}: must include a '{_DEFAULT_TASK_TYPE_ID}' task type"
            )
        types_by_id = {entry.id: entry for entry in validated}
        if fallback_task_type_id not in types_by_id:
            raise ValueError(
                f"{self.task_types_file.name}: defaults.fallback_task_type_id '{fallback_task_type_id}' "
                "must reference an existing task type"
            )
        if "human" not in types_by_id[fallback_task_type_id].who_can_do_it:
            raise ValueError(
                f"{self.task_types_file.name}: defaults.fallback_task_type_id '{fallback_task_type_id}' "
                "must reference a task type usable by a human"
            )

        raw_rules = raw.get("routing_rules", [])
        if raw_rules is None:
            raw_rules = []
        if not isinstance(raw_rules, list):
            raise ValueError(f"{self.task_types_file.name}: 'routing_rules' must be an array")
        routing_rules: list[HumanTaskRoutingRule] = []
        seen_rule_ids: set[str] = set()
        for idx, entry in enumerate(raw_rules):
            loc = f"routing_rules[{idx}]"
            if not isinstance(entry, dict):
                raise ValueError(f"{loc}: each item must be an object")

            rule_id = entry.get("id")
            if not isinstance(rule_id, str) or not rule_id.strip():
                raise ValueError(f"{loc}.id: must be a non-empty string")
            rule_id = rule_id.strip()
            if rule_id in seen_rule_ids:
                raise ValueError(f"{loc}.id: duplicate id '{rule_id}'")
            seen_rule_ids.add(rule_id)

            task_type_id = entry.get("task_type_id")
            if not isinstance(task_type_id, str) or not task_type_id.strip():
                raise ValueError(f"{loc}.task_type_id: must be a non-empty string")
            task_type_id = task_type_id.strip()
            if task_type_id not in types_by_id:
                raise ValueError(f"{loc}.task_type_id: unknown task type '{task_type_id}'")

            match = entry.get("match")
            if not isinstance(match, dict):
                raise ValueError(f"{loc}.match: must be an object")

            def _match_list(key: str) -> list[str]:
                raw_list = match.get(key, [])
                if raw_list is None:
                    return []
                if not isinstance(raw_list, list) or any(
                    not isinstance(item, str) or not item.strip() for item in raw_list
                ):
                    raise ValueError(f"{loc}.match.{key}: must be an array of non-empty strings")
                return [item.strip() for item in raw_list]

            reason_contains = _match_list("reason_contains")
            objective_contains = _match_list("objective_contains")
            if not reason_contains and not objective_contains:
                raise ValueError(
                    f"{loc}.match: must define at least one non-empty matcher list "
                    "(reason_contains or objective_contains)"
                )

            routing_rules.append(
                HumanTaskRoutingRule(
                    id=rule_id,
                    task_type_id=task_type_id,
                    reason_contains=reason_contains,
                    objective_contains=objective_contains,
                )
            )

        return HumanTaskTypeCatalog(
            task_types=validated,
            routing_rules=routing_rules,
            fallback_task_type_id=fallback_task_type_id,
            warnings=[],
        )

    def validate_task_types(self) -> list[HumanTaskType]:
        return self._load_task_type_catalog(strict=True).task_types

    def list_task_types(self) -> list[HumanTaskType]:
        return sorted(self.validate_task_types(), key=lambda item: item.id)

    def _match_routing_rule(
        self, rule: HumanTaskRoutingRule, *, reason: str, objective: str
    ) -> bool:
        reason_l = reason.lower()
        objective_l = objective.lower()
        if rule.reason_contains and not any(token.lower() in reason_l for token in rule.reason_contains):
            return False
        if rule.objective_contains and not any(
            token.lower() in objective_l for token in rule.objective_contains
        ):
            return False
        return True

    def _resolve_task_type_for_request(
        self,
        task: dict,
        reason: str,
        task_type_id: str | None,
    ) -> tuple[HumanTaskType, str, str | None]:
        catalog = self._load_task_type_catalog(strict=False)
        types = {entry.id: entry for entry in catalog.task_types}
        objective = str(task.get("objective", "") or "")
        warning = "; ".join(catalog.warnings) if catalog.warnings else None
        catalog_is_builtin_fallback = bool(catalog.warnings)

        selected_id: str | None = None
        selection_source = "fallback"
        explicit_id = (task_type_id or "").strip()
        task_field_id = str(task.get("human_task_type", "") or "").strip()

        if explicit_id:
            selected_id = explicit_id
            selection_source = "explicit"
        elif task_field_id:
            selected_id = task_field_id
            selection_source = "task_field"
        else:
            for rule in catalog.routing_rules:
                if self._match_routing_rule(rule, reason=reason, objective=objective):
                    selected_id = rule.task_type_id
                    selection_source = f"routing_rule:{rule.id}"
                    break
            if not selected_id:
                selected_id = catalog.fallback_task_type_id
                selection_source = "builtin_fallback" if catalog_is_builtin_fallback else "fallback"

        selected = types.get(selected_id)
        if selected is None:
            if selection_source in {"explicit", "task_field"}:
                raise ValueError(
                    f"unknown human task type '{selected_id}'. Available: {', '.join(sorted(types))}"
                )
            warning = (
                (warning + "; ") if warning else ""
            ) + f"Selected task type '{selected_id}' was not found; using built-in fallback."
            builtin = self._builtin_fallback_catalog()
            return builtin.task_types[0], "builtin_fallback", warning

        if "human" not in selected.who_can_do_it:
            fallback = types.get(catalog.fallback_task_type_id)
            if fallback is None or "human" not in fallback.who_can_do_it:
                builtin = self._builtin_fallback_catalog()
                warning = (
                    (warning + "; ") if warning else ""
                ) + (
                    f"Selected task type '{selected.id}' is not human-capable; "
                    "using built-in fallback."
                )
                return builtin.task_types[0], "builtin_fallback", warning

            warning = (
                (warning + "; ") if warning else ""
            ) + (
                f"Selected task type '{selected.id}' is not human-capable; "
                f"using fallback '{fallback.id}'."
            )
            return fallback, "fallback", warning

        return selected, selection_source, warning

    def append_request(
        self,
        task: dict,
        reason: str,
        diagnosis_packet: dict | None = None,
        run_id: str | None = None,
        task_type_id: str | None = None,
    ) -> str:
        self.ensure_queue()
        diagnosis_packet = diagnosis_packet or {}
        diff_summary = diagnosis_packet.get("diff_summary", {})
        request_id = f"hr-{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        task_id = task.get("id")
        task_type, selection_source, config_warning = self._resolve_task_type_for_request(
            task, reason, task_type_id
        )
        deliverables = "".join(f"  - {field}\n" for field in task_type.required_fields)
        config_warning_line = (
            f"TASK_TYPE_CONFIG_WARNING: {config_warning}\n" if config_warning else ""
        )
        config_warning_why = (
            f"  - Task type config warning: {config_warning}\n" if config_warning else ""
        )

        request = (
            f"REQUEST_ID: {request_id}\n"
            f"TASK_ID: {task_id}\n"
            f"RUN_ID: {run_id or ''}\n"
            "STATUS: pending\n"
            f"CREATED_AT: {now}\n"
            "HUMAN_REQUEST:\n"
            f"TYPE: {task_type.default_type}\n"
            f"URGENCY: {task_type.default_urgency}\n"
            "TIME_REQUIRED_MIN: 15\n"
            f"CONTEXT: Task {task_id} escalated.\n"
            f"TASK_TYPE_ID: {task_type.id}\n"
            f"TASK_CATEGORY: {task_type.category}\n"
            f"TASK_TYPE_SELECTION_SOURCE: {selection_source}\n"
            f"WHO_CAN_DO_IT: {','.join(task_type.who_can_do_it)}\n"
            f"{config_warning_line}"
            f"TASK_TYPE_WHEN_TO_USE: {task_type.when_to_use}\n"
            "DELIVERABLES:\n"
            f"{deliverables}"
            "OPTIONS:\n"
            "  - Approve latest approach\n"
            "  - Redirect implementation approach\n"
            "RECOMMENDATION: Redirect implementation approach\n"
            "WHY:\n"
            f"  - Escalation trigger: {reason}\n"
            "  - Automated loop reached termination condition\n"
            f"{config_warning_why}"
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
