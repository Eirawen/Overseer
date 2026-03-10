import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from '../App';

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;

  constructor(public url: string) {
    MockWebSocket.instances.push(this);
  }

  close(): void {
    this.onclose?.();
  }

  emitOpen(): void {
    this.onopen?.();
  }

  emitMessage(data: object): void {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent<string>);
  }
}

class MockSpeechRecognition {
  continuous = false;
  interimResults = false;
  lang = '';
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript?: string }>> }) => void) | null = null;
  onerror: (() => void) | null = null;
  onend: (() => void) | null = null;

  static startMock = vi.fn();
  static stopMock = vi.fn();

  start(): void {
    MockSpeechRecognition.startMock();
  }

  stop(): void {
    MockSpeechRecognition.stopMock();
  }
}

class ThrowingSpeechRecognition extends MockSpeechRecognition {
  override start(): void {
    throw new Error('not allowed');
  }
}

function jsonResponse(payload: unknown): { ok: true; json: () => Promise<unknown> } {
  return { ok: true, json: async () => payload };
}

function mockFetchRouter(): void {
  let sessionCounter = 1;
  let activeSessionId = 'sess-aaaaaaaaaaaa';
  let runs = [
    {
      run_id: 'run-1',
      status: 'queued',
      task_id: 'task-1',
      cwd: '/tmp/worktree',
      stdout_log: '/tmp/stdout.log',
      stderr_log: '/tmp/stderr.log',
    },
  ];
  let queueItems = [
    {
      request_id: 'req-1',
      status: 'pending',
      context: 'Need decision',
      type: 'decision',
      urgency: 'high',
      time_required_min: 5,
      options: ['A', 'B'],
      reply_format: 'Pick one option',
      recommendation: 'A',
      why: ['because'],
      unblocks: 'next step',
    },
  ];

  const fetchMock = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = String(init?.method ?? 'GET').toUpperCase();

    if (url.endsWith('/sessions') && method === 'POST') {
      activeSessionId = sessionCounter === 1 ? 'sess-aaaaaaaaaaaa' : `sess-aaaaaaaaaaa${sessionCounter}`;
      sessionCounter += 1;
      return jsonResponse({
        session_id: activeSessionId,
        assistant_text: `Created session ${activeSessionId}.`,
        mode: 'conversation',
        active_run_count: runs.length,
        conversation_turns: [],
      });
    }

    if (url.match(/\/sessions\/sess-[0-9a-z]{12}$/) && method === 'GET') {
      return jsonResponse({
        session_id: activeSessionId,
        mode: 'conversation',
        active_run_count: runs.length,
        conversation_turns: [
          { role: 'user', content: 'old question' },
          { role: 'assistant', content: 'old answer' },
        ],
      });
    }

    if (url.match(/\/sessions\/sess-[0-9a-z]{12}\/message$/) && method === 'POST') {
      const body = JSON.parse(String(init?.body ?? '{}'));
      const text = String(body.text ?? '');
      if (text.startsWith('/resume ')) {
        const target = text.split(/\s+/, 2)[1] ?? activeSessionId;
        activeSessionId = target;
        return jsonResponse({
          session_id: target,
          assistant_text: `Resumed ${target}.`,
          mode: 'conversation',
          active_run_count: runs.length,
          conversation_turns: [],
        });
      }
      if (text === '/tick') {
        return jsonResponse({
          session_id: activeSessionId,
          assistant_text: 'Tick processed.',
          mode: 'waiting',
          active_run_count: runs.length,
          created_run_ids: [],
        });
      }
      runs = [{ ...runs[0], status: 'running' }];
      return jsonResponse({
        session_id: activeSessionId,
        assistant_text: 'Plan it',
        mode: 'waiting',
        active_run_count: runs.length,
        created_run_ids: ['run-1'],
        run_id: 'run-1',
      });
    }

    if (url.match(/\/sessions\/sess-[0-9a-z]{12}\/tick$/) && method === 'POST') {
      return jsonResponse({
        session_id: activeSessionId,
        assistant_text: 'Tick processed.',
        mode: 'waiting',
        active_run_count: runs.length,
      });
    }

    if (url.endsWith('/runs') && method === 'GET') {
      return jsonResponse(runs);
    }
    if (url.endsWith('/queue') && method === 'GET') {
      return jsonResponse(queueItems);
    }
    if (url.includes('/runs/run-1/logs') && method === 'GET') {
      return jsonResponse({ run_id: 'run-1', lines: 200, stdout: 'seed out', stderr: '' });
    }
    if (url.endsWith('/queue/req-1/resolve') && method === 'POST') {
      queueItems = [{ ...queueItems[0], status: 'resolved' }];
      return jsonResponse({});
    }
    if (url.endsWith('/runs/run-1/cancel') && method === 'POST') {
      runs = [{ ...runs[0], status: 'canceling' }];
      return jsonResponse({});
    }

    return { ok: false, status: 404, json: async () => ({}) };
  });

  vi.stubGlobal('fetch', fetchMock);
}

