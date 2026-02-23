export type RunRecord = {
  run_id: string;
  status: string;
  task_id?: string | null;
  cwd?: string;
  stdout_log?: string;
  stderr_log?: string;
};

export type QueueRecord = {
  request_id: string;
  status: string;
  task_id?: string | null;
  run_id?: string | null;
  type?: string | null;
  urgency?: string | null;
  time_required_min?: number | null;
  context?: string | null;
  options?: string[] | null;
  recommendation?: string | null;
  why?: string[] | null;
  unblocks?: string | null;
  reply_format?: string | null;
};

export type ResolveQueuePayload = {
  choice: string;
  rationale: string;
  artifact_path?: string | null;
};

export type RunLogPayload = {
  run_id: string;
  lines: number;
  stdout: string;
  stderr: string;
};

export type SessionTurn = {
  role?: string;
  content?: string;
};

export type SessionStep = {
  id?: string;
  title?: string;
  status?: string;
  task_id?: string;
};

export type SessionChatResponse = {
  session_id?: string;
  instance_id?: string | null;
  assistant_text?: string;
  mode?: string;
  latest_response?: string;
  active_run_count?: number;
  pending_human_requests?: string[];
  conversation_turns?: SessionTurn[];
  plan?: SessionStep[];
  active_runs?: Record<string, unknown>;
  created_task_id?: string | null;
  created_run_ids?: string[];
  task_id?: string;
  run_id?: string;
};

export type SessionSummary = {
  session_id: string;
  mode?: string;
  active_run_count?: number;
  pending_human_requests?: string[];
  updated_at?: string;
};

export function getWsRoot(apiRoot: string): string {
  if (apiRoot.startsWith('https://')) {
    return `wss://${apiRoot.slice('https://'.length)}`;
  }
  if (apiRoot.startsWith('http://')) {
    return `ws://${apiRoot.slice('http://'.length)}`;
  }
  return `ws://${apiRoot}`;
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new Error(`request failed (${response.status}): ${url}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchRuns(apiRoot: string): Promise<RunRecord[]> {
  const payload = await fetchJson<unknown>(`${apiRoot}/runs`);
  return Array.isArray(payload) ? (payload as RunRecord[]) : [];
}

export async function fetchQueue(apiRoot: string): Promise<QueueRecord[]> {
  const payload = await fetchJson<unknown>(`${apiRoot}/queue`);
  return Array.isArray(payload) ? (payload as QueueRecord[]) : [];
}

export async function resolveQueueRequest(
  apiRoot: string,
  requestId: string,
  payload: ResolveQueuePayload,
): Promise<void> {
  await fetchJson(`${apiRoot}/queue/${requestId}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function fetchRunLogs(apiRoot: string, runId: string, lines = 150): Promise<RunLogPayload> {
  return fetchJson<RunLogPayload>(`${apiRoot}/runs/${runId}/logs?lines=${lines}`);
}

export async function cancelRun(apiRoot: string, runId: string): Promise<void> {
  await fetchJson(`${apiRoot}/runs/${runId}/cancel`, { method: 'POST' });
}

export async function createSession(apiRoot: string): Promise<SessionChatResponse> {
  return fetchJson<SessionChatResponse>(`${apiRoot}/sessions`, { method: 'POST' });
}

export async function fetchSession(apiRoot: string, sessionId: string): Promise<SessionChatResponse> {
  return fetchJson<SessionChatResponse>(`${apiRoot}/sessions/${sessionId}`);
}

export async function tickSession(apiRoot: string, sessionId: string): Promise<SessionChatResponse> {
  return fetchJson<SessionChatResponse>(`${apiRoot}/sessions/${sessionId}/tick`, { method: 'POST' });
}

export async function sendMessage(apiRoot: string, text: string, sessionId?: string | null): Promise<SessionChatResponse> {
  const url = sessionId ? `${apiRoot}/sessions/${sessionId}/message` : `${apiRoot}/message`;
  const body = sessionId ? { text } : { text };
  return fetchJson<SessionChatResponse>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
