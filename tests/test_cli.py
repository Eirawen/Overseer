from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def run_cli(
    repo: Path, *args: str, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    run_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    run_env.setdefault("OVERSEER_EXECUTION_BACKEND", "local")
    if env:
        run_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "overseer", "--repo-root", str(repo), *args],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=check,
        env=run_env,
    )


def init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True, text=True
    )
    (repo / ".gitignore").write_text(".pytest_cache/\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", ".gitignore"], cwd=repo, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True
    )


def _fake_codex_script(bin_dir: Path, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "codex"
    script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body + "\n", encoding="utf-8")
    script.chmod(0o755)


def test_run_agent_and_status(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    task_id = run_cli(repo, "add-task", "integration objective").stdout.strip()

    bin_dir = tmp_path / "bin"
    _fake_codex_script(bin_dir, 'echo "ok"\n')
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    run_id = run_cli(repo, "run-agent", "--task", task_id, env=env).stdout.strip()

    deadline = time.time() + 10
    status_output = ""
    while time.time() < deadline:
        status_output = run_cli(repo, "runs", "show", "--run", run_id, env=env).stdout
        if "status=done" in status_output or "status=failed" in status_output:
            break
        time.sleep(0.1)

    assert "task=" in status_output
    runs_output = run_cli(repo, "runs", "list", env=env).stdout
    assert run_id in runs_output


def test_integrate_sets_escalated_when_codex_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    task_id = run_cli(repo, "add-task", "integration objective").stdout.strip()

    git_dir = str(Path(shutil.which("git") or "").parent)
    result = run_cli(repo, "integrate", "--task", task_id, check=False, env={"PATH": git_dir})
    assert result.returncode != 0

    queue = (repo / "codex" / "04_HUMAN_API" / "HUMAN_QUEUE.md").read_text(encoding="utf-8")
    assert "Install steps:" in queue


def test_requires_git_repository_context(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / "codex").mkdir(parents=True)

    result = run_cli(repo, "init", check=False)
    assert result.returncode != 0
    assert "Not inside a git repository" in result.stderr


def test_init_prints_message(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    result = run_cli(repo, "init")
    assert result.returncode == 0
    assert "Initialized" in result.stdout or "initialized" in result.stdout.lower()


def test_add_task_prints_task_id(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    result = run_cli(repo, "add-task", "my objective")
    assert result.returncode == 0
    task_id = result.stdout.strip()
    assert task_id.startswith("task-")
    assert len(task_id) == 17  # task- (5) + 12 hex


def test_brief_prints_queued_and_escalated(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")
    run_cli(repo, "add-task", "first")
    run_cli(repo, "add-task", "second")
    result = run_cli(repo, "brief")
    assert result.returncode == 0
    assert "queued" in result.stdout.lower()
    assert "2" in result.stdout or "escalated" in result.stdout.lower()


def test_run_cancel_command(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")

    run_id = "run-cancel-cli"
    run_dir = repo / "codex" / "08_TELEMETRY" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_json = '{"task_id":"task-1","command":[],"cwd":".","stdout_log":"stdout.log","stderr_log":"stderr.log","meta_path":"meta.json","lock_path":"lock"}'

    import sqlite3

    db = repo / "codex" / "08_TELEMETRY" / "overseer.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, task_id TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, heartbeat_at TEXT, backend_type TEXT NOT NULL, worktree_path TEXT NOT NULL, pid INTEGER, exit_code INTEGER, failure_reason TEXT, meta_json TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS run_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, type TEXT NOT NULL, at TEXT NOT NULL, payload_json TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO runs (run_id, task_id, status, created_at, updated_at, heartbeat_at, backend_type, worktree_path, pid, exit_code, failure_reason, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, "task-1", "queued", "2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z", "local", ".", None, None, None, meta_json),
    )
    conn.commit()
    conn.close()

    cancel_output = run_cli(repo, "runs", "cancel", "--run", run_id).stdout
    assert f"{run_id} task=" in cancel_output
    assert "status=canceled" in cancel_output


def test_human_commands_list_show_resolve(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")

    schema = repo / "codex" / "04_HUMAN_API" / "REQUEST_SCHEMA.md"
    schema.write_text(
        (
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
        ),
        encoding="utf-8",
    )

    task_id = run_cli(repo, "add-task", "integration objective").stdout.strip()
    run_cli(repo, "integrate", "--task", task_id, check=False, env={"PATH": str(Path(shutil.which("git") or "").parent)})

    listed = run_cli(repo, "human", "list").stdout.strip().splitlines()
    assert listed
    request_id = listed[0].split()[0]

    show_output = run_cli(repo, "human", "show", "--id", request_id).stdout
    assert "REQUEST_ID:" in show_output

    validate_output = run_cli(repo, "human-types", "validate").stdout
    assert "valid" in validate_output

    types_output = run_cli(repo, "human-types", "list").stdout
    assert "decision" in types_output

    resolve_output = run_cli(
        repo,
        "human",
        "resolve",
        "--id",
        request_id,
        "--choice",
        "Redirect implementation approach",
        "--rationale",
        "Install codex first",
    ).stdout
    assert "resolved" in resolve_output

    second = run_cli(
        repo,
        "human",
        "resolve",
        "--id",
        request_id,
        "--choice",
        "Redirect implementation approach",
        "--rationale",
        "Install codex first",
        check=False,
    )
    assert second.returncode != 0
    assert "already resolved" in second.stderr


def test_human_types_validate_reports_config_errors(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")

    types_config = repo / "codex" / "04_HUMAN_API" / "HUMAN_TASK_TYPES.json"
    types_config.write_text('{"types":[{"id":"decision"}]}\n', encoding="utf-8")

    result = run_cli(repo, "human-types", "validate", check=False)
    assert result.returncode != 0
    assert "must be a non-empty string" in result.stderr



def test_human_resolve_rejects_invalid_choice(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")

    schema = repo / "codex" / "04_HUMAN_API" / "REQUEST_SCHEMA.md"
    schema.write_text(
        (
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
        ),
        encoding="utf-8",
    )

    task_id = run_cli(repo, "add-task", "integration objective").stdout.strip()
    run_cli(repo, "integrate", "--task", task_id, check=False, env={"PATH": str(Path(shutil.which("git") or "").parent)})

    request_id = run_cli(repo, "human", "list").stdout.strip().splitlines()[0].split()[0]
    bad_resolve = run_cli(
        repo,
        "human",
        "resolve",
        "--id",
        request_id,
        "--choice",
        "Not an option",
        "--rationale",
        "no",
        check=False,
    )
    assert bad_resolve.returncode != 0
    assert "choice must be one of" in bad_resolve.stderr


def test_chat_accepts_commands_while_run_active(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")

    bin_dir = tmp_path / "bin"
    _fake_codex_script(bin_dir, 'sleep 2\necho "ok"')
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["OVERSEER_EXECUTION_BACKEND"] = "local"

    proc = subprocess.run(
        [sys.executable, "-m", "overseer", "--repo-root", str(repo), "chat"],
        cwd=Path(__file__).resolve().parents[1],
        input="ship objective\n/run list\n/quit\n",
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=15,
    )

    assert proc.returncode == 0
    assert "Overseer chat started" in proc.stdout
    assert "Run IDs:" in proc.stdout
    assert "status=" in proc.stdout
    assert "Session ended." in proc.stdout


def test_chat_reports_command_errors_and_continues(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "codex").mkdir(parents=True)
    run_cli(repo, "init")

    proc = subprocess.run(
        [sys.executable, "-m", "overseer", "--repo-root", str(repo), "chat"],
        cwd=Path(__file__).resolve().parents[1],
        input="/run status\n/quit\n",
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "OVERSEER_EXECUTION_BACKEND": "local"},
        timeout=15,
    )

    assert proc.returncode == 0
    assert "error: usage: /run" in proc.stdout
    assert "Session ended." in proc.stdout
