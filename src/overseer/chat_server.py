from __future__ import annotations

import json
import queue
import re
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from overseer.codex_store import CodexStore
from overseer.human_api import HumanAPI
from overseer.integrators import CodexIntegrator, RunRequest
from overseer.task_store import TaskStore

TASK_ID_RE = re.compile(r"\b(task-[0-9a-f]{12})\b")


class EventBus:
    def __init__(self) -> None:
        self._subs: list[queue.Queue[dict]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue[dict]:
        q: queue.Queue[dict] = queue.Queue()
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict]) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            subscribers = list(self._subs)
        for sub in subscribers:
            sub.put(event)


class OverseerChatService:
    def __init__(
        self,
        codex_store: CodexStore,
        task_store: TaskStore,
        integrator: CodexIntegrator,
        human_api: HumanAPI,
    ) -> None:
        self.codex_store = codex_store
        self.task_store = task_store
        self.integrator = integrator
        self.human_api = human_api
        self.events = EventBus()
        self._stop = threading.Event()
        self._watcher = threading.Thread(target=self._watch_loop, daemon=True)
        self._seen_statuses: dict[str, str] = {}
        self._human_request_count = 0

    @property
    def conversations_root(self) -> Path:
        return self.codex_store.codex_root / "08_TELEMETRY" / "conversations"

    def start(self) -> None:
        self.conversations_root.mkdir(parents=True, exist_ok=True)
        self._watcher.start()

    def stop(self) -> None:
        self._stop.set()
        self._watcher.join(timeout=2)

    def _conversation_path(self) -> Path:
        date_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.conversations_root / f"{date_stamp}.jsonl"

    def _append_conversation(self, role: str, text: str, payload: dict | None = None) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "text": text,
            "payload": payload or {},
        }
        with self._conversation_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def _find_or_create_task(self, text: str) -> tuple[dict, str | None]:
        match = TASK_ID_RE.search(text)
        if match:
            task_id = match.group(1)
            return self.task_store.get_task(task_id), None
        created = self.task_store.add_task(text)
        return created, created["id"]

    def handle_message(self, text: str) -> dict:
        self._append_conversation("human", text)
        task, created_task_id = self._find_or_create_task(text)
        run_id = self.integrator.submit(RunRequest(task_id=task["id"], objective=task["objective"]))
        self.task_store.update_status(task["id"], "running", run_id=run_id)

        assistant_text = (
            "Plan:\n"
            "- identify the active task context\n"
            "- launch a new local run in its dedicated worktree\n"
            "- stream status updates as transitions occur\n"
            f"Run IDs: {run_id}\n"
            "Updates will stream as they occur."
        )
        payload = {
            "assistant_text": assistant_text,
            "created_task_id": created_task_id,
            "created_run_ids": [run_id],
        }
        self._append_conversation("assistant", assistant_text, payload=payload)
        return payload

    def list_runs(self) -> list[dict]:
        out: list[dict] = []
        for run in self.integrator.runs():
            rec = self.integrator.backend.status(run.run_id)
            out.append(
                {
                    "run_id": run.run_id,
                    "task_id": run.task_id,
                    "status": run.status,
                    "created_at": rec.created_at,
                }
            )
        return out

    def get_run(self, run_id: str) -> dict:
        rec = self.integrator.backend.status(run_id)
        return {
            "run_id": rec.run_id,
            "task_id": rec.task_id,
            "status": rec.status,
            "created_at": rec.created_at,
            "started_at": rec.started_at,
            "ended_at": rec.ended_at,
            "exit_code": rec.exit_code,
            "stdout_log": rec.stdout_log,
            "stderr_log": rec.stderr_log,
            "meta_path": rec.meta_path,
            "worktree": rec.cwd,
        }

    def _watch_loop(self) -> None:
        while not self._stop.is_set():
            for run in self.integrator.runs():
                previous = self._seen_statuses.get(run.run_id)
                if previous != run.status:
                    self._seen_statuses[run.run_id] = run.status
                    self.events.publish(
                        {
                            "type": "run_status",
                            "run_id": run.run_id,
                            "task_id": run.task_id,
                            "status": run.status,
                        }
                    )

            queue_text = self.human_api.queue_file.read_text(encoding="utf-8") if self.human_api.queue_file.exists() else ""
            request_count = queue_text.count("HUMAN_REQUEST:")
            if request_count > self._human_request_count:
                self.events.publish(
                    {
                        "type": "human_escalation",
                        "reason": "new human request",
                        "count": request_count,
                    }
                )
                self._human_request_count = request_count
            time.sleep(0.3)


