from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


RUN_STORE_FILENAME = "overseer.sqlite"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RunSubmission:
    run_id: str
    task_id: str | None
    backend_type: str
    worktree_path: str
    pid: int | None = None
    meta_json: dict[str, Any] | None = None


@dataclass(frozen=True)
class StoredRun:
    run_id: str
    task_id: str | None
    status: str
    created_at: str
    updated_at: str
    heartbeat_at: str | None
    backend_type: str
    worktree_path: str
    pid: int | None
    exit_code: int | None
    failure_reason: str | None
    meta_json: dict[str, Any] | None


@dataclass(frozen=True)
class StoredRunEvent:
    run_id: str
    type: str
    at: str
    payload: dict[str, Any]


class RunStore(Protocol):
    def create_run(self, submission: RunSubmission) -> str: ...

    def get_run(self, run_id: str) -> StoredRun: ...

    def list_runs(self, filters: dict[str, Any] | None = None) -> list[StoredRun]: ...

    def update_status(
        self,
        run_id: str,
        status: str,
        reason: str | None = None,
        updated_fields: dict[str, Any] | None = None,
    ) -> StoredRun: ...

    def heartbeat(self, run_id: str) -> StoredRun: ...

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None: ...


class SQLiteRunStore:
    def __init__(self, codex_root: Path, db_path: Path | None = None) -> None:
        self.codex_root = codex_root
        self.db_path = db_path or (codex_root / "08_TELEMETRY" / RUN_STORE_FILENAME)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    heartbeat_at TEXT,
                    backend_type TEXT NOT NULL,
                    worktree_path TEXT NOT NULL,
                    pid INTEGER,
                    exit_code INTEGER,
                    failure_reason TEXT,
                    meta_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS runs_status_idx ON runs(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS runs_status_heartbeat_idx ON runs(status, heartbeat_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS run_events_run_id_idx ON run_events(run_id)")

    def _row_to_run(self, row: sqlite3.Row) -> StoredRun:
        meta = row["meta_json"]
        return StoredRun(
            run_id=row["run_id"],
            task_id=row["task_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            heartbeat_at=row["heartbeat_at"],
            backend_type=row["backend_type"],
            worktree_path=row["worktree_path"],
            pid=row["pid"],
            exit_code=row["exit_code"],
            failure_reason=row["failure_reason"],
            meta_json=json.loads(meta) if meta else None,
        )

    def create_run(self, submission: RunSubmission) -> str:
        now = _utc_now()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO runs (
                        run_id, task_id, status, created_at, updated_at, heartbeat_at,
                        backend_type, worktree_path, pid, exit_code, failure_reason, meta_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        submission.run_id,
                        submission.task_id,
                        "queued",
                        now,
                        now,
                        now,
                        submission.backend_type,
                        submission.worktree_path,
                        submission.pid,
                        None,
                        None,
                        json.dumps(submission.meta_json) if submission.meta_json else None,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"run already exists: {submission.run_id}") from exc
        return submission.run_id

    def get_run(self, run_id: str) -> StoredRun:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(f"run not found: {run_id}")
        return self._row_to_run(row)

    def list_runs(self, filters: dict[str, Any] | None = None) -> list[StoredRun]:
        filters = filters or {}
        clauses: list[str] = []
        params: list[Any] = []

        allowed_filters = {
            "status": "status = ?",
            "task_id": "task_id = ?",
            "run_id": "run_id = ?",
        }

        for key, clause in allowed_filters.items():
            if key in filters and filters[key] is not None:
                clauses.append(clause)
                params.append(filters[key])

        sql = "SELECT * FROM runs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_run(row) for row in rows]

    def update_status(
        self,
        run_id: str,
        status: str,
        reason: str | None = None,
        updated_fields: dict[str, Any] | None = None,
    ) -> StoredRun:
        now = _utc_now()
        updated_fields = dict(updated_fields or {})
        columns: list[str] = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status, now]

        if reason is not None:
            columns.append("failure_reason = ?")
            values.append(reason)
        elif status in {"done", "canceled"}:
            columns.append("failure_reason = NULL")

        if "meta_json" in updated_fields:
            updated_fields["meta_json"] = json.dumps(updated_fields["meta_json"])

        allowed_fields = {
            "task_id": "task_id = ?",
            "heartbeat_at": "heartbeat_at = ?",
            "pid": "pid = ?",
            "exit_code": "exit_code = ?",
            "meta_json": "meta_json = ?",
        }
        for key, column_clause in allowed_fields.items():
            if key in updated_fields:
                columns.append(column_clause)
                values.append(updated_fields[key])

        values.append(run_id)
        with self._connect() as conn:
            cur = conn.execute(f"UPDATE runs SET {', '.join(columns)} WHERE run_id = ?", values)
            if cur.rowcount == 0:
                raise FileNotFoundError(f"run not found: {run_id}")
        return self.get_run(run_id)

    def heartbeat(self, run_id: str) -> StoredRun:
        now = _utc_now()
        return self.update_status(run_id, self.get_run(run_id).status, updated_fields={"heartbeat_at": now})

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        at = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO run_events (run_id, type, at, payload_json) VALUES (?, ?, ?, ?)",
                (run_id, event_type, at, json.dumps(payload)),
            )

    def list_events(self, run_id: str) -> list[StoredRunEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id, type, at, payload_json FROM run_events WHERE run_id = ? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
        return [
            StoredRunEvent(
                run_id=row["run_id"],
                type=row["type"],
                at=row["at"],
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]
