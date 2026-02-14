from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from overseer.codex_store import CodexStore
from overseer.human_api import HumanAPI
from overseer.integrators import CodexIntegrator
from overseer.task_store import TaskStore


def _services(repo_root: Path | None = None):
    root = repo_root or Path.cwd()
    codex_store = CodexStore(root)
    task_store = TaskStore(codex_store)
    human_api = HumanAPI(codex_store)
    return codex_store, task_store, human_api


def cmd_init(args: argparse.Namespace) -> int:
    codex_store, _, _ = _services(Path(args.repo_root))
    codex_store.init_structure()
    print("Initialized codex scaffolding")
    return 0


def cmd_add_task(args: argparse.Namespace) -> int:
    codex_store, task_store, _ = _services(Path(args.repo_root))
    codex_store.init_structure()
    task = task_store.add_task(args.objective)
    print(task["id"])
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    codex_store, task_store, human_api = _services(Path(args.repo_root))
    codex_store.init_structure()
    from overseer.graph import OverseerGraph

    integrator = CodexIntegrator(codex_store.repo_root)
    graph = OverseerGraph(codex_store, task_store, human_api, integrator=integrator)
    result = graph.run_task(args.task)
    print(f"task={args.task} status={result['status']}")
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    codex_store, task_store, human_api = _services(Path(args.repo_root))
    codex_store.init_structure()
    tasks = task_store.load_tasks()
    queued = [task for task in tasks if task["status"] == "queued"]
    escalated = [task for task in tasks if task["status"] == "escalated"]
    print(human_api.generate_brief(queued, escalated))
    return 0


def _append_integrator_telemetry(
    codex_store: CodexStore,
    task_id: str,
    attempt_number: int,
    exit_code: int,
    patch_diff_path: Path,
    diagnostics: dict[str, str] | None = None,
) -> None:
    run_log_path = codex_store.codex_root / "08_TELEMETRY" / "RUN_LOG.jsonl"
    diff_present = patch_diff_path.exists() and bool(patch_diff_path.read_text(encoding="utf-8").strip())

    entry: dict[str, object] = {
        "phase": "integrator",
        "task_id": task_id,
        "attempt_number": attempt_number,
        "exit_code": exit_code,
        "diff_present": diff_present,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if diagnostics:
        entry["diagnostics"] = diagnostics

    codex_store.assert_write_allowed("overseer", run_log_path)
    with run_log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def cmd_integrate(args: argparse.Namespace) -> int:
    codex_store, _, _ = _services(Path(args.repo_root))
    codex_store.init_structure()

    diagnostics: dict[str, str] = {}
    if args.note:
        diagnostics["note"] = args.note

    _append_integrator_telemetry(
        codex_store=codex_store,
        task_id=args.task,
        attempt_number=args.attempt_number,
        exit_code=args.exit_code,
        patch_diff_path=Path(args.patch_diff),
        diagnostics=diagnostics or None,
    )
    return args.exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="overseer")
    parser.add_argument("--repo-root", default=".")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.set_defaults(func=cmd_init)

    add_task_parser = subparsers.add_parser("add-task")
    add_task_parser.add_argument("objective")
    add_task_parser.set_defaults(func=cmd_add_task)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--task", required=True)
    run_parser.set_defaults(func=cmd_run)

    brief_parser = subparsers.add_parser("brief")
    brief_parser.set_defaults(func=cmd_brief)

    integrate_parser = subparsers.add_parser("integrate")
    integrate_parser.add_argument("--task", required=True)
    integrate_parser.add_argument("--attempt-number", type=int, required=True)
    integrate_parser.add_argument("--exit-code", type=int, required=True)
    integrate_parser.add_argument("--patch-diff", default="patch.diff")
    integrate_parser.add_argument("--note")
    integrate_parser.set_defaults(func=cmd_integrate)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)
