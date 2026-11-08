import type { WorkshopSession } from '../api/workshop';
import { currentGuidance } from '../lib/workshopChoiceRequest';
import WorkshopProposalImpact from './WorkshopProposalImpact';

const AREAS = { premise: '核心故事与题材', characters: '人物与关键关系', plot: '转折与高潮', ending: '结局与阅读体验' };
const STATES = { missing: '待设计', proposed: '已有提案', settled: '已有作者依据', conflict: '需协调' };

export default function WorkshopGuidancePanel({ session, selected, disabled, onSelect, onUpgrade, onGenerate, onRefine, onAlternatives }: {
  session: WorkshopSession; selected: string[]; disabled: boolean;
  onSelect: (groupId: string, proposalId: string) => void;
  onUpgrade: () => void; onGenerate: () => void; onRefine: () => void; onAlternatives: () => void;
}) {
  const { round, advice, fresh } = currentGuidance(session);
  const enabled = session.creative_design?.policy.guided_choices_version === 'guided-choices.v1';
  if (!enabled) return <section className="mb-5 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-violet/20 bg-violet-soft/50 p-5" aria-label="选项式共创">
    <div><p className="text-[15px] font-semibold">让故事设计更好选择</p><p className="mt-1 text-[13px] text-ink-muted">启用选项式共创，下一轮会提供关键选择和全书设计进度。保留已有讨论与设定。</p></div>
    <button type="button" className="btn-secondary !text-violet" disabled={disabled} onClick={onUpgrade}>启用选项式共创</button>
  </section>;
  if (!round) return null;
  const generating = advice?.status === 'assessed' && fresh && advice.next_action === 'generate';
  return <section className="mb-6 overflow-hidden rounded-2xl border border-violet/20 bg-white/90 shadow-[0_8px_32px_rgba(30,43,80,0.04)]" aria-label="本轮选择与设计进度">
    <div className="border-b border-paper-line bg-violet-soft/35 px-5 py-4 sm:px-6">
      <p className="text-[11px] font-semibold tracking-[0.12em] text-violet">一起把故事想完整</p>
      <h3 className="mt-1 text-[18px] font-semibold text-ink">{generating ? '故事已具备生成骨架的条件' : advice?.groups?.length ? '这一次，决定故事往哪里走' : '本书的设计进展'}</h3>
      <p className="mt-2 text-[14px] leading-relaxed text-ink-muted">{advice?.status === 'assessed' ? advice.reason : advice?.notice ?? '本轮尚未提供设计进度；完整回复已保留，可继续讨论或主动生成骨架。'}</p>
      {!fresh && advice?.status === 'assessed' && <p role="status" className="mt-2 text-[13px] text-violet">已有新意见，以下是上轮判断，下一轮将更新设计进度。</p>}
    </div>
    {!!advice?.groups?.length && <div className="space-y-6 px-5 py-5 sm:px-6">{advice.groups.map(group => <fieldset key={group.id} disabled={disabled}>
      <legend className="text-[16px] font-semibold">{group.topic}</legend>
      <p className="mb-4 mt-1 text-[13px] leading-relaxed text-ink-muted">{group.why}</p>
      {group.option_ids.some(id => selected.includes(id)) && <button type="button" className="btn-ghost mb-2 !px-0 !text-[12px]" onClick={() => onSelect(group.id, '')}>清除本次选择</button>}
      <div className={`grid gap-3 ${group.option_ids.length === 3 ? 'lg:grid-cols-3' : 'lg:grid-cols-2'}`}>{group.option_ids.map((id, i) => {
        const proposal = round.proposals.find(p => p.id === id);
        if (!proposal) return null;
        const checked = selected.includes(id);
        return <label key={id} className={`relative flex cursor-pointer flex-col rounded-xl border p-4 transition-colors focus-within:ring-2 focus-within:ring-violet/40 ${checked ? 'border-violet/60 bg-violet-soft/65' : 'border-paper-line bg-white hover:border-violet/35'} ${disabled ? 'cursor-default opacity-65' : ''}`}>
          <div className="flex items-center gap-2">
            <input type="radio" name={`${session.id}-${group.id}`} value={id} checked={checked} onChange={() => onSelect(group.id, id)} className="h-4 w-4 shrink-0 accent-violet" />
            <span className="text-[12px] font-semibold text-violet">方案 {String.fromCharCode(65 + i)}</span>
            {group.recommended_id === id && <span className="ml-auto rounded-full bg-violet-soft px-2 py-1 text-[11px] text-violet">推荐</span>}
          </div>
          <span className="mt-3 text-[16px] font-semibold leading-snug">{proposal.title}</span>
          <span className="mt-2 whitespace-pre-wrap text-[14px] leading-relaxed">{proposal.story}</span>
          <WorkshopProposalImpact proposal={proposal} />
          <span className="mt-4 border-t border-paper-line pt-3 text-[13px] leading-relaxed text-ink-muted">取舍与影响：{proposal.tradeoff}</span>
        </label>;
      })}</div>
    </fieldset>)}
      {round.recommendation_reason && <p className="text-[13px] leading-relaxed text-ink-muted">推荐理由：{round.recommendation_reason}</p>}
      <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-[13px] text-ink-muted">选择后可在下方补充想法，也可以直接提出另一条路线。</p>
        <button type="button" className="btn-ghost !text-violet" disabled={disabled} onClick={onAlternatives}>都不合适，换一组</button></div>
    </div>}
    {advice?.status === 'assessed' && <details className="border-t border-paper-line px-5 py-4 sm:px-6">
      <summary className="cursor-pointer text-[14px] font-semibold">全书设计进度 · {advice.coverage?.filter(c => c.status === 'settled').length ?? 0}/4 项已有作者依据</summary>
      <p className="mt-2 text-[12px] text-ink-muted">模型的设计建议，可查看依据；不代表正文质量已经通过验证。</p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">{advice.coverage?.map(item => <div key={item.area} className="rounded-xl border border-paper-line p-4">
        <div className="flex items-center justify-between gap-2"><span className="text-[14px] font-medium">{AREAS[item.area]}</span><span className="text-[12px] text-violet">{STATES[item.status]}</span></div>
        <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">{item.summary}</p>
        {!!item.evidence.length && <details className="mt-2 text-[12px] text-ink-muted"><summary className="cursor-pointer">查看作者依据</summary>{item.evidence.map((e, i) => <blockquote key={i} className="mt-2 border-l-2 border-violet/25 pl-3">{e.quote}</blockquote>)}</details>}
      </div>)}</div>
      {!!advice.missing?.length && <p className="mt-3 text-[13px] text-ink-muted">还需确定：{advice.missing.join('；')}</p>}
    </details>}
    {generating && <div className="flex flex-wrap items-center gap-3 border-t border-paper-line bg-violet-soft/25 px-5 py-4 sm:px-6">
      <button type="button" className="btn-primary" disabled={disabled} onClick={onGenerate}>{session.prompt_md ? '更新完整骨架（推荐）' : '生成完整骨架（推荐）'}</button>
      <button type="button" className="btn-secondary" disabled={disabled} onClick={onRefine}>继续打磨</button>
      <p className="w-full text-[12px] text-ink-muted">将展开章节、伏笔回收和人物资料；生成后可以下载，不会启动正文。</p>
    </div>}
  </section>;
}
