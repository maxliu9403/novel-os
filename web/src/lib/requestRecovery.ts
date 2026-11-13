import type { RequestRecovery, RequestRecoveryRetry } from '../api/requestRecovery';
import { operationFor } from './sourceRouteDraft';

type BoundRecovery = RequestRecovery & { request_sha256: string; receipt_sha256: string };
const pendingOperations = new Map<string, RequestRecoveryRetry>();

export function canRetryUnknown(recovery: RequestRecovery | null | undefined): recovery is BoundRecovery {
  return Boolean(recovery?.can_retry_unknown && recovery.request_sha256 && recovery.receipt_sha256);
}

export function blocksOrdinaryResume(recovery: RequestRecovery | null | undefined) {
  return Boolean(recovery && (recovery.can_retry_unknown || !recovery.can_resume));
}

export function requestRecoveryOperation(owner: string, recovery: BoundRecovery): RequestRecoveryRetry {
  // Each exact receipt keeps its identity across reloads, lost acknowledgements,
  // and stale reads arriving after a newer receipt. Never clear it on submit.
  const key = `novelos-request-recovery:${owner}:${recovery.request_sha256}:${recovery.receipt_sha256}`;
  const pending = pendingOperations.get(key);
  if (pending) return pending;
  const operation = operationFor(key, {
    action: 'retry_unknown' as const,
    request_sha256: recovery.request_sha256,
    receipt_sha256: recovery.receipt_sha256,
  });
  pendingOperations.set(key, operation);
  return operation;
}
