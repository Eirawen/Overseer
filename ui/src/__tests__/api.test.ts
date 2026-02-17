import { describe, expect, it, vi } from 'vitest';

import { cancelRun, fetchQueue, fetchRunLogs, fetchRuns, getWsRoot, resolveQueueRequest } from '../api';

describe('getWsRoot', () => {
  it('maps http to ws', () => {
    expect(getWsRoot('http://127.0.0.1:8765')).toBe('ws://127.0.0.1:8765');
  });

  it('maps https to wss', () => {
    expect(getWsRoot('https://localhost:8765')).toBe('wss://localhost:8765');
  });
});

describe('api fetch helpers', () => {
  it('returns list payloads from /runs and /queue', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => [{ run_id: 'run-1', status: 'running' }] })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ request_id: 'req-1', status: 'open', options: ['A', 'B'] }],
      });

    vi.stubGlobal('fetch', fetchMock);

    await expect(fetchRuns('http://x')).resolves.toEqual([{ run_id: 'run-1', status: 'running' }]);
    await expect(fetchQueue('http://x')).resolves.toEqual([
      { request_id: 'req-1', status: 'open', options: ['A', 'B'] },
    ]);
  });

  it('posts resolve and cancel operations plus log fetch', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({}) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({}) })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ run_id: 'run-1', lines: 12, stdout: 'out', stderr: 'err' }),
      });
    vi.stubGlobal('fetch', fetchMock);

    await expect(resolveQueueRequest('http://x', 'hr-1', { choice: 'A', rationale: 'ok' })).resolves.toBeUndefined();
    await expect(cancelRun('http://x', 'run-1')).resolves.toBeUndefined();
    await expect(fetchRunLogs('http://x', 'run-1', 12)).resolves.toEqual({
      run_id: 'run-1',
      lines: 12,
      stdout: 'out',
      stderr: 'err',
    });
  });

  it('throws on non-2xx responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 }));
    await expect(fetchRuns('http://x')).rejects.toThrow('request failed (500): http://x/runs');
  });
});
