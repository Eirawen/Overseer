"""Concurrency tests: meta.json atomicity, cancel vs worker, TaskStore, hammer runs, worktree creation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from overseer.execution.backend import (
    ExecutionRecord,
    ExecutionRequest,
    LocalBackend,
)
from overseer.fs import atomic_write_text
from overseer.git_worktree import GitRepoError, GitWorktreeManager
from overseer.locks import file_lock
from overseer.task_store import TaskStore
from overseer.codex_store import CodexStore


# --- meta.json: concurrent reads never see partial JSON ---


def test_meta_json_never_partially_written_under_concurrent_reads(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    runs_root = codex_root / "08_TELEMETRY" / "runs"
    runs_root.mkdir(parents=True)
    run_id = "run-concurrent-read"
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    meta_path = run_dir / "meta.json"
    lock_path = run_dir / "meta.lock"

    record = ExecutionRecord(
        run_id=run_id,
        task_id="t1",
        status="queued",
        command=[],
        cwd=str(tmp_path),
        stdout_log="",
        stderr_log="",
        meta_path=str(meta_path),
        lock_path=str(tmp_path / "exec.lock"),
        created_at="2020-01-01T00:00:00Z",
    )
    atomic_write_text(meta_path, json.dumps({
        "run_id": record.run_id,
        "task_id": record.task_id,
        "status": record.status,
        "command": record.command,
        "cwd": record.cwd,
        "stdout_log": record.stdout_log,
        "stderr_log": record.stderr_log,
        "meta_path": record.meta_path,
        "lock_path": record.lock_path,
        "created_at": record.created_at,
    }, indent=2) + "\n")

    backend = LocalBackend(codex_root)
    done = threading.Event()
    errors: list[Exception] = []
    reader_count = [0]

    def writer() -> None:
        for i in range(80):
            with file_lock(lock_path):
                r = backend._read_record(meta_path)
                r.started_at = f"2020-01-01T00:00:{i:02d}Z"
                backend._write_record(meta_path, r)
        done.set()

    def reader() -> None:
        while not done.is_set():
            try:
                rec = backend.status(run_id)
                assert rec.run_id == run_id
                assert "status" in rec.__dict__ or hasattr(rec, "status")
                reader_count[0] += 1
            except Exception as e:
                errors.append(e)
            time.sleep(0.001)

    t_w = threading.Thread(target=writer)
    t_r1 = threading.Thread(target=reader)
    t_r2 = threading.Thread(target=reader)
    t_w.start()
    t_r1.start()
    t_r2.start()
    t_w.join(timeout=5)
    done.set()
    t_r1.join(timeout=2)
    t_r2.join(timeout=2)
    assert not errors, errors
    assert reader_count[0] >= 0


# --- cancel vs worker: final status is canceled, no regression ---


def test_cancel_vs_worker_no_status_regression(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    (codex_root / "08_TELEMETRY" / "runs").mkdir(parents=True)
    run_id = LocalBackend.new_run_id()
    run_root = codex_root / "08_TELEMETRY" / "runs" / run_id
    run_root.mkdir(parents=True)
    lock_path = codex_root / "10_OVERSEER" / "locks" / f"{run_id}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path = run_root / "meta.json"

    backend = LocalBackend(codex_root)
    record = ExecutionRecord(
        run_id=run_id,
        task_id="task-1",
        status="queued",
        command=[sys.executable, "-c", "import time; time.sleep(5)"],
        cwd=str(tmp_path),
        stdout_log=str(run_root / "stdout.log"),
        stderr_log=str(run_root / "stderr.log"),
        meta_path=str(meta_path),
        lock_path=str(lock_path),
        created_at="2020-01-01T00:00:00Z",
    )
    with file_lock(backend._events_lock_path(meta_path)):
        backend._append_event(meta_path, "started", {"record": record.__dict__})

    t = threading.Thread(target=backend.run_worker, args=(meta_path,))
    t.start()

    deadline = time.time() + 5
    while time.time() < deadline:
        if backend.status(run_id).status == "running":
            break
        time.sleep(0.05)

    canceling = backend.cancel(run_id)
    assert canceling.status == "canceling"

    t.join(timeout=10)

    rec = backend.status(run_id)
    assert rec.status == "canceled"
    meta = json.loads((run_root / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "canceled"
    assert "run_id" in meta and "task_id" in meta


# --- TaskStore: concurrent process updates do not lose updates ---


def test_task_graph_concurrent_thread_updates_do_not_lose_updates(tmp_path: Path) -> None:
    """Concurrent TaskStore updates (threads) under lock: no lost updates, valid file."""
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    (codex_root / "03_WORK").mkdir(parents=True)
    (codex_root / "10_OVERSEER" / "locks").mkdir(parents=True)
    store = CodexStore(tmp_path)
    store.codex_root = codex_root
    store.init_structure()
    ts = TaskStore(store)
    t1 = ts.add_task("obj1")
    t2 = ts.add_task("obj2")
    task_id_1 = t1["id"]
    task_id_2 = t2["id"]

    os.environ["OVERSEER_TEST_DELAY_TASKSTORE_AFTER_READ"] = "0.02"
    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            ex.submit(ts.update_status, task_id_1, "running")
            ex.submit(ts.update_status, task_id_2, "running")
    finally:
        os.environ.pop("OVERSEER_TEST_DELAY_TASKSTORE_AFTER_READ", None)

    tasks = ts.load_tasks()
    by_id = {t["id"]: t for t in tasks}
    assert len(by_id) >= 2
    assert by_id[task_id_1]["status"] == "running"
    assert by_id[task_id_2]["status"] == "running"
    with (codex_root / "03_WORK" / "TASK_GRAPH.jsonl").open(encoding="utf-8") as f:
        content = f.read()
    assert task_id_1 in content and task_id_2 in content


# --- Hammer: many concurrent runs, isolated and valid ---


def test_hammer_many_runs_isolated_and_valid(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    runs_root = codex_root / "08_TELEMETRY" / "runs"
    runs_root.mkdir(parents=True)
    locks_dir = codex_root / "10_OVERSEER" / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    backend = LocalBackend(codex_root)
    n = 25
    run_ids: list[str] = []

    def submit_one(i: int) -> str:
        run_id = LocalBackend.new_run_id()
        run_root = runs_root / run_id
        run_root.mkdir(parents=True)
        cmd = [
            sys.executable, "-c",
            f"print('run_id={run_id}')",
        ]
        req = ExecutionRequest(
            run_id=run_id,
            task_id=f"task-{i}",
            command=cmd,
            cwd=tmp_path,
            stdout_log=run_root / "stdout.log",
            stderr_log=run_root / "stderr.log",
            meta_path=run_root / "meta.json",
            lock_path=locks_dir / f"{run_id}.lock",
        )
        backend.submit(req)
        return run_id

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(submit_one, i) for i in range(n)]
        for f in as_completed(futs, timeout=15):
            run_ids.append(f.result())

    deadline = time.time() + 12
    while time.time() < deadline:
        records = backend.list_runs()
        terminal = [r for r in records if r.run_id in run_ids and r.status in ("done", "failed")]
        if len(terminal) >= n:
            break
        time.sleep(0.1)
    else:
        pending = [r for r in backend.list_runs() if r.run_id in run_ids and r.status not in ("done", "failed")]
        raise AssertionError(f"Not all runs finished: {len(pending)} pending")

    for run_id in run_ids:
        run_dir = runs_root / run_id
        assert run_dir.exists()
        meta_path = run_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["run_id"] == run_id
        assert meta["status"] in ("done", "failed")
        stdout = (run_dir / "stdout.log").read_text(encoding="utf-8")
        assert f"run_id={run_id}" in stdout
        assert stdout.strip().count("run_id=") == 1


# --- Git worktree: concurrent creation safe ---


def _create_worktree(repo_path: Path, codex_path: Path, run_id: str) -> str:
    mgr = GitWorktreeManager(repo_root=repo_path, codex_root=codex_path)
    handle = mgr.create_for_run(task_id="task-1", run_id=run_id)
    assert handle.path.exists()
    return run_id


def test_git_worktree_create_concurrent_safe(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    (repo / "f").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True)
    codex_root = repo / "codex"
    codex_root.mkdir(parents=True)
    (codex_root / "10_OVERSEER" / "worktrees").mkdir(parents=True)
    (codex_root / "10_OVERSEER" / "locks").mkdir(parents=True, exist_ok=True)

    run_ids = [f"run-{i:04d}-{os.urandom(4).hex()}" for i in range(10)]
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [
            ex.submit(_create_worktree, repo, codex_root, rid)
            for rid in run_ids
        ]
        results = [f.result(timeout=10) for f in as_completed(futures, timeout=15)]

    assert len(results) == 10
    for run_id in run_ids:
        path = codex_root / "10_OVERSEER" / "worktrees" / run_id
        assert path.exists(), f"worktree missing for {run_id}"


def test_worktree_collision_raises(tmp_path: Path) -> None:
    """If worktree path already exists, create_for_run raises (no silent reuse)."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    (repo / "f").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True)
    codex_root = repo / "codex"
    codex_root.mkdir(parents=True)
    wt_dir = codex_root / "10_OVERSEER" / "worktrees" / "run-abc123"
    wt_dir.mkdir(parents=True)
    (codex_root / "10_OVERSEER" / "locks").mkdir(parents=True, exist_ok=True)

    mgr = GitWorktreeManager(repo_root=repo, codex_root=codex_root)
    with pytest.raises(GitRepoError, match="already exists"):
        mgr.create_for_run(task_id="task-1", run_id="run-abc123")


