from __future__ import annotations

import argparse
import sys
import queue
import threading
from pathlib import Path

from overseer.codex_store import CodexStore
from overseer.chat_server import OverseerChatService
from overseer.execution import LocalBackend, build_backend
from overseer.git_worktree import GitRepoError, resolve_git_root
from overseer.human_api import HumanAPI
from overseer.integrators import CodexIntegrator, RunRequest
from overseer.task_store import TaskStore


def _print_event_stream(service: OverseerChatService, stop: threading.Event) -> None:
    sub = service.events.subscribe()
    try:
        while not stop.is_set():
            try:
                event = sub.get(timeout=0.5)
            except queue.Empty:
                continue
            if event.get("type") == "run_status":
                print(f"[status] {event['run_id']} task={event['task_id']} status={event['status']}")
            elif event.get("type") == "human_escalation":
                print(f"[queue] human escalations pending={event['count']}")
    finally:
        service.events.unsubscribe(sub)


def _services(repo_root: Path | None = None):
    requested_root = repo_root or Path.cwd()
    root = resolve_git_root(requested_root)
    codex_store = CodexStore(root)
    task_store = TaskStore(codex_store)
    human_api = HumanAPI(codex_store)
    backend = build_backend(codex_store.codex_root, human_api=human_api)
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


def cmd_runs_list(args: argparse.Namespace) -> int:
    integrator = _build_integrator(Path(args.repo_root))
    for run in integrator.runs():
        print(f"{run.run_id} task={run.task_id} status={run.status} exit={run.exit_code}")
    return 0


def cmd_runs_show(args: argparse.Namespace) -> int:
    integrator = _build_integrator(Path(args.repo_root))
    run = integrator.status(args.run)
    print(f"{run.run_id} task={run.task_id} status={run.status} exit={run.exit_code}")
    return 0


def cmd_runs_cancel(args: argparse.Namespace) -> int:
    integrator = _build_integrator(Path(args.repo_root))
    run = integrator.cancel(args.run)
    print(f"{run.run_id} task={run.task_id} status={run.status} exit={run.exit_code}")
    return 0


def cmd_runs_reconcile(args: argparse.Namespace) -> int:
    _, _, _, backend = _services(Path(args.repo_root))
    reconciled = backend.reconcile(stale_after_seconds=args.stale_after_seconds)
    for run in reconciled:
        print(f"{run.run_id} task={run.task_id} status={run.status} reason={run.failure_reason}")
    print(f"reconciled={len(reconciled)}")
    return 0


def cmd_execution_worker(args: argparse.Namespace) -> int:
    backend = LocalBackend(Path(args.codex_root))
    return backend.run_worker(args.run_id)


def cmd_integrate(args: argparse.Namespace) -> int:
    return cmd_run_agent(args)


def cmd_human_list(args: argparse.Namespace) -> int:
    _, _, human_api, _ = _services(Path(args.repo_root))
    for request in human_api.list_requests():
        print(
            f"{request.request_id} status={request.status} type={request.request_type} "
            f"urgency={request.urgency} task={request.task_id or '-'} run={request.run_id or '-'}"
        )
    return 0


def cmd_human_show(args: argparse.Namespace) -> int:
    _, _, human_api, _ = _services(Path(args.repo_root))
    request = human_api.show_request(args.id)
    print(request.request_path.read_text(encoding="utf-8"))
    if request.resolution_path is not None:
        print(request.resolution_path.read_text(encoding="utf-8"))
    return 0


def cmd_human_resolve(args: argparse.Namespace) -> int:
    _, _, human_api, _ = _services(Path(args.repo_root))
    resolution_path = human_api.resolve_request(
        request_id=args.id,
        choice=args.choice,
        rationale=args.rationale,
        artifact_path=args.artifact_path,
    )
    print(f"resolved {args.id} -> {resolution_path}")
    return 0


def cmd_human_types_validate(args: argparse.Namespace) -> int:
    _, _, human_api, _ = _services(Path(args.repo_root))
    task_types = human_api.validate_task_types()
    print(f"valid {len(task_types)} human task types")
    return 0