describe('App', () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    MockSpeechRecognition.startMock.mockReset();
    MockSpeechRecognition.stopMock.mockReset();
    const store = new Map<string, string>();
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: (key: string) => store.get(key) ?? null,
        setItem: (key: string, value: string) => {
          store.set(key, String(value));
        },
        removeItem: (key: string) => {
          store.delete(key);
        },
        clear: () => {
          store.clear();
        },
      },
    });
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket);
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    window.__OVERSEER_FLAGS__ = undefined;
    window.SpeechRecognition = undefined;
    window.webkitSpeechRecognition = undefined;
  });

  it('renders queue details, resolves requests, and streams run logs', async () => {
    mockFetchRouter();

    render(<App />);

    await screen.findByText('TYPE:');
    await screen.findByText('Need decision');
    await screen.findByText(/RECOMMENDATION:/);
    await screen.findByText('next step');
    await screen.findByText('seed out');

    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Ship it' } });
    fireEvent.click(screen.getByRole('button', { name: 'Resolve request' }));

    await screen.findByText('Resolved req-1.');
    const fetchMock = vi.mocked(fetch);
    const resolveCall = fetchMock.mock.calls.find((call) => String(call[0]).includes('/queue/req-1/resolve')) as
      | [string, RequestInit]
      | undefined;
    if (!resolveCall) {
      throw new Error('Expected queue resolve call');
    }
    expect(resolveCall[0]).toContain('/queue/req-1/resolve');
    expect(String(resolveCall[1].body)).toContain('REPLY_FORMAT: Pick one option');

    const socket = MockWebSocket.instances[0];
    await act(async () => {
      socket.emitOpen();
    });

    await waitFor(() => {
      expect(screen.getByText(/Live activity connected/i)).toBeInTheDocument();
    });

    await act(async () => {
      socket.emitMessage({ type: 'event', run_id: 'run-1', event: { type: 'status', status: 'running' } });
    });
    await screen.findByText(/seed out/);
  });

  it('enables voice from an in-app button and persists the setting', async () => {
    window.SpeechRecognition = MockSpeechRecognition as unknown as typeof window.SpeechRecognition;
    mockFetchRouter();

    render(<App />);

    const enableButton = await screen.findByRole('button', { name: /Enable voice/i });
    fireEvent.click(enableButton);

    expect(await screen.findByRole('button', { name: /Disable voice/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Hold to talk/i })).toBeEnabled();
    expect(window.localStorage.getItem('overseer.voice.enabled')).toBe('true');
  });

  it('shows text-only fallback when enabled in unsupported browsers', async () => {
    mockFetchRouter();

    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: /Enable voice/i }));

    expect(await screen.findByRole('button', { name: /Disable voice/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Hold to talk/i })).toBeDisabled();
    expect(screen.getByText(/Voice unavailable; using text input only/i)).toBeInTheDocument();
    expect(await screen.findByText(/Voice is unavailable in this browser/i)).toBeInTheDocument();
  });

  it('supports push-to-talk with browser speech recognition when available', async () => {
    window.SpeechRecognition = MockSpeechRecognition as unknown as typeof window.SpeechRecognition;
    mockFetchRouter();

    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: /Enable voice/i }));

    const pttButton = await screen.findByRole('button', { name: /Hold to talk/i });
    await act(async () => {
      fireEvent.mouseDown(pttButton);
    });
    expect(MockSpeechRecognition.startMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('img', { name: /voice orb visualization/i })).toHaveClass('pulse');

    await act(async () => {
      fireEvent.mouseUp(pttButton);
    });
    expect(MockSpeechRecognition.stopMock).toHaveBeenCalledTimes(1);
  });

  it('recovers to text input when microphone start throws', async () => {
    window.SpeechRecognition = ThrowingSpeechRecognition as unknown as typeof window.SpeechRecognition;
    mockFetchRouter();

    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: /Enable voice/i }));

    const pttButton = await screen.findByRole('button', { name: /Hold to talk/i });
    fireEvent.mouseDown(pttButton);

    expect(await screen.findByText(/Unable to start microphone capture/i)).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /voice orb visualization/i })).not.toHaveClass('pulse');
  });

  it('routes chat messages to the active Overseer session and supports tick', async () => {
    mockFetchRouter();
    render(<App />);

    await screen.findByText(/Created session sess-aaaaaaaaaaaa/);
    expect(screen.getByText(/Session sess-aaaaaaaaaaaa · mode=/i)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/Talk to Overseer/i), { target: { value: 'build x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await screen.findByText('Plan it');
    const fetchMock = vi.mocked(fetch);
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes('/sessions/sess-aaaaaaaaaaaa/message'))).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'Tick' }));
    await screen.findByText('Tick processed.');
  });
});
