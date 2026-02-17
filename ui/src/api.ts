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
