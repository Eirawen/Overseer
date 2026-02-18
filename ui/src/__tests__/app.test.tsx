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

function mockFetchSequence(): void {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => [
        {
          run_id: 'run-1',
          status: 'queued',
          task_id: 'task-1',
          cwd: '/tmp/worktree',
          stdout_log: '/tmp/stdout.log',
          stderr_log: '/tmp/stderr.log',
        },
      ],
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => [
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
      ],
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ run_id: 'run-1', lines: 200, stdout: 'seed out', stderr: '' }),
    })
    .mockResolvedValueOnce({ ok: true, json: async () => ({}) })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => [
        {
          run_id: 'run-1',
          status: 'running',
          task_id: 'task-1',
          cwd: '/tmp/worktree',
          stdout_log: '/tmp/stdout.log',
          stderr_log: '/tmp/stderr.log',
        },
      ],
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => [
        {
          request_id: 'req-1',
          status: 'resolved',
          context: 'Need decision',
          type: 'decision',
          urgency: 'high',
          options: ['A', 'B'],
          reply_format: 'Pick one option',
          recommendation: 'A',
          why: ['because'],
          unblocks: 'next step',
        },
      ],
    });

  vi.stubGlobal('fetch', fetchMock);
}

describe('App', () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    MockSpeechRecognition.startMock.mockReset();
    MockSpeechRecognition.stopMock.mockReset();
    window.localStorage.clear();
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket);
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    window.__OVERSEER_FLAGS__ = undefined;
    window.SpeechRecognition = undefined;
    window.webkitSpeechRecognition = undefined;
  });

  it('renders queue details, resolves requests, and streams run logs', async () => {
    mockFetchSequence();

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
    const resolveCall = fetchMock.mock.calls[3] as [string, RequestInit];
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
    mockFetchSequence();

    render(<App />);

    const enableButton = await screen.findByRole('button', { name: /Enable voice/i });
    fireEvent.click(enableButton);

    expect(await screen.findByRole('button', { name: /Disable voice/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Hold to talk/i })).toBeEnabled();
    expect(window.localStorage.getItem('overseer.voice.enabled')).toBe('true');
  });

  it('shows text-only fallback when enabled in unsupported browsers', async () => {
    mockFetchSequence();

    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: /Enable voice/i }));

    expect(await screen.findByRole('button', { name: /Disable voice/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Hold to talk/i })).toBeDisabled();
    expect(screen.getByText(/Voice unavailable; using text input only/i)).toBeInTheDocument();
    expect(await screen.findByText(/Voice is unavailable in this browser/i)).toBeInTheDocument();
  });

  it('supports push-to-talk with browser speech recognition when available', async () => {
    window.SpeechRecognition = MockSpeechRecognition as unknown as typeof window.SpeechRecognition;
    mockFetchSequence();

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
    mockFetchSequence();

    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: /Enable voice/i }));

    const pttButton = await screen.findByRole('button', { name: /Hold to talk/i });
    fireEvent.mouseDown(pttButton);

    expect(await screen.findByText(/Unable to start microphone capture/i)).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /voice orb visualization/i })).not.toHaveClass('pulse');
  });
});
