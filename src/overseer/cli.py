from __future__ import annotations

import argparse
import sys
from pathlib import Path

from overseer.codex_store import CodexStore
from overseer.chat_server import OverseerChatService, serve_chat
from overseer.execution.backend import LocalBackend
from overseer.git_worktree import GitRepoError, resolve_git_root
from overseer.human_api import HumanAPI
from overseer.integrators import CodexIntegrator, RunRequest
from overseer.task_store import TaskStore


def _services(repo_root: Path | None = None):
    requested_root = repo_root or Path.cwd()
    root = resolve_git_root(requested_root)
    codex_store = CodexStore(root)
    task_store = TaskStore(codex_store)
    human_api = HumanAPI(codex_store)
    backend = LocalBackend(codex_store.codex_root, human_api=human_api)
    return codex_store, task_store, human_api, backend


def cmd_init(args: argparse.Namespace) -> int:
    codex_store, _, _, _ = _services(Path(args.repo_root))
    codex_store.init_structure()
    print("Initialized codex scaffolding")
    return 0


def cmd_add_task(args: argparse.Namespace) -> int:
    codex_store, task_store, _, _ = _services(Path(args.repo_root))
    codex_store.init_structure()
    task = task_store.add_task(args.objective)
    print(task["id"])
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    codex_store, task_store, human_api, _ = _services(Path(args.repo_root))
    codex_store.init_structure()
    from overseer.graph import OverseerGraph

    graph = OverseerGraph(codex_store, task_store, human_api)
    result = graph.run_task(args.task)
    print(f"task={args.task} status={result['status']}")
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    codex_store, task_store, human_api, _ = _services(Path(args.repo_root))
    codex_store.init_structure()
    tasks = task_store.load_tasks()
    queued = [task for task in tasks if task["status"] == "queued"]
    escalated = [task for task in tasks if task["status"] == "escalated"]
    print(human_api.generate_brief(queued, escalated))
    return 0


def _build_integrator(repo_root: Path):
    codex_store, _, human_api, backend = _services(repo_root)
    codex_store.init_structure()
    return CodexIntegrator(codex_store.repo_root, human_api=human_api, backend=backend)


def cmd_run_agent(args: argparse.Namespace) -> int:
    codex_store, task_store, _, _ = _services(Path(args.repo_root))
    codex_store.init_structure()
    task = task_store.get_task(args.task)
    integrator = _build_integrator(codex_store.repo_root)
    run_id = integrator.submit(RunRequest(task_id=task["id"], objective=task["objective"]))
    task_store.update_status(task["id"], "running", run_id=run_id)
    print(run_id)
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    integrator = _build_integrator(Path(args.repo_root))
    for run in integrator.runs():
        print(f"{run.run_id} task={run.task_id} status={run.status} exit={run.exit_code}")
    return 0


def cmd_run_status(args: argparse.Namespace) -> int:
    integrator = _build_integrator(Path(args.repo_root))
    run = integrator.status(args.run)
    print(f"{run.run_id} task={run.task_id} status={run.status} exit={run.exit_code}")
    return 0


def cmd_run_cancel(args: argparse.Namespace) -> int:
    integrator = _build_integrator(Path(args.repo_root))
    run = integrator.cancel(args.run)
    print(f"{run.run_id} task={run.task_id} status={run.status} exit={run.exit_code}")
    return 0


def cmd_execution_worker(args: argparse.Namespace) -> int:
    meta_path = Path(args.meta)
    codex_root = meta_path.parents[3]
    backend = LocalBackend(codex_root)
    return backend.run_worker(meta_path)


def cmd_integrate(args: argparse.Namespace) -> int:
    return cmd_run_agent(args)


def cmd_serve(args: argparse.Namespace) -> int:
    codex_store, task_store, human_api, _ = _services(Path(args.repo_root))
    codex_store.init_structure()
    integrator = _build_integrator(codex_store.repo_root)
    service = OverseerChatService(codex_store, task_store, integrator, human_api)
    serve_chat(service, host=args.host, port=args.port)
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

    integrate_parser = subparsers.add_parser("integrate")
    integrate_parser.add_argument("--task", required=True)
    integrate_parser.set_defaults(func=cmd_integrate)

    run_agent_parser = subparsers.add_parser("run-agent")
    run_agent_parser.add_argument("--task", required=True)
    run_agent_parser.set_defaults(func=cmd_run_agent)

    runs_parser = subparsers.add_parser("runs")
    runs_parser.set_defaults(func=cmd_runs)

    run_status_parser = subparsers.add_parser("run-status")
    run_status_parser.add_argument("--run", required=True)
    run_status_parser.set_defaults(func=cmd_run_status)

    run_cancel_parser = subparsers.add_parser("run-cancel")
    run_cancel_parser.add_argument("--run", required=True)
    run_cancel_parser.set_defaults(func=cmd_run_cancel)

    worker_parser = subparsers.add_parser("execution-worker")
    worker_parser.add_argument("--meta", required=True)
    worker_parser.set_defaults(func=cmd_execution_worker)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.set_defaults(func=cmd_serve)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except GitRepoError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
