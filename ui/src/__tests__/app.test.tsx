import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

describe('App', () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket);
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it('renders queue details, resolves requests, and streams run logs', async () => {
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

    render(<App />);

    await screen.findByText('TYPE:');
    await screen.findByText('Need decision');
    await screen.findByText('A');
    await screen.findByText('next step');
    await screen.findByText('seed out');

    fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Ship it' } });
    fireEvent.click(screen.getByRole('button', { name: 'Resolve request' }));

    await screen.findByText('Resolved req-1.');
    const resolveCall = fetchMock.mock.calls[3] as [string, RequestInit];
    expect(resolveCall[0]).toContain('/queue/req-1/resolve');
    expect(String(resolveCall[1].body)).toContain('REPLY_FORMAT: Pick one option');

    const socket = MockWebSocket.instances[0];
    socket.emitOpen();

    await waitFor(() => {
      expect(screen.getByText(/Live activity connected/i)).toBeInTheDocument();
    });

    socket.emitMessage({ type: 'event', run_id: 'run-1', event: { type: 'stdout', payload: { chunk: '\nhello' } } });
    await screen.findByText(/seed out/);
    await screen.findByText(/hello/);
  });
});
