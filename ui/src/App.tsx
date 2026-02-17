import { useEffect, useMemo, useState } from 'react';

import { fetchQueue, fetchRuns, getWsRoot, type QueueRecord, type RunRecord } from './api';

const DEFAULT_API_ROOT = 'http://127.0.0.1:8765';

type ChatMessage = {
  role: 'human' | 'system';
  text: string;
};

type EventEnvelope = {
  type?: string;
  run_id?: string;
  event?: {
    status?: string;
  };
};

export function App(): JSX.Element {
  const [apiRoot, setApiRoot] = useState<string>(DEFAULT_API_ROOT);
  const [chatInput, setChatInput] = useState<string>('');
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [queueItems, setQueueItems] = useState<QueueRecord[]>([]);
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null);
  const [wsConnected, setWsConnected] = useState<boolean>(false);
  const [lastActivityAt, setLastActivityAt] = useState<Date | null>(null);

  const selectedQueueItem = useMemo(
    () => queueItems.find((item) => item.request_id === selectedRequestId) ?? null,
    [queueItems, selectedRequestId],
  );

  useEffect(() => {
    const load = async (): Promise<void> => {
      const [runRecords, queueRecords] = await Promise.all([fetchRuns(apiRoot), fetchQueue(apiRoot)]);
      setRuns(runRecords);
      setQueueItems(queueRecords);
      if (!selectedRequestId && queueRecords.length > 0) {
        setSelectedRequestId(queueRecords[0].request_id);
      }
    };

    load().catch((error: unknown) => {
      setChatMessages((prev) => [
        ...prev,
        { role: 'system', text: `Failed to load data: ${String(error)}` },
      ]);
    });
  }, [apiRoot, selectedRequestId]);

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
        if (payload.type === 'event' && payload.run_id) {
          setRuns((prev) => {
            const idx = prev.findIndex((item) => item.run_id === payload.run_id);
            if (idx < 0 || !payload.event?.status) {
              return prev;
            }
            const copy = [...prev];
            copy[idx] = { ...copy[idx], status: payload.event.status };
            return copy;
          });
        }
      } catch {
        // Ignore malformed messages for MVP scaffold resiliency.
      }
    };

    return () => {
      socket.close();
    };
  }, [apiRoot]);

  const handleSend = (): void => {
    const trimmed = chatInput.trim();
    if (!trimmed) {
      return;
    }
    setChatMessages((prev) => [...prev, { role: 'human', text: trimmed }]);
    setChatInput('');
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
                  <p>
                    <strong>Type:</strong> {selectedQueueItem.type ?? 'n/a'}
                  </p>
                  <p>
                    <strong>Urgency:</strong> {selectedQueueItem.urgency ?? 'n/a'}
                  </p>
                  <p>
                    <strong>Context:</strong> {selectedQueueItem.context || 'n/a'}
                  </p>
                  <p>
                    <strong>Task:</strong> {selectedQueueItem.task_id || 'n/a'}
                  </p>
                  <p>
                    <strong>Run:</strong> {selectedQueueItem.run_id || 'n/a'}
                  </p>
                  <p>
                    <strong>Options:</strong>{' '}
                    {(selectedQueueItem.options && selectedQueueItem.options.join(', ')) || 'n/a'}
                  </p>
                </>
              ) : (
                <p className="placeholder">Select a queue item to inspect details.</p>
              )}
            </article>
          </div>
          <h3>Runs</h3>
          <ul className="runs-list">
            {runs.map((run) => (
              <li key={run.run_id}>
                <code>{run.run_id}</code> · <span>{run.status}</span>
              </li>
            ))}
            {runs.length === 0 ? <li className="placeholder">No runs found.</li> : null}
          </ul>
        </section>
      </section>
    </main>
  );
}
