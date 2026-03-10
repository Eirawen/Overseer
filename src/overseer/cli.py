from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from overseer.codex_store import CodexStore
from overseer.execution import LocalBackend, build_backend
from overseer.git_worktree import GitRepoError, resolve_git_root
from overseer.human_api import HumanAPI
from overseer.integrators import CodexIntegrator, RunRequest
from overseer.handoff import HandoffService
from overseer.llm import (
    CODEX_PROVIDER_ID,
    JsonOAuthCredentialStore,
    OAuthRefreshCoordinator,
    build_runtime_llm,
    import_codex_cli_credential,
)
from overseer.llm.codex import CodexOAuthAdapter
from overseer.overseer_graph import OverseerCoreGraph
from overseer.session_store import SessionStore
from overseer.task_store import TaskStore

def _runtime_llm(codex_store: CodexStore):
    return build_runtime_llm(codex_store)


def _runtime_status_line(backend, llm) -> str:
    backend_kind = getattr(backend, "backend_kind", backend.__class__.__name__.replace("Backend", "").lower())
    llm_health = llm.health() if hasattr(llm, "health") else {"mode": llm.__class__.__name__}
    return f"backend={backend_kind} llm={llm_health.get('mode', llm.__class__.__name__)}"


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


def _build_handoff_service(repo_root: Path, instance_id: str | None = None) -> tuple[CodexStore, HandoffService]:
    codex_store, _, _, _ = _services(repo_root)
    codex_store.init_structure()
    service = HandoffService(codex_store, SessionStore(codex_store), instance_id=instance_id)
    return codex_store, service


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
    codex_store, task_store, human_api, backend = _services(Path(args.repo_root))
    codex_store.init_structure()
    integrator = _build_integrator(codex_store.repo_root)
    handoff_service = HandoffService(codex_store, SessionStore(codex_store))
    llm = _runtime_llm(codex_store)
    graph = OverseerCoreGraph.build(
        codex_store=codex_store,
        task_store=task_store,
        human_api=human_api,
        backend=backend,
        integrator=integrator,
        llm=llm,
        handoff_service=handoff_service,
        instance_id=handoff_service.instance_id,
    )
    from overseer.daemon_api import OverseerDaemon, serve_daemon

    daemon = OverseerDaemon(
        backend=backend,
        integrator=integrator,
        human_api=human_api,
        task_store=task_store,
        overseer_graph=graph,
        handoff_service=handoff_service,
    )
    print(f"Overseer self-hosted daemon starting ({_runtime_status_line(backend, llm)})")
    serve_daemon(daemon, host=args.host, port=args.port)
    return 0


def _handoff_status_text(status) -> str:
    latest = status.latest_assessment or {}
    latest_score = latest.get("score", "-")
    latest_band = latest.get("band", "-")
    lease = status.lease
    lines = [
        f"instance_id={status.instance_id}",
        f"owner={lease.get('owner_instance_id')} lease_epoch={lease.get('lease_epoch')} status={lease.get('status')}",
        f"active_handoff={lease.get('active_handoff_id') or '-'} observers={','.join(lease.get('observer_instance_ids', [])) or '-'}",
        f"pressure_score={latest_score} pressure_band={latest_band}",
    ]
    return "\n".join(lines)


def cmd_session_handoff_status(args: argparse.Namespace) -> int:
    _, handoff = _build_handoff_service(Path(args.repo_root), instance_id=args.instance_id)
    status = handoff.status(args.session)
    print(_handoff_status_text(status))
    return 0


def cmd_session_handoff_assess(args: argparse.Namespace) -> int:
    _, handoff = _build_handoff_service(Path(args.repo_root), instance_id=args.instance_id)
    handoff.ensure_lease(args.session, handoff.instance_id)
    assessment = handoff.assess_pressure(args.session)
    print(f"instance_id={handoff.instance_id}")
    print(json.dumps(assessment.__dict__, indent=2, sort_keys=True))
    return 0


def cmd_session_handoff_prepare(args: argparse.Namespace) -> int:
    _, handoff = _build_handoff_service(Path(args.repo_root), instance_id=args.instance_id)
    checkpoint = handoff.prepare_handoff(args.session, owner_instance_id=handoff.instance_id)
    print(f"instance_id={handoff.instance_id}")
    print(f"handoff_id={checkpoint.handoff_id}")
    print(f"root={checkpoint.root}")
    print(f"checkpoint={checkpoint.checkpoint_json_path}")
    print(f"brief={checkpoint.handoff_brief_path}")
    return 0


