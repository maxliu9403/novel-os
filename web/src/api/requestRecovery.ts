export interface RequestRecovery {
  can_resume: boolean;
  can_retry_unknown: boolean;
  reason: string;
  message: string;
  request_sha256?: string;
  receipt_sha256?: string;
  execution?: {
    local_outcome?: string;
    reason?: string;
    message?: string;
    elapsed_seconds?: number;
    timeout_seconds?: number;
    timeout_scope?: string;
    observed_at?: string;
    attempt_count?: number;
  };
}

export interface RequestRecoveryRetry {
  action: 'retry_unknown';
  operation_id: string;
  request_sha256: string;
  receipt_sha256: string;
}