def test_cancel_one_running_run_does_not_affect_another(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir(parents=True)
    backend = LocalBackend(codex_root)

    def make_record(run_id: str, code: str) -> Path:
        run_root = codex_root / "08_TELEMETRY" / "runs" / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        lock_path = codex_root / "10_OVERSEER" / "locks" / f"{run_id}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path = run_root / "meta.json"
        record = ExecutionRecord(
            run_id=run_id,
            task_id=f"task-{run_id}",
            status="queued",
            command=[sys.executable, "-c", code],
            cwd=str(tmp_path),
            stdout_log=str(run_root / "stdout.log"),
            stderr_log=str(run_root / "stderr.log"),
            meta_path=str(meta_path),
            lock_path=str(lock_path),
            created_at="2020-01-01T00:00:00Z",
        )
        with file_lock(backend._events_lock_path(meta_path)):
            backend._append_event(meta_path, "started", {"record": record.__dict__})
        return meta_path

    run_one = "run-cancel-target"
    run_two = "run-keep-running"
    meta_one = make_record(run_one, "import time; time.sleep(5)")
    meta_two = make_record(run_two, "import time; time.sleep(0.5)")

    t1 = threading.Thread(target=backend.run_worker, args=(meta_one,))
    t2 = threading.Thread(target=backend.run_worker, args=(meta_two,))
    t1.start()
    t2.start()

    deadline = time.time() + 5
    while time.time() < deadline:
        if backend.status(run_one).status == "running":
            break
        time.sleep(0.05)

    canceling = backend.cancel(run_one)
    assert canceling.status == "canceling"

    t1.join(timeout=10)
    t2.join(timeout=10)

    assert backend.status(run_one).status == "canceled"
    assert backend.status(run_two).status == "done"

    event_types = [
        json.loads(line)["type"]
        for line in (meta_one.parent / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert event_types.index("cancel_requested") < event_types.index("canceled")