def cmd_session_handoff_observe(args: argparse.Namespace) -> int:
    _, handoff = _build_handoff_service(Path(args.repo_root), instance_id=args.instance_id)
    checkpoint = handoff.register_observer(args.session, args.handoff, observer_instance_id=handoff.instance_id)
    print(f"instance_id={handoff.instance_id}")
    print(f"handoff_id={checkpoint.handoff_id}")
    print(f"mode=observe read_only=true")
    print(f"brief={checkpoint.handoff_brief_path}")
    return 0


def cmd_session_handoff_switch(args: argparse.Namespace) -> int:
    _, handoff = _build_handoff_service(Path(args.repo_root), instance_id=args.instance_id)
    checkpoint = handoff.switch_handoff(
        args.session,
        args.handoff,
        from_owner_instance_id=handoff.instance_id,
        to_owner_instance_id=args.to_instance,
    )
    status = handoff.status(args.session)
    print(f"instance_id={handoff.instance_id}")
    print(f"handoff_id={checkpoint.handoff_id}")
    print(f"new_owner={status.lease.get('owner_instance_id')} lease_epoch={status.lease.get('lease_epoch')}")
    return 0


def cmd_session_handoff_abort(args: argparse.Namespace) -> int:
    _, handoff = _build_handoff_service(Path(args.repo_root), instance_id=args.instance_id)
    checkpoint = handoff.abort_handoff(args.session, args.handoff, owner_instance_id=handoff.instance_id)
    print(f"instance_id={handoff.instance_id}")
    print(f"handoff_id={checkpoint.handoff_id}")
    print("status=aborted")
    return 0


def cmd_session_handoff_note(args: argparse.Namespace) -> int:
    _, handoff = _build_handoff_service(Path(args.repo_root), instance_id=args.instance_id)
    if args.role == "observer":
        handoff.append_observer_note(args.session, args.handoff, handoff.instance_id, args.text)
    else:
        handoff.append_advisor_note(args.session, args.handoff, handoff.instance_id, args.text)
    print(f"instance_id={handoff.instance_id}")
    print(f"handoff_id={args.handoff}")
    print(f"note_role={args.role}")
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    codex_store, task_store, human_api, backend = _services(Path(args.repo_root))
    codex_store.init_structure()
    integrator = _build_integrator(codex_store.repo_root)
    handoff_service = HandoffService(codex_store, SessionStore(codex_store))
    llm = _runtime_llm(codex_store)
    graph = OverseerCoreGraph.build(
        codex_store=codex_store,
        task_store=task_store,
        human_api=human_api,
        backend=backend,
        integrator=integrator,
        llm=llm,
        handoff_service=handoff_service,
        instance_id=handoff_service.instance_id,
    )
    session_id = graph.create_session()
    print(f"Overseer chat started. Session={session_id}")
    print(f"Instance={handoff_service.instance_id}")
    print(f"Runtime: {_runtime_status_line(backend, llm)}")
    print("Commands: /new, /resume <id>, /status, /plan, /tick, /handoff <status|assess|prepare|observe|switch>, /exit")
    while True:
        try:
            raw = input("overseer> ").strip()
        except EOFError:
            print("Session ended.")
            break
        if not raw:
            continue
        if raw == "/exit":
            print("Session ended.")
            break
        if raw == "/new":
            session_id = graph.create_session()
            print(f"created session {session_id}")
            continue
        if raw.startswith("/resume "):
            session_id = raw.split(" ", 1)[1].strip()
            graph.load_state(session_id)
            print(f"resumed {session_id}")
            continue
        if raw == "/status":
            state = graph.load_state(session_id)
            print(
                f"mode={state.get('mode')} active_runs={len(state.get('active_runs', {}))} "
                f"pending_human={','.join(state.get('pending_human_requests', [])) or '-'}"
            )
            continue
        if raw == "/plan":
            state = graph.load_state(session_id)
            if not state.get("plan"):
                print("No plan yet.")
                continue
            for step in state["plan"]:
                print(f"{step['id']} [{step['status']}] {step['title']}")
            continue
        if raw == "/tick":
            try:
                state = graph.tick(session_id)
                print(state.get("latest_response", "Tick complete."))
            except (PermissionError, RuntimeError, ValueError) as exc:
                print(f"error: {exc}")
            continue
        if raw.startswith("/handoff"):
            parts = raw.split()
            if len(parts) < 2:
                print("usage: /handoff <status|assess|prepare|observe|switch>")
                continue
            action = parts[1]
            try:
                if action == "status":
                    print(_handoff_status_text(handoff_service.status(session_id)))
                elif action == "assess":
                    assessment = handoff_service.assess_pressure(session_id)
                    print(json.dumps(assessment.__dict__, indent=2, sort_keys=True))
                elif action == "prepare":
                    checkpoint = handoff_service.prepare_handoff(session_id, handoff_service.instance_id)
                    print(f"handoff_id={checkpoint.handoff_id}")
                    print(f"brief={checkpoint.handoff_brief_path}")
                elif action == "observe" and len(parts) == 3:
                    checkpoint = handoff_service.register_observer(session_id, parts[2], handoff_service.instance_id)
                    print(f"handoff_id={checkpoint.handoff_id} mode=observe read_only=true")
                elif action == "switch" and len(parts) == 4:
                    checkpoint = handoff_service.switch_handoff(
                        session_id,
                        parts[2],
                        from_owner_instance_id=handoff_service.instance_id,
                        to_owner_instance_id=parts[3],
                    )
                    print(f"handoff_id={checkpoint.handoff_id} switched_to={parts[3]}")
                else:
                    print("usage: /handoff <status|assess|prepare|observe <handoff_id>|switch <handoff_id> <to_instance_id>>")
            except (PermissionError, ValueError, FileNotFoundError) as exc:
                print(f"error: {exc}")
            continue

        if raw.startswith("/"):
            print("error: unknown command")
            continue

        try:
            state = graph.submit_user_message(session_id, raw)
            print(state.get("latest_response", "ok"))
        except (PermissionError, RuntimeError, ValueError) as exc:
            print(f"error: {exc}")
    return 0


