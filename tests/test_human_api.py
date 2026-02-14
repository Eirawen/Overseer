"""Tests for overseer.human_api: ensure_queue, append_request, generate_brief."""

from __future__ import annotations

from pathlib import Path

from overseer.codex_store import CodexStore, EMPTY_HUMAN_QUEUE
from overseer.human_api import HumanAPI


def _store_with_codex(tmp_path: Path) -> tuple[CodexStore, HumanAPI]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    codex = repo / "codex"
    codex.mkdir(parents=True)
    (codex / "04_HUMAN_API").mkdir(parents=True)
    (codex / "10_OVERSEER" / "locks").mkdir(parents=True, exist_ok=True)
    store = CodexStore(repo)
    store.codex_root = codex
    return store, HumanAPI(store)


def test_ensure_queue_creates_file_when_missing(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    assert not api.queue_file.exists()
    api.ensure_queue()
    assert api.queue_file.exists()
    assert api.queue_file.read_text(encoding="utf-8") == EMPTY_HUMAN_QUEUE


def test_ensure_queue_does_not_overwrite_existing(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.queue_file.write_text("# Custom\n", encoding="utf-8")
    api.ensure_queue()
    assert api.queue_file.read_text(encoding="utf-8") == "# Custom\n"


def test_append_request_adds_content(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.ensure_queue()
    out = api.append_request(
        {"id": "task-abc"},
        "tests failed",
        {"last_exit_code": 1, "codex_log_tail": "error"},
    )
    assert "HUMAN_REQUEST:" in out
    assert "task-abc" in out
    assert "tests failed" in out
    assert "last_exit_code" in out
    content = api.queue_file.read_text(encoding="utf-8")
    assert "HUMAN_REQUEST:" in content
    assert "task-abc" in content


def test_append_request_uses_defaults_when_diagnosis_none(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.ensure_queue()
    api.append_request({"id": "task-x"}, "reason", None)
    content = api.queue_file.read_text(encoding="utf-8")
    assert "unknown" in content or "(missing)" in content


def test_generate_brief_includes_counts_and_path(tmp_path: Path) -> None:
    _, api = _store_with_codex(tmp_path)
    api.ensure_queue()
    brief = api.generate_brief(
        [{"id": "t1"}, {"id": "t2"}],
        [{"id": "t3"}],
    )
    assert "queued: 2" in brief
    assert "escalated: 1" in brief
    assert "human_queue" in brief or "HUMAN_QUEUE" in brief
    assert "Morning Brief" in brief
