import { useState } from 'react';
import type { SkeletonReview } from '../api/workshop';

const labels: Record<string, string> = { opening: '开篇代入', causality: '故事因果', reader_value: '阅读回报', character: '人物与关系', capacity: '场景展开', payoff: '结局兑现' };

export default function WorkshopSkeletonReview({ review, disabled, onResolve }: {
  review: SkeletonReview; disabled: boolean; onResolve: (ids: string[]) => void;
}) {
  const [selection, setSelection] = useState<{ hash?: string; ids: string[] }>({ ids: [] });
  const selected = selection.hash === review.review_sha256 ? selection.ids : review.issues.map(issue => issue.id);
  return <section aria-label="骨架质量与修订" className="mb-5 rounded-2xl border border-paper-line bg-white/85 p-5 sm:p-6">
    <h3 className="text-[20px] font-semibold">让修改落到故事里</h3>
    <p className="mt-2 text-[14px] leading-7 text-ink-muted">本轮骨架复核建议 · 原文来自第 {review.source_revision} 版。结构检查与读者体验分开记录。</p>
    {review.status === 'unavailable' ? <p className="mt-3 text-[15px] leading-7">{review.notice}</p>
      : review.status === 'resolved' ? <p role="status" className="mt-4 rounded-xl bg-violet-soft p-4 text-[15px] leading-7">{review.decision === 'keep_original' ? '已按你的决定保留当前骨架。' : `已采用 ${review.selected_ids?.length ?? 0} 项修订，保存为第 ${review.resolved_revision} 版。`}结构检查通过；正文阅读效果尚未验证。</p>
      : <><p className="mt-3 text-[16px] leading-7">{review.summary}</p>
        {review.status === 'clear' && <p className="mt-3 text-[14px] text-ink-muted">本轮未提出可操作的局部修改；这不代表正文质量已经通过验收。</p>}
        <div className="mt-5 space-y-4">{review.issues.map(issue => <article key={issue.id} className="rounded-2xl border border-paper-line p-4 sm:p-5">
          <label className="flex cursor-pointer items-start gap-3 text-[16px] font-semibold"><input type="checkbox" className="mt-1 size-4 accent-violet" disabled={disabled} checked={selected.includes(issue.id)} onChange={e => setSelection({ hash: review.review_sha256, ids: e.target.checked ? [...selected, issue.id] : selected.filter(id => id !== issue.id) })} />第 {issue.chapter} 章 · {labels[issue.category] ?? issue.category}</label>
          <p className="mt-3 text-[15px] leading-7">{issue.problem}</p>
          <div className="mt-4 grid gap-4 lg:grid-cols-2"><div className="rounded-xl bg-slate-50 p-4"><p className="mb-2 text-[13px] font-medium text-ink-muted">当前设计</p><p className="whitespace-pre-wrap text-[14px] leading-7">{issue.quote}</p></div><div className="rounded-xl bg-violet-soft/55 p-4"><p className="mb-2 text-[13px] font-medium text-violet">建议改为</p><p className="whitespace-pre-wrap text-[14px] leading-7">{issue.replacement}</p></div></div>
        </article>)}</div>
        {Boolean(review.notes?.length) && <div className="mt-4 rounded-xl bg-slate-50 p-4"><h4 className="text-[15px] font-medium">仍需留意</h4><ul className="mt-2 list-disc space-y-2 pl-5 text-[14px] leading-7">{review.notes!.map((note, i) => <li key={i}>{note}</li>)}</ul></div>}
        {review.status === 'proposed' && <div className="mt-5"><p className="mb-3 text-[14px] leading-7 text-ink-muted">查看修改是否符合你的故事意图。保存将应用选中项并重新检查骨架结构，原版保留；不会调用模型或启动正文。</p><div className="flex flex-wrap gap-3"><button type="button" disabled={disabled || !selected.length} className="btn-primary" onClick={() => onResolve(selected)}>采用 {selected.length} 项修订并保存</button><button type="button" disabled={disabled} className="btn-secondary" onClick={() => onResolve([])}>保留当前骨架</button></div></div>}
      </>}
  </section>;
}
