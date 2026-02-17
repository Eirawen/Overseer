import { useEffect, useMemo, useState } from 'react';

import {
  cancelRun,
  fetchQueue,
  fetchRunLogs,
  fetchRuns,
  getWsRoot,
  resolveQueueRequest,
  type QueueRecord,
  type RunRecord,
} from './api';

const DEFAULT_API_ROOT = 'http://127.0.0.1:8765';

type ChatMessage = {
  role: 'human' | 'system';
  text: string;
};

type EventEnvelope = {
  type?: string;
  run_id?: string;
  event?: {
    type?: string;
    status?: string;
    payload?: {
      chunk?: string;
    };
  };
};


function buildFormattedRationale(replyFormat: string | null | undefined, choice: string, rationale: string): string {
  const trimmed = rationale.trim();
  if (!replyFormat) {
    return trimmed;
  }
  return `REPLY_FORMAT: ${replyFormat}
CHOICE: ${choice}
RATIONALE: ${trimmed}`;
}

export function App(): JSX.Element {
  const [apiRoot, setApiRoot] = useState<string>(DEFAULT_API_ROOT);
  const [chatInput, setChatInput] = useState<string>('');
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [queueItems, setQueueItems] = useState<QueueRecord[]>([]);
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [resolutionChoice, setResolutionChoice] = useState<string>('');
  const [resolutionRationale, setResolutionRationale] = useState<string>('');
  const [runStdout, setRunStdout] = useState<string>('');
  const [runStderr, setRunStderr] = useState<string>('');
  const [wsConnected, setWsConnected] = useState<boolean>(false);
  const [lastActivityAt, setLastActivityAt] = useState<Date | null>(null);

  const selectedQueueItem = useMemo(
    () => queueItems.find((item) => item.request_id === selectedRequestId) ?? null,
    [queueItems, selectedRequestId],
  );
  const selectedRun = useMemo(
    () => runs.find((item) => item.run_id === selectedRunId) ?? null,
    [runs, selectedRunId],
  );

  const loadData = async (): Promise<void> => {
    const [runRecords, queueRecords] = await Promise.all([fetchRuns(apiRoot), fetchQueue(apiRoot)]);
    setRuns(runRecords);
    setQueueItems(queueRecords);
    if (!selectedRequestId && queueRecords.length > 0) {
      setSelectedRequestId(queueRecords[0].request_id);
    }
    if (!selectedRunId && runRecords.length > 0) {
      setSelectedRunId(runRecords[0].run_id);
    }
  };

  useEffect(() => {
    let stopped = false;
    const refresh = (): void => {
      loadData().catch((error: unknown) => {
        if (stopped) {
          return;
        }
        setChatMessages((prev) => [...prev, { role: 'system', text: `Failed to load data: ${String(error)}` }]);
      });
    };
    refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [apiRoot]);

  useEffect(() => {
    const firstOption = selectedQueueItem?.options?.[0] ?? '';
    setResolutionChoice(firstOption);
    setResolutionRationale('');
  }, [selectedQueueItem?.request_id]);

  useEffect(() => {
    if (!selectedRunId) {
      setRunStdout('');
      setRunStderr('');
      return;
    }
    fetchRunLogs(apiRoot, selectedRunId, 200)
      .then((payload) => {
        setRunStdout(payload.stdout);
        setRunStderr(payload.stderr);
      })
      .catch((error: unknown) => {
        setRunStdout('');
        setRunStderr(`Failed to load logs: ${String(error)}`);
      });
  }, [apiRoot, selectedRunId]);

  useEffect(() => {
    const socket = new WebSocket(`${getWsRoot(apiRoot)}/events`);
    socket.onopen = () => {
      setWsConnected(true);
    };
    socket.onclose = () => {
      setWsConnected(false);
    };
    socket.onerror = () => {
      setWsConnected(false);
    };
    socket.onmessage = (event: MessageEvent<string>) => {
      setLastActivityAt(new Date());
      try {
        const payload = JSON.parse(event.data) as EventEnvelope;
        if (payload.type !== 'event' || !payload.run_id) {
          return;
        }

        setRuns((prev) => {
          const idx = prev.findIndex((item) => item.run_id === payload.run_id);
          if (idx < 0 || !payload.event?.status) {
            return prev;
          }
          const copy = [...prev];
          copy[idx] = { ...copy[idx], status: payload.event.status };
          return copy;
        });

        if (payload.run_id !== selectedRunId) {
          return;
        }
        if (payload.event?.type === 'stdout' && payload.event.payload?.chunk) {
          setRunStdout((prev) => `${prev}${payload.event?.payload?.chunk ?? ''}`);
        }
        if (payload.event?.type === 'stderr' && payload.event.payload?.chunk) {
          setRunStderr((prev) => `${prev}${payload.event?.payload?.chunk ?? ''}`);
        }
      } catch {
        // Ignore malformed messages for MVP scaffold resiliency.
      }
    };

    return () => {
      socket.close();
    };
  }, [apiRoot, selectedRunId]);

  const handleSend = (): void => {
    const trimmed = chatInput.trim();
    if (!trimmed) {
      return;
    }
    setChatMessages((prev) => [...prev, { role: 'human', text: trimmed }]);
    setChatInput('');
  };

  const handleResolve = async (): Promise<void> => {
    if (!selectedQueueItem || !resolutionChoice || !resolutionRationale.trim()) {
      return;
    }
    try {
      await resolveQueueRequest(apiRoot, selectedQueueItem.request_id, {
        choice: resolutionChoice,
        rationale: buildFormattedRationale(selectedQueueItem.reply_format, resolutionChoice, resolutionRationale),
      });
      await loadData();
      setChatMessages((prev) => [...prev, { role: 'system', text: `Resolved ${selectedQueueItem.request_id}.` }]);
    } catch (error: unknown) {
      setChatMessages((prev) => [...prev, { role: 'system', text: `Failed to resolve request: ${String(error)}` }]);
    }
  };

  const handleCancelRun = async (): Promise<void> => {
    if (!selectedRunId) {
      return;
    }
    try {
      await cancelRun(apiRoot, selectedRunId);
      await loadData();
    } catch (error: unknown) {
      setChatMessages((prev) => [...prev, { role: 'system', text: `Failed to cancel run: ${String(error)}` }]);
    }
  };

  const copyText = async (label: string, value?: string): Promise<void> => {
    if (!value) {
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      setChatMessages((prev) => [...prev, { role: 'system', text: `Copied ${label} path.` }]);
    } catch {
      setChatMessages((prev) => [...prev, { role: 'system', text: `Failed to copy ${label} path.` }]);
    }
  };

  return (
    <main className="app-shell">
      <header className="toolbar">
        <h1>Overseer UI MVP</h1>
        <label>
          API Root
          <input value={apiRoot} onChange={(event) => setApiRoot(event.target.value)} />
        </label>
        <span className={`live-indicator ${wsConnected ? 'on' : 'off'}`}>
          {wsConnected ? 'Live activity connected' : 'Live activity disconnected'}
          {lastActivityAt ? ` · Last event ${lastActivityAt.toLocaleTimeString()}` : ''}
        </span>
      </header>

      <section className="pane-grid">
        <section className="pane left">
          <h2>Chat</h2>
          <div className="chat-stream">
            {chatMessages.length === 0 ? <p className="placeholder">No messages yet.</p> : null}
            {chatMessages.map((message, idx) => (
              <article key={`${message.role}-${idx}`} className={`chat-msg ${message.role}`}>
                <strong>{message.role}</strong>
                <p>{message.text}</p>
              </article>
            ))}
          </div>
          <div className="chat-input-row">
            <input
              value={chatInput}
              onChange={(event) => setChatInput(event.target.value)}
              placeholder="Type message (MVP local-only)"
            />
            <button type="button" onClick={handleSend}>
              Send
            </button>
          </div>
        </section>

        <section className="pane right">
          <h2>Human Queue</h2>
          <div className="queue-layout">
            <ul className="queue-list">
              {queueItems.map((item) => (
                <li key={item.request_id}>
                  <button
                    type="button"
                    onClick={() => setSelectedRequestId(item.request_id)}
                    className={item.request_id === selectedRequestId ? 'selected' : ''}
                  >
                    <span>{item.request_id}</span>
                    <small>{item.status}</small>
                  </button>
                </li>
              ))}
              {queueItems.length === 0 ? <li className="placeholder">Queue is empty.</li> : null}
            </ul>

            <article className="queue-detail">
              {selectedQueueItem ? (
                <>
                  <h3>{selectedQueueItem.request_id}</h3>
                  <p><strong>TYPE:</strong> {selectedQueueItem.type ?? 'n/a'}</p>
                  <p><strong>URGENCY:</strong> {selectedQueueItem.urgency ?? 'n/a'}</p>
                  <p><strong>TIME_REQUIRED_MIN:</strong> {selectedQueueItem.time_required_min ?? 'n/a'}</p>
                  <p><strong>CONTEXT:</strong> {selectedQueueItem.context || 'n/a'}</p>
                  <p><strong>OPTIONS:</strong> {(selectedQueueItem.options && selectedQueueItem.options.join(' | ')) || 'n/a'}</p>
                  <p><strong>RECOMMENDATION:</strong> {selectedQueueItem.recommendation || 'n/a'}</p>
                  <p><strong>WHY:</strong> {(selectedQueueItem.why && selectedQueueItem.why.join(' | ')) || 'n/a'}</p>
                  <p><strong>UNBLOCKS:</strong> {selectedQueueItem.unblocks || 'n/a'}</p>
                  <p><strong>REPLY_FORMAT:</strong> {selectedQueueItem.reply_format || 'n/a'}</p>

                  <label>
                    Resolve choice
                    <select
                      value={resolutionChoice}
                      onChange={(event) => setResolutionChoice(event.target.value)}
                      disabled={selectedQueueItem.status === 'resolved'}
                    >
                      {(selectedQueueItem.options ?? []).map((option) => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Rationale
                    <textarea
                      value={resolutionRationale}
                      onChange={(event) => setResolutionRationale(event.target.value)}
                      disabled={selectedQueueItem.status === 'resolved'}
                    />
                  </label>
                  <button type="button" onClick={() => { void handleResolve(); }} disabled={selectedQueueItem.status === 'resolved' || !resolutionChoice || !resolutionRationale.trim()}>
                    Resolve request
                  </button>
                </>
              ) : (
                <p className="placeholder">Select a queue item to inspect details.</p>
              )}
            </article>
          </div>

          <h3>Runs</h3>
          <div className="runs-layout">
            <ul className="runs-list">
              {runs.map((run) => (
                <li key={run.run_id}>
                  <button type="button" onClick={() => setSelectedRunId(run.run_id)} className={run.run_id === selectedRunId ? 'selected' : ''}>
                    <code>{run.run_id}</code> · <span>{run.status}</span>
                  </button>
                </li>
              ))}
              {runs.length === 0 ? <li className="placeholder">No runs found.</li> : null}
            </ul>

            <article className="run-detail">
              {selectedRun ? (
                <>
                  <h4>{selectedRun.run_id}</h4>
                  <p><strong>Status:</strong> {selectedRun.status}</p>
                  <p><strong>Task:</strong> {selectedRun.task_id ?? 'n/a'}</p>
                  <div className="run-actions">
                    <button type="button" onClick={() => { void handleCancelRun(); }}>Cancel run</button>
                    <button type="button" onClick={() => { void copyText('worktree', selectedRun.cwd); }}>Copy worktree path</button>
                    <button type="button" onClick={() => { void copyText('stdout log', selectedRun.stdout_log); }}>Copy stdout path</button>
                    <button type="button" onClick={() => { void copyText('stderr log', selectedRun.stderr_log); }}>Copy stderr path</button>
                  </div>
                  <div className="log-panels">
                    <section>
                      <h5>stdout</h5>
                      <pre>{runStdout || '(no stdout yet)'}</pre>
                    </section>
                    <section>
                      <h5>stderr</h5>
                      <pre>{runStderr || '(no stderr yet)'}</pre>
                    </section>
                  </div>
                </>
              ) : (
                <p className="placeholder">Select a run to inspect details.</p>
              )}
            </article>
          </div>
        </section>
      </section>
    </main>
  );
}
