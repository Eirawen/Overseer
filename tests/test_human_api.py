"""Tests for strict Human API request parsing and request resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from overseer.codex_store import CodexStore, EMPTY_HUMAN_QUEUE
from overseer.human_api import HumanAPI


SCHEMA_TEXT = (
    "# Human Request Schema (strict)\n\n"
    "HUMAN_REQUEST:\n"
    "TYPE: {design_direction | decision | external_action | clarification | review}\n"
    "URGENCY: {low | medium | high | interrupt_now}\n"
    "TIME_REQUIRED_MIN: <int>\n"
    "CONTEXT: <short>\n"
    "OPTIONS:\n"
    "  - <option A>\n"
    "  - <option B>\n"
    "RECOMMENDATION: <one of options or custom>\n"
    "WHY: <1-3 bullets>\n"
    "UNBLOCKS: <what changes after you answer>\n"
    "REPLY_FORMAT: <exact expected reply>\n"
)

TASK_TYPES_TEXT = {
    "version": 2,
    "defaults": {"fallback_task_type_id": "decision"},
    "task_types": [
        {
            "id": "decision",
            "category": "clarification",
            "description": "General tradeoff and approval decisions.",
            "default_type": "decision",
            "default_urgency": "high",
            "who_can_do_it": ["human"],
            "required_fields": ["CONTEXT", "OPTIONS", "RECOMMENDATION"],
            "when_to_use": "Use for unresolved forks.",
            "examples": ["Approve a migration strategy"],
        },
        {
            "id": "game_asset_request",
            "category": "external_action",
            "description": "Request a game asset from a human.",
            "default_type": "external_action",
            "default_urgency": "medium",
            "who_can_do_it": ["human"],
            "required_fields": ["asset_name", "target_format"],
            "when_to_use": "Use when blocked on an externally created asset.",
            "examples": ["Need icon for pause menu"],
        },
    ],
    "routing_rules": [],
}


def _store_with_codex(tmp_path: Path) -> tuple[CodexStore, HumanAPI]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    codex = repo / "codex"
    codex.mkdir(parents=True)
    (codex / "04_HUMAN_API").mkdir(parents=True)
    (codex / "10_OVERSEER" / "locks").mkdir(parents=True, exist_ok=True)
    (codex / "04_HUMAN_API" / "REQUEST_SCHEMA.md").write_text(SCHEMA_TEXT, encoding="utf-8")
    (codex / "04_HUMAN_API" / "HUMAN_TASK_TYPES.json").write_text(
        json.dumps(TASK_TYPES_TEXT, indent=2),
        encoding="utf-8",
    )
    store = CodexStore(repo)
    store.codex_root = codex
    return store, HumanAPI(store)


def _write_request(path: Path, *, request_id: str = "hr-123456789abc", body: str = "") -> None:
    path.write_text(
        (
            f"REQUEST_ID: {request_id}\n"
            "TASK_ID: task-1\n"
            "RUN_ID: run-1\n"
            "STATUS: pending\n"
            "CREATED_AT: now\n"
            "HUMAN_REQUEST:\n"
            "TYPE: decision\n"
            "URGENCY: high\n"
            "TIME_REQUIRED_MIN: 2\n"
            "CONTEXT: bad\n"
            "OPTIONS:\n"
            "  - a\n"
            "  - b\n"
            "RECOMMENDATION: custom rec\n"
            "WHY:\n"
            "  - because\n"
            "UNBLOCKS: x\n"
            "REPLY_FORMAT: y\n"
            f"{body}"
        ),
        encoding="utf-8",
    )


def test_ensure_queue_creates_file_when_missing(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    assert not api.queue_file.exists()
    api.ensure_queue()
    assert api.queue_file.exists()
    assert api.queue_file.read_text(encoding="utf-8") == EMPTY_HUMAN_QUEUE


def test_append_and_parse_request_validates_schema(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.ensure_queue()
    request_text = api.append_request(
        {"id": "task-abc"},
        "tests failed",
        {"last_exit_code": 1, "codex_log_tail": "error"},
        run_id="run-123",
    )
    assert "HUMAN_REQUEST:" in request_text

    request = api.list_requests()[0]
    assert request.task_id == "task-abc"
    assert request.run_id == "run-123"
    assert request.request_type == "decision"
    assert request.urgency == "high"


def test_append_request_uses_selected_task_type_defaults(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.ensure_queue()
    request_text = api.append_request(
        {"id": "task-game", "human_task_type": "game_asset_request"},
        "blocked on art",
    )

    assert "TYPE: external_action" in request_text
    assert "URGENCY: medium" in request_text
    assert "TASK_TYPE_ID: game_asset_request" in request_text

    request = api.list_requests()[0]
    assert request.request_type == "external_action"
    assert request.urgency == "medium"


def test_append_request_uses_routing_rule_category_and_type_metadata(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.task_types_file.write_text(
        json.dumps(
            {
                "version": 2,
                "defaults": {"fallback_task_type_id": "decision"},
                "task_types": [
                    {
                        "id": "decision",
                        "category": "clarification",
                        "description": "General tradeoff and approval decisions.",
                        "default_type": "decision",
                        "default_urgency": "high",
                        "who_can_do_it": ["human"],
                        "required_fields": ["CONTEXT", "OPTIONS", "RECOMMENDATION"],
                        "when_to_use": "Use for unresolved forks.",
                        "examples": ["Approve a migration strategy"],
                    },
                    {
                        "id": "notes_review",
                        "category": "review",
                        "description": "Review missing notes failures.",
                        "default_type": "review",
                        "default_urgency": "medium",
                        "who_can_do_it": ["human"],
                        "required_fields": ["failure_reason", "expected_note_location", "remediation_choice"],
                        "when_to_use": "Use for notes policy failures.",
                        "examples": ["missing required notes"],
                    },
                ],
                "routing_rules": [
                    {
                        "id": "missing-required-notes",
                        "task_type_id": "notes_review",
                        "match": {"reason_contains": ["missing required notes"], "objective_contains": []},
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    api.ensure_queue()
    request_text = api.append_request({"id": "task-1"}, "missing required notes", run_id="run-1")

    assert "TASK_TYPE_ID: notes_review" in request_text
    assert "TASK_CATEGORY: review" in request_text
    assert "TYPE: review" in request_text
    assert "URGENCY: medium" in request_text
    assert "TASK_TYPE_SELECTION_SOURCE: routing_rule:missing-required-notes" in request_text


def test_append_request_runtime_invalid_config_falls_back_with_warning(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.task_types_file.write_text('{"version":2,"task_types":[]}\n', encoding="utf-8")
    api.ensure_queue()

    request_text = api.append_request({"id": "task-bad-config"}, "blocked")

    assert "TASK_TYPE_ID: decision" in request_text
    assert "TASK_TYPE_SELECTION_SOURCE: builtin_fallback" in request_text
    assert "TASK_TYPE_CONFIG_WARNING:" in request_text
    assert "TYPE: decision" in request_text


def test_append_request_runtime_missing_config_falls_back_with_warning(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.task_types_file.unlink()
    api.ensure_queue()

    request_text = api.append_request({"id": "task-missing-config"}, "blocked")

    assert "TASK_TYPE_ID: decision" in request_text
    assert "TASK_TYPE_SELECTION_SOURCE: builtin_fallback" in request_text
    assert "TASK_TYPE_CONFIG_WARNING:" in request_text
    assert "missing human task types config" in request_text


def test_append_request_agent_only_selected_type_falls_back_to_human(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.task_types_file.write_text(
        json.dumps(
            {
                "version": 2,
                "defaults": {"fallback_task_type_id": "decision"},
                "task_types": [
                    {
                        "id": "decision",
                        "category": "clarification",
                        "description": "General tradeoff and approval decisions.",
                        "default_type": "decision",
                        "default_urgency": "high",
                        "who_can_do_it": ["human"],
                        "required_fields": ["CONTEXT", "OPTIONS", "RECOMMENDATION"],
                        "when_to_use": "Use for unresolved forks.",
                        "examples": ["Approve a migration strategy"],
                    },
                    {
                        "id": "agent_triage",
                        "category": "review",
                        "description": "Internal agent triage only.",
                        "default_type": "review",
                        "default_urgency": "low",
                        "who_can_do_it": ["agent"],
                        "required_fields": ["triage_note"],
                        "when_to_use": "Use for agent-only triage.",
                        "examples": ["agent-only"],
                    },
                ],
                "routing_rules": [
                    {
                        "id": "route-agent-only",
                        "task_type_id": "agent_triage",
                        "match": {"reason_contains": ["agent-only"], "objective_contains": []},
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    api.ensure_queue()

    request_text = api.append_request({"id": "task-agent-fallback"}, "agent-only routing trigger")

    assert "TASK_TYPE_ID: decision" in request_text
    assert "TASK_TYPE_SELECTION_SOURCE: fallback" in request_text
    assert "TASK_TYPE_CONFIG_WARNING:" in request_text
    assert "WHO_CAN_DO_IT: human" in request_text


def test_parse_request_fails_with_useful_error(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.ensure_queue()
    path = api.requests_dir / "hr-123456789abc.md"
    _write_request(path, body="TYPE: nope\n")

    with pytest.raises(ValueError, match="invalid TYPE"):
        api.parse_request(path)


def test_parse_request_allows_custom_recommendation(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.ensure_queue()
    path = api.requests_dir / "hr-123456789abc.md"
    _write_request(path)

    parsed = api.parse_request(path)
    assert parsed.recommendation == "custom rec"


def test_parse_request_rejects_invalid_status_and_why_count(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.ensure_queue()
    path = api.requests_dir / "hr-123456789abc.md"
    _write_request(
        path,
        body=(
            "STATUS: unknown\n"
            "WHY:\n"
            "  - one\n"
            "  - two\n"
            "  - three\n"
            "  - four\n"
        ),
    )

    with pytest.raises(ValueError, match="invalid STATUS"):
        api.parse_request(path)


def test_schema_validation_requires_all_keys(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.schema_file.write_text("TYPE: {decision}\nURGENCY: {high}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required keys"):
        api._load_schema()


def test_task_types_validation_catches_friendly_errors(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.task_types_file.write_text(
        json.dumps(
            {
                "types": [
                    {
                        "id": "decision",
                        "description": "ok",
                        "default_type": "decision",
                        "default_urgency": "urgent",
                        "required_fields": ["x"],
                        "when_to_use": "when",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="default_urgency"):
        api.validate_task_types()


def test_task_types_validation_supports_legacy_v1_types_shape(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.task_types_file.write_text(
        json.dumps(
            {
                "types": [
                    {
                        "id": "decision",
                        "description": "ok",
                        "default_type": "decision",
                        "default_urgency": "high",
                        "required_fields": ["x"],
                        "when_to_use": "when",
                        "examples": ["e1"],
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    task_types = api.validate_task_types()
    assert len(task_types) == 1
    assert task_types[0].id == "decision"
    assert task_types[0].category == "decision"
    assert task_types[0].who_can_do_it == ["human"]


def test_task_types_validation_rejects_unknown_routing_task_type(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.task_types_file.write_text(
        json.dumps(
            {
                "version": 2,
                "defaults": {"fallback_task_type_id": "decision"},
                "task_types": TASK_TYPES_TEXT["task_types"],
                "routing_rules": [
                    {
                        "id": "bad-route",
                        "task_type_id": "does_not_exist",
                        "match": {"reason_contains": ["x"], "objective_contains": []},
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="routing_rules\\[0\\]\\.task_type_id: unknown task type"):
        api.validate_task_types()


def test_task_types_validation_rejects_non_human_fallback_task_type(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.task_types_file.write_text(
        json.dumps(
            {
                "version": 2,
                "defaults": {"fallback_task_type_id": "agent_triage"},
                "task_types": [
                    *TASK_TYPES_TEXT["task_types"],
                    {
                        "id": "agent_triage",
                        "category": "review",
                        "description": "Agent-only triage.",
                        "default_type": "review",
                        "default_urgency": "low",
                        "who_can_do_it": ["agent"],
                        "required_fields": ["triage_note"],
                        "when_to_use": "Use internally.",
                        "examples": ["agent-only"],
                    },
                ],
                "routing_rules": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fallback_task_type_id 'agent_triage'.*usable by a human"):
        api.validate_task_types()


def test_task_types_validation_rejects_wrong_v2_version(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    bad = dict(TASK_TYPES_TEXT)
    bad["version"] = 3
    api.task_types_file.write_text(json.dumps(bad, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="v2 config must set 'version' to 2"):
        api.validate_task_types()


def test_resolve_request_writes_resolution_and_events(tmp_path: Path) -> None:
    store, api = _store_with_codex(tmp_path)
    api.ensure_queue()
    api.append_request({"id": "task-1"}, "blocked", run_id="run-1")
    request = api.list_requests()[0]

    run_dir = api.codex_store.codex_root / "08_TELEMETRY" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        '{"type":"started","at":"2020-01-01T00:00:00Z","payload":{"record":{"run_id":"run-1"}}}\n',
        encoding="utf-8",
    )
    (store.repo_root / ".overseer_resume_policy").write_text("auto\n", encoding="utf-8")

    artifact = tmp_path / "artifact.txt"
    artifact.write_text("ok\n", encoding="utf-8")
    resolution_path = api.resolve_request(
        request.request_id,
        "Redirect implementation approach",
        "Safer fix.",
        artifact_path=str(artifact),
    )

    assert resolution_path.exists()
    resolution = json.loads(resolution_path.read_text(encoding="utf-8"))
    assert resolution["run_id"] == "run-1"
    assert resolution["artifact_path"] == str(artifact)

    events_text = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "human_resolved" in events_text
    assert "resume_requested" in events_text


def test_resolve_rejects_missing_artifact_and_empty_rationale(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.ensure_queue()
    api.append_request({"id": "task-2"}, "blocked")
    request = api.list_requests()[0]

    with pytest.raises(ValueError, match="rationale cannot be empty"):
        api.resolve_request(request.request_id, "Redirect implementation approach", " ")

    with pytest.raises(ValueError, match="artifact path does not exist"):
        api.resolve_request(
            request.request_id,
            "Redirect implementation approach",
            "Need redirect",
            artifact_path=str(tmp_path / "missing.txt"),
        )


def test_resolve_is_not_idempotent(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.ensure_queue()
    api.append_request({"id": "task-2"}, "blocked")
    request = api.list_requests()[0]

    api.resolve_request(request.request_id, "Redirect implementation approach", "Need redirect")
    with pytest.raises(ValueError, match="already resolved"):
        api.resolve_request(request.request_id, "Redirect implementation approach", "Need redirect")
