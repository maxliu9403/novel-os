import { afterEach, expect, test, vi } from 'vitest';
import { blocksOrdinaryResume, canRetryUnknown, requestRecoveryOperation } from './requestRecovery';

const recovery = { can_resume: false, can_retry_unknown: true, reason: 'request_cancelled', message: '本次请求已停止。', request_sha256: 'a'.repeat(64), receipt_sha256: 'b'.repeat(64) };

afterEach(() => vi.restoreAllMocks());

test('only a bound unknown permits explicit retry, while legacy resume remains available', () => {
  expect(canRetryUnknown(recovery)).toBe(true);
  expect(canRetryUnknown({ ...recovery, receipt_sha256: undefined })).toBe(false);
  expect(blocksOrdinaryResume(recovery)).toBe(true);
  expect(blocksOrdinaryResume(undefined)).toBe(false);
  expect(blocksOrdinaryResume({ ...recovery, can_resume: true, can_retry_unknown: false })).toBe(false);
});

test('each receipt retains its operation after newer bindings and local storage failures', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('storage unavailable'); });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage unavailable'); });
  const first = requestRecoveryOperation('memory-fallback', recovery);
  const next = requestRecoveryOperation('memory-fallback', { ...recovery, receipt_sha256: 'c'.repeat(64) });
  expect(next.operation_id).not.toBe(first.operation_id);
  expect(requestRecoveryOperation('memory-fallback', recovery)).toEqual(first);
});
