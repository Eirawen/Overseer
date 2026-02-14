from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "overseer", "--repo-root", str(repo), *args],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )


def test_init_validates_codex_structure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "codex").mkdir(parents=True)

    run_cli(repo, "init")

    assert (repo / "codex" / "03_WORK" / "TASK_GRAPH.jsonl").exists()
    assert (repo / "codex" / "10_OVERSEER").exists()
    assert (repo / "codex" / "11_WORKERS" / "builder").exists()


def test_init_copies_real_canonical_docs_and_keeps_queue_empty(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "codex" / "PROJECT").mkdir(parents=True)
    (repo / "codex" / "MEMORY").mkdir(parents=True)
    (repo / "codex" / "HUMAN_API").mkdir(parents=True)
    (repo / "codex" / "AGENTS").mkdir(parents=True)

    (repo / "codex" / "PROJECT" / "OPERATING_MODE.md").write_text("REAL MODE\n", encoding="utf-8")
    (repo / "codex" / "MEMORY" / "DECISION_LOG.md").write_text("REAL DECISIONS\n", encoding="utf-8")
    (repo / "codex" / "HUMAN_API" / "REQUEST_SCHEMA.md").write_text("REAL REQUEST SCHEMA\n", encoding="utf-8")
    (repo / "codex" / "AGENTS" / "TERMINATION.md").write_text("max review cycles per task: 3\n", encoding="utf-8")

    run_cli(repo, "init")

    assert (repo / "codex" / "01_PROJECT" / "OPERATING_MODE.md").read_text(encoding="utf-8") == "REAL MODE\n"
    assert (repo / "codex" / "04_HUMAN_API" / "REQUEST_SCHEMA.md").read_text(encoding="utf-8") == "REAL REQUEST SCHEMA\n"
    queue = (repo / "codex" / "04_HUMAN_API" / "HUMAN_QUEUE.md").read_text(encoding="utf-8")
    assert "(empty)" in queue
    assert "HUMAN_REQUEST:" not in queue


def test_add_task_appends_valid_jsonl(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")

    result = run_cli(repo, "add-task", "scaffold sanity check")
    task_id = result.stdout.strip()

    lines = (repo / "codex" / "03_WORK" / "TASK_GRAPH.jsonl").read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[-1])
    assert payload["id"] == task_id
    assert payload["status"] == "queued"
    assert payload["objective"] == "scaffold sanity check"
    assert "created_at" in payload


def test_run_writes_run_log_updates_status_and_worker_notes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    task_id = run_cli(repo, "add-task", "normal objective").stdout.strip()

    run_cli(repo, "run", "--task", task_id)

    tasks = [json.loads(line) for line in (repo / "codex" / "03_WORK" / "TASK_GRAPH.jsonl").read_text(encoding="utf-8").splitlines() if line]
    task = next(t for t in tasks if t["id"] == task_id)
    assert task["status"] == "done"

    logs = [json.loads(line) for line in (repo / "codex" / "08_TELEMETRY" / "RUN_LOG.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert logs[-1]["task_id"] == task_id
    assert logs[-1]["status"] == "done"

    assert task_id in (repo / "codex" / "11_WORKERS" / "builder" / "NOTES.md").read_text(encoding="utf-8")


def test_escalation_writes_human_request_format(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    task_id = run_cli(repo, "add-task", "force-test-fail scenario").stdout.strip()

    run_cli(repo, "run", "--task", task_id)

    queue = (repo / "codex" / "04_HUMAN_API" / "HUMAN_QUEUE.md").read_text(encoding="utf-8")
    assert "HUMAN_REQUEST:" in queue
    assert "TYPE:" in queue
    assert "REPLY_FORMAT:" in queue
    assert task_id in queue


def test_disagreement_escalates_after_two_disputes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    task_id = run_cli(repo, "add-task", "force-escalate-disagreement").stdout.strip()

    run_cli(repo, "run", "--task", task_id)

    tasks = [json.loads(line) for line in (repo / "codex" / "03_WORK" / "TASK_GRAPH.jsonl").read_text(encoding="utf-8").splitlines() if line]
    task = next(t for t in tasks if t["id"] == task_id)
    assert task["status"] == "escalated"

    logs = [json.loads(line) for line in (repo / "codex" / "08_TELEMETRY" / "RUN_LOG.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert logs[-1]["verifier_disputes"] >= 2


def test_termination_policy_is_loaded_from_codex_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")

    termination = repo / "codex" / "05_AGENTS" / "TERMINATION.md"
    termination.write_text(
        "Hard limits:\n"
        "- max review cycles per task: 1\n"
        "- if Reviewer and Verifier disagree one => escalate to human\n"
        "- if tests fail one without progress => escalate to human with diagnosis packet\n",
        encoding="utf-8",
    )
    task_id = run_cli(repo, "add-task", "force-escalate-disagreement").stdout.strip()

    run_cli(repo, "run", "--task", task_id)

    logs = [json.loads(line) for line in (repo / "codex" / "08_TELEMETRY" / "RUN_LOG.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert logs[-1]["status"] == "escalated"
    assert logs[-1]["cycle_count"] == 1