def cmd_human_types_list(args: argparse.Namespace) -> int:
    _, _, human_api, _ = _services(Path(args.repo_root))
    for item in human_api.list_task_types():
        print(
            f"{item.id} default_TYPE={item.default_type} default_URGENCY={item.default_urgency} :: {item.description}"
        )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    codex_store, _, human_api, backend = _services(Path(args.repo_root))
    codex_store.init_structure()
    integrator = _build_integrator(codex_store.repo_root)
    from overseer.daemon_api import OverseerDaemon, serve_daemon

    daemon = OverseerDaemon(backend=backend, integrator=integrator, human_api=human_api)
    serve_daemon(daemon, host=args.host, port=args.port)
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    codex_store, task_store, human_api, _ = _services(Path(args.repo_root))
    codex_store.init_structure()
    integrator = _build_integrator(codex_store.repo_root)
    service = OverseerChatService(codex_store, task_store, integrator, human_api)
    service.start()
    stop = threading.Event()
    printer = threading.Thread(target=_print_event_stream, args=(service, stop), daemon=True)
    printer.start()
    print("Overseer chat started. Use /run, /queue, /open, /quit.")
    try:
        while True:
            try:
                raw = input("overseer> ").strip()
            except EOFError:
                print("Session ended.")
                break
            if not raw:
                continue
            try:
                if raw.startswith("/"):
                    out = service.handle_command(raw)
                else:
                    out = service.handle_message(raw)
                print(out["assistant_text"])
                if out.get("exit"):
                    break
            except ValueError as exc:
                print(f"error: {exc}")
    finally:
        stop.set()
        printer.join(timeout=1)
        service.stop()
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
    runs_subparsers = runs_parser.add_subparsers(dest="runs_command", required=True)

    runs_list_parser = runs_subparsers.add_parser("list")
    runs_list_parser.set_defaults(func=cmd_runs_list)

    runs_show_parser = runs_subparsers.add_parser("show")
    runs_show_parser.add_argument("--run", required=True)
    runs_show_parser.set_defaults(func=cmd_runs_show)

    runs_cancel_parser = runs_subparsers.add_parser("cancel")
    runs_cancel_parser.add_argument("--run", required=True)
    runs_cancel_parser.set_defaults(func=cmd_runs_cancel)

    runs_reconcile_parser = runs_subparsers.add_parser("reconcile")
    runs_reconcile_parser.add_argument("--stale-after-seconds", type=int, default=300)
    runs_reconcile_parser.set_defaults(func=cmd_runs_reconcile)

    # Backwards-compatible aliases
    run_status_parser = subparsers.add_parser("run-status")
    run_status_parser.add_argument("--run", required=True)
    run_status_parser.set_defaults(func=cmd_runs_show)

    run_cancel_parser = subparsers.add_parser("run-cancel")
    run_cancel_parser.add_argument("--run", required=True)
    run_cancel_parser.set_defaults(func=cmd_runs_cancel)

    human_parser = subparsers.add_parser("human")
    human_subparsers = human_parser.add_subparsers(dest="human_command", required=True)

    human_list_parser = human_subparsers.add_parser("list")
    human_list_parser.set_defaults(func=cmd_human_list)

    human_show_parser = human_subparsers.add_parser("show")
    human_show_parser.add_argument("--id", required=True)
    human_show_parser.set_defaults(func=cmd_human_show)

    human_resolve_parser = human_subparsers.add_parser("resolve")
    human_resolve_parser.add_argument("--id", required=True)
    human_resolve_parser.add_argument("--choice", required=True)
    human_resolve_parser.add_argument("--rationale", required=True)
    human_resolve_parser.add_argument("--artifact-path")
    human_resolve_parser.set_defaults(func=cmd_human_resolve)

    human_types_parser = subparsers.add_parser("human-types")
    human_types_subparsers = human_types_parser.add_subparsers(
        dest="human_types_command", required=True
    )

    human_types_validate_parser = human_types_subparsers.add_parser("validate")
    human_types_validate_parser.set_defaults(func=cmd_human_types_validate)

    human_types_list_parser = human_types_subparsers.add_parser("list")
    human_types_list_parser.set_defaults(func=cmd_human_types_list)

    worker_parser = subparsers.add_parser("execution-worker")
    worker_parser.add_argument("--run-id", required=True)
    worker_parser.add_argument("--codex-root", required=True)
    worker_parser.set_defaults(func=cmd_execution_worker)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.set_defaults(func=cmd_serve)

    chat_parser = subparsers.add_parser("chat")
    chat_parser.set_defaults(func=cmd_chat)

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
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