class OverseerHandler(BaseHTTPRequestHandler):
    service: OverseerChatService

    def _send_json(self, payload: dict | list, code: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/message":
            self._send_json({"error": "not found"}, code=404)
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(content_length) or b"{}")
        text = str(payload.get("text", "")).strip()
        if not text:
            self._send_json({"error": "text is required"}, code=400)
            return
        result = self.service.handle_message(text)
        self._send_json(result)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_index()
            return
        if parsed.path == "/runs":
            self._send_json(self.service.list_runs())
            return
        if parsed.path.startswith("/runs/"):
            run_id = parsed.path.removeprefix("/runs/")
            self._send_json(self.service.get_run(run_id))
            return
        if parsed.path == "/events":
            self._serve_events()
            return
        self._send_json({"error": "not found"}, code=404)

    def _serve_events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        sub = self.service.events.subscribe()
        try:
            while True:
                try:
                    event = sub.get(timeout=1)
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.service.events.unsubscribe(sub)

    def _serve_index(self) -> None:
        html = """<!doctype html>
<html>
<head><meta charset='utf-8'><title>Overseer Chat</title></head>
<body>
<h1>Overseer Chat</h1>
<div style='display:flex;gap:20px'>
  <div style='flex:1'>
    <h3>Chat</h3>
    <div id='chat' style='border:1px solid #ccc;height:320px;overflow:auto;padding:8px'></div>
    <input id='msg' style='width:80%'><button id='send'>Send</button>
  </div>
  <div style='flex:1'>
    <h3>Runs</h3>
    <div id='runs'></div>
  </div>
</div>
<script>
const chat = document.getElementById('chat');
const runs = document.getElementById('runs');
function line(text){const p=document.createElement('div');p.textContent=text;chat.appendChild(p);chat.scrollTop=chat.scrollHeight;}
async function refreshRuns(){
  const res=await fetch('/runs'); const data=await res.json();
  runs.innerHTML='';
  data.forEach(r=>{
    const d=document.createElement('div');
    d.innerHTML=`<b>${r.run_id}</b> task=${r.task_id} status=${r.status} <button data-id='${r.run_id}'>copy</button><pre></pre>`;
    d.querySelector('button').onclick=async()=>{
      const rr=await (await fetch('/runs/'+r.run_id)).json();
      navigator.clipboard.writeText(`worktree=${rr.worktree}\nstdout=${rr.stdout_log}\nstderr=${rr.stderr_log}\nmeta=${rr.meta_path}`);
      d.querySelector('pre').textContent=`worktree=${rr.worktree}\nstdout=${rr.stdout_log}\nstderr=${rr.stderr_log}\nmeta=${rr.meta_path}`;
    };
    runs.appendChild(d);
  });
}
document.getElementById('send').onclick=async()=>{
  const text=document.getElementById('msg').value;
  line('Human: '+text);
  const res=await fetch('/message',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text})});
  const data=await res.json();
  line('Overseer: '+data.assistant_text);
  document.getElementById('msg').value='';
  refreshRuns();
};
const events = new EventSource('/events');
events.onmessage = (ev)=>{const data=JSON.parse(ev.data);line('Event: '+JSON.stringify(data));refreshRuns();};
refreshRuns();
</script>
</body>
</html>"""
        content = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def serve_chat(service: OverseerChatService, host: str, port: int) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        msg = "overseer serve must bind to localhost only"
        raise RuntimeError(msg)

    server = build_server(service, host, port)
    service.start()
    try:
        server.serve_forever()
    finally:
        service.stop()
        server.server_close()


def build_server(service: OverseerChatService, host: str, port: int) -> ThreadingHTTPServer:
    OverseerHandler.service = service
    return ThreadingHTTPServer((host, port), OverseerHandler)
