import { useEffect, useState } from 'react';
import type { CapacityOption, GuidedTurn } from '../api/workshop';
import { clearLocal, readLocal, writeLocal } from '../lib/sourceRouteDraft';

interface Draft { total: string; chapterLength: string; chapters: string; exact: boolean; targets: string[] }
const positive = (value: string) => /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) && Number(value) > 0;
const inputStyle = 'mt-2 w-full rounded-xl border border-paper-line bg-white px-3 py-2.5 text-[17px] font-semibold tabular-nums focus-visible:outline-2 focus-visible:outline-violet disabled:opacity-60';

export default function WorkshopCapacityEditor({ option, proposal, sessionId, limit, unit, disabled, onSubmit, onClose }: {
  option: CapacityOption; proposal: string; sessionId: string; limit?: number | null; unit: string; disabled: boolean;
  onSubmit: (request: NonNullable<GuidedTurn['capacity_replan']>) => void; onClose: () => void;
}) {
  const key = `novelos-capacity-edit:${sessionId}:${proposal}:${option.id}`;
  const initial: Draft = { total: String(option.total_words), chapterLength: String(limit || Math.max(...option.chapters.map(c => c.target_words))),
    chapters: String(option.chapters.length), exact: false, targets: option.chapters.map(c => String(c.target_words)) };
  const [draft, setDraft] = useState<Draft>(() => {
    const saved = readLocal<Draft>(key);
    return saved && typeof saved.total === 'string' && typeof saved.chapterLength === 'string' && typeof saved.chapters === 'string'
      && typeof saved.exact === 'boolean' && Array.isArray(saved.targets) && saved.targets.length === option.chapters.length && saved.targets.every(t => typeof t === 'string') ? saved : initial;
  });
  useEffect(() => { writeLocal(key, draft); }, [key, draft]);
  const total = Number(draft.total), length = Number(draft.chapterLength), count = Number(draft.chapters);
  const minimum = positive(draft.total) && positive(draft.chapterLength) ? Math.ceil(total / length) : 1;
  const exactPossible = count === option.chapters.length;
  const sum = draft.targets.reduce((value, target) => value + Number(target), 0);
  const exactValid = !draft.exact || (exactPossible && draft.targets.every(t => positive(t) && Number(t) <= length) && sum === total);
  const valid = positive(draft.total) && positive(draft.chapterLength) && positive(draft.chapters) && count <= 511 && count >= minimum && count <= total && exactValid;
  const changed = total !== option.total_words || length !== Number(initial.chapterLength) || count !== option.chapters.length
    || (draft.exact && draft.targets.some((t, i) => Number(t) !== option.chapters[i].target_words));
  function submit() {
    if (disabled || !valid || !changed) return;
    onSubmit({ proposal_sha256: proposal, option_id: option.id, chapters: count, total_words: total, words_per_chapter: length,
      ...(draft.exact ? { chapter_targets: option.chapters.map((chapter, i) => ({ number: chapter.number, target_words: Number(draft.targets[i]) })) } : {}) });
  }
  return <section aria-label={`${option.title} · 编辑篇幅`} className="mt-4 rounded-xl border border-violet/25 bg-white p-4 sm:p-5">
    <div className="flex flex-wrap items-center justify-between gap-2"><h5 className="text-[17px] font-semibold">调整故事的展开空间</h5><span className="text-[12px] text-ink-muted">编辑草稿 · 尚未采用</span></div>
    <p className="mt-2 text-[13px] leading-6 text-ink-muted">修改后保留人物、关键场面与结局，重新协调分章和节奏。新方案保存后仍由你选择，不会直接生成骨架。</p>
    <div className="mt-4 grid gap-4 sm:grid-cols-3">
      <label className="text-[14px] font-medium">目标总篇幅（{unit}）<input aria-label={`${option.title} · 目标总篇幅`} type="number" min={1} step={1} value={draft.total} disabled={disabled} onChange={e => setDraft(d => ({ ...d, total: e.target.value }))} className={inputStyle}/></label>
      <label className="text-[14px] font-medium">每章规划字数（{unit}）<input aria-label={`${option.title} · 每章规划字数`} type="number" min={1} step={1} value={draft.chapterLength} disabled={disabled} onChange={e => setDraft(d => ({ ...d, chapterLength: e.target.value }))} className={inputStyle}/><span className="mt-1 block text-[12px] font-normal text-ink-muted">每章目标的上限，可按需要调整。</span></label>
      <label className="text-[14px] font-medium">章节数<input aria-label={`${option.title} · 章节数`} type="number" min={1} max={511} step={1} value={draft.chapters} disabled={disabled} onChange={e => setDraft(d => ({ ...d, chapters: e.target.value, exact: Number(e.target.value) === option.chapters.length && d.exact }))} className={inputStyle}/></label>
    </div>
    <div aria-live="polite" className="mt-3 text-[13px] leading-6 text-ink-muted">
      {positive(draft.total) && positive(draft.chapters) && <p>调整后平均约 {Math.round(total / count).toLocaleString()} {unit} / 章。</p>}
      {minimum > count && <p className="text-amber-800">当前总篇幅至少需要 {minimum} 章，才能符合每章设置。{minimum <= 511 && <button type="button" className="ml-2 text-violet underline" disabled={disabled} onClick={() => setDraft(d => ({ ...d, chapters: String(minimum), exact: false }))}>使用 {minimum} 章</button>}</p>}
      {(!positive(draft.total) || !positive(draft.chapterLength) || !positive(draft.chapters) || count > 511 || count > total) && <p className="text-amber-800">篇幅和章节数请填写正整数，章节数不超过 511，且每章至少有 1 {unit}。</p>}
    </div>
    <label className="mt-4 flex items-start gap-2 text-[14px]"><input type="checkbox" className="mt-1 accent-violet" disabled={disabled || !exactPossible} checked={draft.exact} onChange={e => setDraft(d => ({ ...d, exact: e.target.checked }))}/>逐章调整字数<span className="text-[12px] text-ink-muted">{exactPossible ? '保留当前分章，合计同步为目标总篇幅。' : '先完成新的分章安排，再逐章调整。'}</span></label>
    {draft.exact && <div className="mt-3 grid max-h-72 gap-3 overflow-y-auto sm:grid-cols-2 lg:grid-cols-3">{option.chapters.map((chapter, index) => <label key={chapter.number} className="text-[13px]">第 {chapter.number} 章（{unit}）<input aria-label={`第 ${chapter.number} 章目标字数`} type="number" min={1} max={length || undefined} step={1} value={draft.targets[index]} disabled={disabled || !exactPossible} className={inputStyle} onChange={e => setDraft(d => { const targets = d.targets.map((value, i) => i === index ? e.target.value : value); return { ...d, targets, total: targets.every(positive) ? String(targets.reduce((sum, value) => sum + Number(value), 0)) : d.total }; })}/></label>)}</div>}
    {draft.exact && !exactValid && <p role="status" className="mt-3 text-[13px] text-amber-800">逐章目标须为正整数、不超过每章设置，且合计等于目标总篇幅；调整章数前请取消逐章编辑。</p>}
    <div className="mt-5 flex flex-wrap gap-3"><button type="button" className="btn-primary" disabled={disabled || !valid || !changed} onClick={submit}>保存调整并重新安排</button><button type="button" className="btn-secondary" disabled={disabled} onClick={onClose}>收起，保留草稿</button><button type="button" className="btn-ghost" disabled={disabled} onClick={() => { clearLocal(key); setDraft(initial); }}>恢复原方案数字</button></div>
  </section>;
}
