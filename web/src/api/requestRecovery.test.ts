import { afterEach, expect, test, vi } from 'vitest';
import { api } from './client';
import { workshopApi } from './workshop';

afterEach(() => vi.unstubAllGlobals());

const recovery = { action: 'retry_unknown' as const, operation_id: 'recovery-operation', request_sha256: 'a'.repeat(64), receipt_sha256: 'b'.repeat(64) };

test('task retry binds authorization in the existing resume request', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'running' }) });
  vi.stubGlobal('fetch', fetch);
  await api.resumeProjectTask('project / one', 'run / one', { request_recovery: recovery });
  const [url, options] = fetch.mock.calls[0];
  expect(url).toMatch(/\/projects\/project%20%2F%20one\/tasks\/run%20%2F%20one\/resume$/);
  expect(options.method).toBe('POST');
  expect(JSON.parse(options.body)).toEqual({ request_recovery: recovery });
});

test('Workshop retains ordinary resume and carries explicit recovery beside revision', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'running' }) });
  vi.stubGlobal('fetch', fetch);
  await workshopApi.resume('session / one', 7);
  await workshopApi.resume('session / one', 7, recovery);
  expect(fetch.mock.calls[1][0]).toMatch(/\/sessions\/session%20%2F%20one\/resume$/);
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ revision: 7 });
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ revision: 7, request_recovery: recovery });
});

test('stale request rejection is surfaced without automatic resend', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: { message: '请求状态已变化，请刷新后再操作。' } }) });
  vi.stubGlobal('fetch', fetch);
  await expect(workshopApi.resume('session', 7, recovery)).rejects.toThrow('请求状态已变化');
  expect(fetch).toHaveBeenCalledTimes(1);
});
