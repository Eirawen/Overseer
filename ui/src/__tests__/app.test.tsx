import { render, screen, waitFor } from '@testing-library/react';
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
  });

  it('renders queue details and updates run status from websocket events', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => [{ run_id: 'run-1', status: 'queued' }] })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            request_id: 'req-1',
            status: 'open',
            context: 'Need decision',
            type: 'decision',
            urgency: 'high',
            options: ['A'],
          },
        ],
      });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    await screen.findByText('req-1');
    await screen.findByText('Need decision');

    const socket = MockWebSocket.instances[0];
    socket.emitOpen();

    await waitFor(() => {
      expect(screen.getByText(/Live activity connected/i)).toBeInTheDocument();
    });

    socket.emitMessage({ type: 'event', run_id: 'run-1', event: { status: 'running' } });

    await screen.findByText('run-1');
    await screen.findByText('running');
  });
});
