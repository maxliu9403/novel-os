import type { RequestRecovery } from '../api/requestRecovery';
import { canRetryUnknown } from '../lib/requestRecovery';

export default function RequestRecoveryNotice({ recovery, busy, onRetry }: {
  recovery: RequestRecovery;
  busy: boolean;
  onRetry: () => void;
}) {
  const retryable = canRetryUnknown(recovery);
  return <section aria-label="未完成请求恢复" className="mt-3 rounded-xl border border-violet/20 bg-white/70 px-4 py-3 text-[13px] leading-relaxed">
    <p>{recovery.message || '本次请求未完成，已有内容已保留。'}</p>
    {retryable && <>
      <p className="mt-1 text-[12px] text-ink-muted">已保存的内容和完成进度会保留。原请求结果无法确认，重新执行可能产生额外模型费用。</p>
      <button type="button" className="btn-secondary mt-3 !text-violet" disabled={busy} onClick={onRetry}>
        {busy ? '正在提交恢复…' : '重新执行未完成请求'}
      </button>
    </>}
  </section>;
}
