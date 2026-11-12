import { useEffect, useState, type ReactNode } from 'react';
import type { CapacityOption, GuidedTurn, WorkshopSession } from '../api/workshop';
import { workshopNextStep } from '../lib/workshopNextStep';
import WorkshopCapacityEditor from './WorkshopCapacityEditor';

function CapacityCard({ option, limit, unit, locked, proposal, onAdopt, onRepartition, children, sessionId, editing, onEdit }: {
  option: CapacityOption; limit?: number | null; unit: string; locked: boolean; proposal: string;
  onAdopt: (choice: NonNullable<GuidedTurn['capacity_choice']>) => void;
  onRepartition?: (request: NonNullable<GuidedTurn['capacity_replan']>) => void; children: ReactNode;
  sessionId: string; editing: boolean; onEdit: (value: boolean) => void;
}) {
  const count = option.chapters.length;
  const longest = Math.max(...option.chapters.map(ch => ch.target_words));
  const exceeds = Boolean(limit && longest > limit);
  const minimum = limit ? Math.ceil(option.total_words / limit) : 1;
  const [chapterInput, setChapterInput] = useState(String(exceeds ? minimum : count));
  const selected = Number(chapterInput);
  const valid = Number.isInteger(selected) && selected >= minimum && selected <= Math.min(511, option.total_words);
  const replan = exceeds || selected !== count;
  return <article aria-label={option.title} className={`flex min-w-0 flex-col rounded-2xl border p-5 ${option.recommended ? 'border-violet/35 bg-violet-soft/40' : 'border-paper-line bg-white/80'}`}>
    <div className="flex flex-wrap items-center gap-2"><h4 className="text-[18px] font-semibold">{option.title}</h4>{option.recommended && <span className="rounded-full bg-violet-soft px-2 py-1 text-[12px] text-violet">建议方案</span>}</div>
    <p className="my-3 text-[24px] font-semibold tabular-nums">约 {option.total_words.toLocaleString()} <span className="text-[14px] font-normal">{unit}</span><span className="ml-3 text-[15px] font-normal text-ink-muted">完整故事篇幅</span></p>
    <dl className="mb-4 grid grid-cols-3 gap-3 rounded-xl border border-paper-line bg-white/80 p-3 text-[13px] text-ink-muted">
      <div><dt>当前章数</dt><dd className="mt-1 text-[17px] font-semibold text-ink-text">{count} 章</dd></div>
      <div><dt>平均每章</dt><dd className="mt-1 text-[17px] font-semibold text-ink-text">{Math.round(option.total_words / count).toLocaleString()} <span className="text-[12px] font-normal">{unit}</span></dd></div>
      <div><dt>最长一章</dt><dd className={`mt-1 text-[17px] font-semibold ${exceeds ? 'text-amber-700' : 'text-ink-text'}`}>{longest.toLocaleString()} <span className="text-[12px] font-normal">{unit}</span></dd></div>
    </dl>
    <p className="flex-1 text-[15px] leading-7 text-ink-muted">{option.tradeoff}</p>
    {onRepartition && !editing && <button type="button" className="btn-secondary mt-4 self-start" disabled={locked} onClick={() => onEdit(true)}>编辑篇幅方案</button>}
    {exceeds && <p className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-[14px] leading-6 text-amber-800">部分章节超过你设置的 {limit?.toLocaleString()} {unit}。增加章节，保留故事的展开空间。</p>}
    {children}
    {editing && onRepartition && <WorkshopCapacityEditor key={`${proposal}:${option.id}`} option={option} proposal={proposal} sessionId={sessionId} limit={limit} unit={unit} disabled={locked} onClose={() => onEdit(false)} onSubmit={request => { onEdit(false); onRepartition(request); }}/>}
    {!editing && limit && onRepartition && <div className="mt-4 rounded-xl border border-paper-line bg-white/80 p-4">
      <div className="flex flex-wrap items-center gap-3"><label className="text-[15px] font-medium">调整章节数 <input aria-label={`${option.title} · 章节数`} type="number" min={minimum} max={Math.min(511, option.total_words)} step={1} value={chapterInput} disabled={locked}
        onChange={event => setChapterInput(event.target.value)} className="ml-2 w-20 rounded-lg border border-paper-line bg-white px-3 py-2 text-[17px] font-semibold tabular-nums focus-visible:outline-2 focus-visible:outline-violet"/> 章</label>
        {minimum <= 511 && <button type="button" disabled={locked} className="text-[13px] text-violet underline-offset-4 hover:underline" onClick={() => setChapterInput(String(minimum))}>建议 {minimum} 章起</button>}</div>
      <p className="mt-2 text-[13px] leading-6 text-ink-muted">{valid ? `调整后平均约 ${Math.round(option.total_words / selected).toLocaleString()} ${unit} / 章，总篇幅保持不变。` : minimum > 511 ? '当前总篇幅与每章设置超出 511 章限制，请先调整篇幅要求。' : `请选择 ${minimum}–${Math.min(511, option.total_words)} 之间的整数章数。`}</p>
    </div>}
    {!editing && (replan ? <><button type="button" disabled={locked || !valid || !onRepartition} className="btn-primary mt-4 self-start" onClick={() => onRepartition?.({ proposal_sha256: proposal, option_id: option.id, chapters: selected })}>按 {valid ? selected : '新'} 章重新安排</button>
      <p className="mt-2 text-[13px] leading-6 text-ink-muted">模型安排自然分章后，先确认新方案，再生成骨架。</p></>
      : <button type="button" disabled={locked} className="btn-primary mt-4 self-start" onClick={() => onAdopt({ proposal_sha256: proposal, option_id: option.id })}>采用「{option.title}」并生成骨架</button>)}
  </article>;
}

export default function WorkshopCapacity({ session, disabled, draft, onAdopt, onPlan, onRepartition, onEditingChange }: {
  session: WorkshopSession; disabled: boolean; draft: string;
  onAdopt: (choice: NonNullable<GuidedTurn['capacity_choice']>) => void; onPlan: () => void;
  onRepartition?: (request: NonNullable<GuidedTurn['capacity_replan']>) => void;
  onEditingChange?: (editing: boolean) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  useEffect(() => () => onEditingChange?.(false), [onEditingChange]);
  function changeEditing(value: string | null) { setEditing(value); onEditingChange?.(Boolean(value)); }
  if (session.creative_design?.policy.capacity_version !== 'story-capacity.v1') return null;
  const state = session.capacity;
  const pending = Boolean(session.creative_design.story_state?.pending);
  const confirmed = state?.status === 'confirmed' ? state.confirmed : null;
  const plan = state?.plan;
  const needsAdjustment = state?.status === 'stale' && state.adjustment?.status === 'needs_more_space' && plan && state.proposal_sha256;
  const proposed = state?.status === 'proposed' && plan && state.proposal_sha256;
  const unit = (confirmed?.count_unit ?? plan?.count_unit) === 'characters' ? '字' : '词';
  const locked = disabled || pending || Boolean(draft.trim());
  const replanRequest = session.capacity_replan;
  const hint = pending ? '先完成本轮故事走向选择，再确定篇幅。' : draft.trim() ? '先发送下方补充，更新篇幅建议后再采用。' : '采用方案后直接生成骨架，暂不启动正文。';
  const allocation = (option: CapacityOption) => <details className="mt-4 border-t border-paper-line pt-3">
    <summary className="cursor-pointer text-[14px] text-ink-muted">查看分章安排</summary>
    <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-[14px]"><thead><tr className="text-ink-muted"><th className="py-2 pr-3">章节</th><th>安排的故事场景</th><th className="text-right">预计篇幅</th></tr></thead><tbody>
      {option.chapters.map(ch => <tr key={ch.number} className="border-t border-paper-line"><td className="py-3 pr-3 whitespace-nowrap">第 {ch.number} 章</td><td className="pr-4">{ch.scene_ids.map(id => { const scene = plan?.scenes.find(s => s.id === id); return scene?.partition_label ?? scene?.title ?? id; }).join(' · ')}</td><td className="whitespace-nowrap text-right tabular-nums">{ch.target_words.toLocaleString()} {unit}</td></tr>)}
    </tbody></table></div>
  </details>;
  return <section id="workshop-capacity" tabIndex={-1} aria-label="故事容量与篇幅" className="my-5 rounded-2xl border border-paper-line bg-white/80 p-5 sm:p-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-[20px] font-semibold text-ink-text">给故事足够的展开空间</h3><span className="rounded-full bg-violet-soft px-3 py-1 text-[13px] text-violet">{confirmed ? '篇幅已确认' : '编辑规划 · 篇幅估算'}</span></div>
    {confirmed ? <><p className="mt-3 text-[16px] leading-relaxed">{confirmed.option.chapters.length} 章 · 约 {confirmed.option.total_words.toLocaleString()} {unit} · {confirmed.language}</p><p className="mt-2 text-[14px] text-ink-muted">已保存你的选择。正文允许合理浮动；分章目标会随骨架传递到写作规划。</p>{allocation(confirmed.option)}
      {onRepartition && state?.proposal_sha256 && (editing === confirmed.option.id ? <WorkshopCapacityEditor key={`${state.proposal_sha256}:${confirmed.option.id}`} option={confirmed.option} proposal={state.proposal_sha256} sessionId={session.id} limit={session.input.words_per_chapter} unit={unit} disabled={locked} onClose={() => changeEditing(null)} onSubmit={request => { changeEditing(null); onRepartition(request); }}/>
        : <button type="button" className="btn-secondary mt-4" disabled={locked} onClick={() => changeEditing(confirmed.option.id)}>编辑篇幅方案</button>)}</>
      : replanRequest ? <><p className="mt-3 text-[16px] leading-7">{session.status === 'running' ? '正在' : '等待'}重新安排为 {replanRequest.request.chapters} 章 · 约 {replanRequest.total_words.toLocaleString()} {unit}</p>
        <p className="mt-2 text-[14px] leading-6 text-ink-muted">每章规划不超过 {replanRequest.chapter_words_limit.toLocaleString()} {unit}。保留已确认故事；新分章保存后仍由你确认。</p>
        {['error', 'paused'].includes(session.status) && <button type="button" className="btn-secondary mt-4" disabled={locked} onClick={() => onRepartition?.(replanRequest.request)}>重新安排分章</button>}</>
      : needsAdjustment ? <><div role="status" className="mt-4 rounded-xl bg-amber-50 p-4 text-[15px] leading-7 text-amber-900"><h4 className="font-semibold">为关键场面保留必要篇幅</h4><p>{state.adjustment!.reason}</p><p>你希望约 {state.adjustment!.requested_total_words.toLocaleString()} {unit}；模型建议至少 {state.adjustment!.minimum_total_words.toLocaleString()} {unit}。原方案与故事内容仍然保留。</p></div>
        {plan.options.map(option => <div key={`${state.proposal_sha256}:${option.id}`} className="mt-4"><h4 className="text-[16px] font-medium">调整「{option.title}」</h4>{onRepartition && (editing === option.id ? <WorkshopCapacityEditor option={option} proposal={state.proposal_sha256!} sessionId={session.id} limit={state.adjustment!.chapter_words_limit} unit={unit} disabled={locked} onClose={() => changeEditing(null)} onSubmit={request => { changeEditing(null); onRepartition(request); }}/>
          : <button type="button" className="btn-secondary mt-3" disabled={locked} onClick={() => changeEditing(option.id)}>编辑篇幅方案</button>)}</div>)}</>
      : proposed ? <><p className="mt-3 text-[16px] leading-7">{plan.rationale}</p><p className="mt-2 text-[14px] text-ink-muted">正文语言：{plan.language} · 按{unit}估算</p>
        {state.length_request?.matches_requested_total === false && <p role="status" className="mt-3 rounded-xl bg-amber-50 px-4 py-3 text-[14px] leading-7 text-amber-900">{state.notice || `你希望约 ${state.length_request.target_total_words.toLocaleString()} ${unit}。下面是不同篇幅的建议，尚未替你改变目标；可以编辑后重新安排，或明确采用其中一个方案。`}</p>}
        {session.input.words_per_chapter && <p className="mt-3 rounded-xl bg-violet-soft/60 px-4 py-3 text-[15px] text-ink-text">你的每章设置：<strong>{session.input.words_per_chapter.toLocaleString()} {unit}</strong> · 用作分章规划上限，扩展优先增加章数。</p>}
        <div className={`mt-5 grid gap-4 ${plan.options.length > 1 ? 'xl:grid-cols-2' : ''}`}>
          {plan.options.map(option => <CapacityCard key={`${state.proposal_sha256}:${option.id}`} option={option} limit={session.input.words_per_chapter} unit={unit} locked={locked || Boolean(editing && editing !== option.id)} sessionId={session.id} editing={editing === option.id} onEdit={value => changeEditing(value ? option.id : null)}
            proposal={state.proposal_sha256!} onAdopt={onAdopt} onRepartition={onRepartition}>{allocation(option)}</CapacityCard>)}
        </div>
        <details className="mt-4 text-[14px]"><summary className="cursor-pointer text-ink-muted">为什么需要这些场景与篇幅</summary><div className="mt-3 space-y-3">{plan.scenes.map(scene => <div key={scene.id} className="rounded-xl bg-white p-4"><p className="font-medium">{scene.title} · {scene.presentation === 'on_page' ? '完整呈现场景' : '过渡概述'} · {scene.minimum}–{scene.maximum} {unit}</p><p className="mt-2 leading-6">{scene.reader_value}</p><p className="mt-1 leading-6 text-ink-muted">{scene.reason}</p><blockquote className="mt-2 border-l-2 border-violet/25 pl-3 text-ink-muted">{scene.source_quote}</blockquote></div>)}</div></details>
      </> : <><p className="mt-3 text-[15px] leading-7 text-ink-muted">{state?.notice ?? '先确定主要故事走向，再由模型按关键场景、人物变化和高潮余波提出篇幅方案。初始章数是偏好，避免为了凑章数把重要情节一笔带过。'}</p>
        <button type="button" className="btn-secondary mt-4" disabled={disabled || pending || !workshopNextStep(session).can_plan_capacity} onClick={onPlan}>按当前故事规划篇幅</button></>}
    {!confirmed && <p className="mt-4 text-[13px] leading-6 text-ink-muted">{hint} 数字是编辑估算，不代表阅读质量已通过评议。</p>}
  </section>;
}
