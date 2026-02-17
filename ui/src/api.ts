export type RunRecord = {
  run_id: string;
  status: string;
  task_id?: string | null;
};

export type QueueRecord = {
  request_id: string;
  status: string;
  task_id?: string | null;
  run_id?: string | null;
  type?: string | null;
  urgency?: string | null;
  context?: string | null;
  options?: string[] | null;
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

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
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