def cmd_session_list(args: argparse.Namespace) -> int:
    codex_store, task_store, human_api, backend = _services(Path(args.repo_root))
    integrator = _build_integrator(codex_store.repo_root)
    graph = OverseerCoreGraph.build(
        codex_store=codex_store,
        task_store=task_store,
        human_api=human_api,
        backend=backend,
        integrator=integrator,
        llm=_runtime_llm(codex_store),
    )
    for session_id in graph.list_sessions():
        print(session_id)
    return 0


def _auth_store(codex_store: CodexStore) -> JsonOAuthCredentialStore:
    root = codex_store.codex_root / "10_OVERSEER" / "auth"
    codex_store.assert_write_allowed("overseer", root)
    return JsonOAuthCredentialStore(root)


def _refresh_coordinator(codex_store: CodexStore) -> OAuthRefreshCoordinator:
    return OAuthRefreshCoordinator(codex_store.codex_root / "10_OVERSEER" / "locks")


def cmd_auth_import_codex_cli(args: argparse.Namespace) -> int:
    codex_store, _, _, _ = _services(Path(args.repo_root))
    codex_store.init_structure()
    credential = import_codex_cli_credential()
    _auth_store(codex_store).put(CODEX_PROVIDER_ID, credential, args.profile)
    print(f"imported provider={CODEX_PROVIDER_ID} profile={args.profile}")
    return 0


def cmd_auth_status(args: argparse.Namespace) -> int:
    codex_store, _, _, _ = _services(Path(args.repo_root))
    codex_store.init_structure()
    store = _auth_store(codex_store)
    records = store.list(args.provider)
    if not records:
        print("No stored OAuth credentials.")
        return 0
    for provider_id, profile_id, credential in records:
        print(
            f"provider={provider_id} profile={profile_id} expires_at={credential.expires_at} "
            f"email={credential.email or '-'} account_id={credential.account_id or '-'}"
        )
    return 0


def cmd_auth_logout(args: argparse.Namespace) -> int:
    codex_store, _, _, _ = _services(Path(args.repo_root))
    codex_store.init_structure()
    deleted = _auth_store(codex_store).delete(args.provider, args.profile)
    if not deleted:
        raise RuntimeError(f"No stored credential for {args.provider}:{args.profile}")
    print(f"deleted provider={args.provider} profile={args.profile}")
    return 0


