from __future__ import annotations

import argparse
from pathlib import Path

from overseer.codex_store import CodexStore
from overseer.human_api import HumanAPI
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

    graph = OverseerGraph(codex_store, task_store, human_api)
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
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)
