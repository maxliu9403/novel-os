import { afterEach, expect, test, vi } from 'vitest';
import { sourceRoutesApi } from './sourceRoutes';

afterEach(() => vi.unstubAllGlobals());

test('explicit unknown retry sends its receipt binding to the existing run', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'running' }) });
  vi.stubGlobal('fetch', fetch);
  const body = { action: 'retry_unknown' as const, operation_id: 'retry-operation', request_sha256: 'a'.repeat(64), receipt_sha256: 'b'.repeat(64) };
  await sourceRoutesApi.retryUnknown('source / one', 'run / one', body);
  const [url, options] = fetch.mock.calls[0];
  expect(url).toMatch(/\/source-books\/source%20%2F%20one\/route-runs\/run%20%2F%20one\/resume$/);
  expect(options.method).toBe('POST');
  expect(JSON.parse(options.body)).toEqual(body);
});

test('stale receipt rejection is returned to the author without silently starting another run', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: '请求状态已变化，请刷新后再操作。' }) });
  vi.stubGlobal('fetch', fetch);
  await expect(sourceRoutesApi.retryUnknown('source', 'run', {
    action: 'retry_unknown', operation_id: 'retry-operation', request_sha256: 'a'.repeat(64), receipt_sha256: 'b'.repeat(64),
  })).rejects.toThrow('请求状态已变化，请刷新后再操作。');
  expect(fetch).toHaveBeenCalledTimes(1);
});
