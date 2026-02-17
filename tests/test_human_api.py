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


def _store_with_codex(tmp_path: Path) -> tuple[CodexStore, HumanAPI]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    codex = repo / "codex"
    codex.mkdir(parents=True)
    (codex / "04_HUMAN_API").mkdir(parents=True)
    (codex / "10_OVERSEER" / "locks").mkdir(parents=True, exist_ok=True)
    (codex / "04_HUMAN_API" / "REQUEST_SCHEMA.md").write_text(SCHEMA_TEXT, encoding="utf-8")
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