def cmd_auth_login(args: argparse.Namespace) -> int:
    codex_store, _, _, _ = _services(Path(args.repo_root))
    codex_store.init_structure()
    if args.provider != CODEX_PROVIDER_ID:
        raise RuntimeError(f"Unsupported provider: {args.provider}")
    adapter = CodexOAuthAdapter()
    credential = adapter.login(is_headless=args.headless)
    _auth_store(codex_store).put(args.provider, credential, args.profile)
    print(f"stored provider={args.provider} profile={args.profile} expires_at={credential.expires_at}")
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

    auth_parser = subparsers.add_parser("auth")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", required=True)

    auth_login_parser = auth_subparsers.add_parser("login")
    auth_login_parser.add_argument("--provider", default=CODEX_PROVIDER_ID)
    auth_login_parser.add_argument("--profile", default="default")
    auth_login_parser.add_argument("--headless", action="store_true")
    auth_login_parser.set_defaults(func=cmd_auth_login)

    auth_status_parser = auth_subparsers.add_parser("status")
    auth_status_parser.add_argument("--provider")
    auth_status_parser.set_defaults(func=cmd_auth_status)

    auth_logout_parser = auth_subparsers.add_parser("logout")
    auth_logout_parser.add_argument("--provider", default=CODEX_PROVIDER_ID)
    auth_logout_parser.add_argument("--profile", default="default")
    auth_logout_parser.set_defaults(func=cmd_auth_logout)

    auth_import_parser = auth_subparsers.add_parser("import-codex-cli")
    auth_import_parser.add_argument("--profile", default="default")
    auth_import_parser.set_defaults(func=cmd_auth_import_codex_cli)

    session_parser = subparsers.add_parser("session")
    session_subparsers = session_parser.add_subparsers(dest="session_command", required=True)
    session_list_parser = session_subparsers.add_parser("list")
    session_list_parser.set_defaults(func=cmd_session_list)

    session_handoff_parser = session_subparsers.add_parser("handoff")
    session_handoff_subparsers = session_handoff_parser.add_subparsers(dest="session_handoff_command", required=True)

    sh_status = session_handoff_subparsers.add_parser("status")
    sh_status.add_argument("--session", required=True)
    sh_status.add_argument("--instance-id")
    sh_status.set_defaults(func=cmd_session_handoff_status)

    sh_assess = session_handoff_subparsers.add_parser("assess")
    sh_assess.add_argument("--session", required=True)
    sh_assess.add_argument("--instance-id")
    sh_assess.set_defaults(func=cmd_session_handoff_assess)

    sh_prepare = session_handoff_subparsers.add_parser("prepare")
    sh_prepare.add_argument("--session", required=True)
    sh_prepare.add_argument("--instance-id")
    sh_prepare.set_defaults(func=cmd_session_handoff_prepare)

    sh_observe = session_handoff_subparsers.add_parser("observe")
    sh_observe.add_argument("--session", required=True)
    sh_observe.add_argument("--handoff", required=True)
    sh_observe.add_argument("--instance-id")
    sh_observe.set_defaults(func=cmd_session_handoff_observe)

    sh_switch = session_handoff_subparsers.add_parser("switch")
    sh_switch.add_argument("--session", required=True)
    sh_switch.add_argument("--handoff", required=True)
    sh_switch.add_argument("--to-instance", required=True)
    sh_switch.add_argument("--instance-id")
    sh_switch.set_defaults(func=cmd_session_handoff_switch)

    sh_abort = session_handoff_subparsers.add_parser("abort")
    sh_abort.add_argument("--session", required=True)
    sh_abort.add_argument("--handoff", required=True)
    sh_abort.add_argument("--instance-id")
    sh_abort.set_defaults(func=cmd_session_handoff_abort)

    sh_note = session_handoff_subparsers.add_parser("note")
    sh_note.add_argument("--session", required=True)
    sh_note.add_argument("--handoff", required=True)
    sh_note.add_argument("--role", choices=("observer", "advisor"), required=True)
    sh_note.add_argument("--text", required=True)
    sh_note.add_argument("--instance-id")
    sh_note.set_defaults(func=cmd_session_handoff_note)

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
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
