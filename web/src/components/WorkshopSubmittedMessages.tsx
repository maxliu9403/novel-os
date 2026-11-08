import type { WorkshopSession } from '../api/workshop';

export interface SubmittedTurn {
  priorMessageCount: number;
  content: string;
}

/** A submission receipt, separate from the draft and the assistant's reply. */
export default function WorkshopSubmittedMessages({ session, submitted, onHistory }: {
  session: WorkshopSession; submitted?: SubmittedTurn; onHistory: () => void;
}) {
  const localReceipt = submitted && session.messages.length <= submitted.priorMessageCount;
  const lastUser = session.messages.findLastIndex(message => message.role === 'user');
  const previousAssistant = session.messages.findLastIndex((message, index) => index < lastUser && message.role === 'assistant');
  const messages = localReceipt ? [submitted.content]
    : session.messages.slice(previousAssistant + 1, lastUser + 1).filter(message => message.role === 'user').map(message => message.content);
  if (!messages.length) return null;
  return <section aria-label="已发送的内容" className="mb-5 rounded-2xl border border-violet/20 bg-violet-soft/40 p-5">
    <div className="mb-3 flex items-center justify-between gap-3">
      <h4 className="text-[16px] font-semibold">你已发送的内容</h4>
      <button type="button" className="btn-ghost !py-1 !text-[13px]" onClick={onHistory}>查看讨论记录</button>
    </div>
    <div className="max-h-56 space-y-3 overflow-y-auto pr-2 text-[15px] leading-relaxed" tabIndex={0} aria-label="已发送内容全文">
      {messages.map((content, index) => <p key={index} className="whitespace-pre-wrap break-words">{content}</p>)}
    </div>
    <p role="status" className="mt-3 text-[12px] text-ink-muted">{localReceipt ? '提交已成功，正在同步记录。' : '已保存在讨论记录中。'}{session.status === 'running' && ' 模型正在处理，可继续查看已发送内容。'}</p>
  </section>;
}
