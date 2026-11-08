import type { ReactNode } from 'react';
import type { StoryAction, StoryArea, WorkshopSession } from '../api/workshop';
import WorkshopSubmittedMessages, { type SubmittedTurn } from './WorkshopSubmittedMessages';

export type StoryTab = 'decision' | 'story' | 'assumptions' | 'history';
const AREAS: Record<StoryArea, string> = { direction: '核心方向', opening: '开篇与前段', middle: '中段推进', climax: '高潮选择', ending: '结局与回报' };
const STATES = { missing: '待设计', proposed: '已有提案', settled: '有作者依据', conflict: '待协调' };
const KINDS = { major_premise: '重大前提', causal_gap: '因果缺口', detail: '创作细节', external_fact: '现实资料' };
const ASSUMPTION_STATES: Record<string, string> = { reframed: '已重新拟定方案', pending: '待处理', provisional: '模型暂定', unverified: '尚未核实', adopted_premise: '随所选方案采用', not_adopted: '未采用', delegated: '本题已委托', superseded_by_custom: '以自定义方向为准', model_resolved: '模型已提出解释' };

export default function WorkshopStoryWorkspace({ session, tab, onTab, mode, selected, disabled, onSelect, onMode, onReopen, latestReply, submitted }: {
  session: WorkshopSession; tab: StoryTab; onTab: (tab: StoryTab) => void;
  mode: string; selected: string[]; disabled: boolean;
  onSelect: (group: string, id: string) => void; onMode: (mode: StoryAction['mode']) => void;
  onReopen: (id: string) => void; latestReply: ReactNode;
  submitted?: SubmittedTurn;
}) {
  const state = session.creative_design?.story_state;
  if (!state) return null;
  const { map, pending } = state;
  const validation = state.validation;
  const notice = state.notice && !validation
    ? '讨论已保存，故事地图未更新，当前展示上次地图。本次历史记录没有保存具体校验原因；可继续本轮讨论，不会自动重试。'
    : state.notice;
  const guidance = session.creative_design?.rounds.at(-1)?.guidance;
  const fresh = map?.reviewed_revision === session.revision && session.status !== 'running';
  const ready = !pending && guidance?.reviewed_revision === session.revision && guidance.next_action === 'generate';
  const decisions = state.actions.filter(a => ['select', 'custom', 'delegate'].includes(a.mode));
  return <section className="mb-5" aria-label="故事共创工作台">
    <div className="mb-4 rounded-2xl border border-paper-line bg-white/85 px-5 py-4 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><p className="text-[14px] text-violet">全书设计 · {map ? AREAS[map.focus] : '从核心方向开始'}</p>
          <h3 className="mt-1 text-[22px] font-semibold leading-snug">{ready ? '故事主线已收拢，可以展开完整骨架' : pending ? pending.topic : '让每一次决定，都推进整个故事'}</h3></div>
        <span className="rounded-full bg-violet-soft px-3 py-1.5 text-[14px] text-violet">{session.status === 'running' ? '正在推演 · 保留已保存进度' : pending ? '等你决定本题' : '模型负责细节，你决定走向'}</span>
      </div>
      <div className="mt-4 flex gap-2 overflow-x-auto pb-1 sm:grid sm:grid-cols-5" aria-label="全书设计阶段">{(Object.keys(AREAS) as StoryArea[]).map(area => {
        const node = map?.nodes.find(n => n.area === area);
        return <button type="button" key={area} onClick={() => onTab('story')} className={`min-w-[112px] shrink-0 rounded-xl border px-3 py-2 text-left ${map?.focus === area ? 'border-violet/30 bg-violet-soft' : 'border-paper-line bg-white/60'}`}>
          <span className="block text-[14px] font-medium">{AREAS[area]}</span><span className="text-[12px] text-ink-muted">{node ? STATES[node.status] : '尚未整理'}</span>
        </button>;
      })}</div>
      <p className="mt-3 text-[13px] text-ink-muted">{map ? `故事地图基于第 ${map.source_revision} 版输入 · ${fresh ? '已结合本轮整理' : '等待新意见整合，当前展示上次地图'}` : '首次回复后整理全书走线。设计进度不代表正文质量评分。'}</p>
    </div>
    <div role="tablist" aria-label="共创内容" className="sticky top-0 z-10 mb-5 flex gap-1 overflow-x-auto rounded-2xl border border-paper-line bg-[#f4f5ff] p-1.5">
      {([['decision', '本轮决定'], ['story', '故事全貌'], ['assumptions', `待定事项 · ${state.assumptions.filter(a => ['pending', 'unverified'].includes(a.status)).length}`], ['history', '讨论记录']] as const).map(([key, label]) =>
        <button key={key} id={`story-tab-${key}`} role="tab" aria-selected={tab === key} aria-controls={`story-panel-${key}`} type="button" tabIndex={tab === key ? 0 : -1} onKeyDown={event => {
          const tabs: StoryTab[] = ['decision', 'story', 'assumptions', 'history'];
          const index = tabs.indexOf(key);
          const next = event.key === 'ArrowRight' ? tabs[(index + 1) % 4] : event.key === 'ArrowLeft' ? tabs[(index + 3) % 4] : event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs[3] : null;
          if (next) { event.preventDefault(); onTab(next); document.getElementById(`story-tab-${next}`)?.focus(); }
        }} onClick={() => onTab(key)} className={`whitespace-nowrap rounded-xl px-4 py-2.5 text-[15px] font-medium transition-colors ${tab === key ? 'bg-ink text-white shadow-sm' : 'text-ink-muted hover:bg-white'}`}>{label}</button>)}
    </div>
    {notice && <div role="status" className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-[14px] leading-relaxed text-amber-900">
      <p>{notice}</p>
      {!!validation?.issues.length && <details className="mt-2">
        <summary className="cursor-pointer font-medium">查看处理详情 · {validation.issues.length} 项</summary>
        <p className="mt-2">本次处理基于第 {validation.source_revision} 版输入。有效方案仍可继续选择。</p>
        <ul className="mt-2 space-y-2">{validation.issues.map((issue, index) => <li key={`${issue.path}:${index}`}>
          {issue.message}<span className="ml-2 text-[12px] text-ink-muted">{issue.path}</span>
        </li>)}</ul>
      </details>}
    </div>}
    {tab === 'decision' && <div role="tabpanel" id="story-panel-decision" aria-labelledby="story-tab-decision">
      <WorkshopSubmittedMessages session={session} submitted={submitted} onHistory={() => onTab('history')} />
      {pending ? <>
        <div className="mb-5"><h4 className="text-[24px] font-semibold">本轮方案与取舍 <span className="font-normal text-ink-muted">· {pending.option_ids.length} 个方案</span></h4><p className="mt-2 text-[16px] leading-relaxed text-ink-muted">{pending.why}</p></div>
        <fieldset disabled={disabled}><legend className="sr-only">本轮故事方向（单选）</legend>
          <div className="grid gap-4 lg:grid-cols-2">{pending.option_ids.map((id, i) => {
            const proposal = pending.proposals.find(p => p.id === id);
            if (!proposal) return null;
            const checked = mode === 'select' && selected.includes(id);
            return <label key={id} className={`flex cursor-pointer flex-col rounded-2xl border p-5 transition-colors focus-within:ring-2 focus-within:ring-violet/40 ${checked ? 'border-violet bg-violet-soft/60' : 'border-paper-line bg-white/90 hover:border-violet/40'} ${disabled ? 'opacity-65' : ''}`}>
              <div className="flex items-center gap-3"><input type="radio" name={`${session.id}-${pending.id}`} checked={checked} onChange={() => onSelect(pending.id, id)} className="h-5 w-5 accent-violet"/><span className="text-[14px] font-semibold text-violet">方案 {String.fromCharCode(65 + i)}</span>{pending.recommended_id === id && <span className="ml-auto rounded-full bg-white px-3 py-1 text-[13px] text-violet">推荐</span>}</div>
              <span className="mt-4 text-[18px] font-semibold leading-relaxed">{proposal.title}</span>
              <span className="mt-3 whitespace-pre-wrap text-[16px] leading-relaxed">{proposal.story}</span>
              <span className="mt-5 border-t border-paper-line pt-4 text-[14px] leading-relaxed text-ink-muted">取舍与后续影响：{proposal.tradeoff}</span>
            </label>;
          })}</div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">{([['custom', '我来定方向', '在下方写出自己的大方向或组合方式。'], ['delegate', '委托模型决定本题', '仅在本题方案范围内选择，并说明理由。']] as const).map(([value, label, hint]) => <label key={value} className={`cursor-pointer rounded-xl border p-4 ${mode === value ? 'border-violet bg-violet-soft/50' : 'border-paper-line bg-white/70'}`}>
            <span className="flex items-center gap-3 text-[16px] font-medium"><input type="radio" name={`${session.id}-${pending.id}`} checked={mode === value} onChange={() => onMode(value)} className="h-5 w-5 accent-violet"/>{label}</span><span className="mt-2 block text-[14px] text-ink-muted">{hint}</span>
          </label>)}</div>
        </fieldset>
        {pending.recommendation_reason && <p className="mt-4 rounded-xl bg-violet-soft/40 p-4 text-[15px] leading-relaxed">推荐理由：{pending.recommendation_reason}</p>}
        <div className="mt-3 flex flex-wrap gap-2"><button type="button" aria-pressed={mode === 'question'} className="btn-ghost !text-[14px]" disabled={disabled} onClick={() => onMode('question')}>先问清楚，不作决定</button><button type="button" aria-pressed={mode === 'reframe'} className="btn-ghost !text-[14px]" disabled={disabled} onClick={() => onMode('reframe')}>都不合适，换一组</button></div>
        <p className="mt-2 text-[14px] text-ink-muted">{mode === 'question' ? '请在下方填写问题。此次只解释，不采用方案。' : mode === 'reframe' ? '可补充不满意的原因。提交后仅调整本题方案，未决状态会保留。' : '选择后可补充想法，再点击「采用并继续推演」。未选择不会默认采用推荐项。'}</p>
      </> : <div className="rounded-2xl border border-paper-line bg-white/85 p-6"><h4 className="text-[20px] font-semibold">{session.status === 'running' ? '正在推演你刚才的决定与补充' : ready ? '可以生成完整骨架了' : '当前没有需要你决定的重大分歧'}</h4><p className="mt-3 text-[16px] leading-relaxed text-ink-muted">{session.status === 'running' ? '本轮回复尚未完成。你的输入已保留在上方，完整记录可随时查看。' : guidance?.reason ?? '可以补充故事方向，也可以查看完整讨论。模型会完成专业细节，必要时再提出影响全书的选择。'}</p>{ready && <p className="mt-3 text-[14px] text-violet">下方生成按钮将展开章节、人物、伏笔与回报；不会直接启动正文。</p>}</div>}
      <details className="mt-5 rounded-xl border border-paper-line bg-white/65 p-4" open={!pending && session.status !== 'running'}><summary className="cursor-pointer text-[15px] font-medium">{session.status === 'running' ? '上一轮设计说明' : '本轮设计说明'}</summary><div className="mt-4">{latestReply}</div></details>
    </div>}
    {tab === 'story' && <div role="tabpanel" id="story-panel-story" aria-labelledby="story-tab-story" className="space-y-5">
      <section className="rounded-2xl border border-paper-line bg-white/90 p-6"><h4 className="text-[22px] font-semibold">我们的故事，现在是这样</h4><p className="mt-2 text-[14px] text-ink-muted">模型对当前走线的整理。提案与作者决定仍有区别，完整讨论保留在记录中。</p><p className="mt-4 whitespace-pre-wrap text-[16px] leading-[1.9]">{map?.synopsis ?? '尚未收到完整故事地图，原有讨论仍保留。'}</p>{!!map?.changes.length && <div className="mt-5 rounded-xl bg-violet-soft/40 p-4"><p className="text-[14px] font-medium text-violet">本轮变化</p><ul className="mt-2 list-disc space-y-1 pl-5 text-[15px] leading-relaxed">{map.changes.map((change, i) => <li key={i}>{change}</li>)}</ul></div>}</section>
      <div className="grid gap-4 lg:grid-cols-2">{map?.nodes.map(node => <section key={node.area} className="rounded-2xl border border-paper-line bg-white/80 p-5"><div className="flex items-center justify-between gap-3"><h4 className="text-[18px] font-semibold">{AREAS[node.area]}</h4><span className="text-[13px] text-violet">{STATES[node.status]}</span></div><p className="mt-3 whitespace-pre-wrap text-[16px] leading-relaxed">{node.summary}</p>{!!node.evidence.length && <details className="mt-3 text-[14px] text-ink-muted"><summary className="cursor-pointer">查看作者依据</summary>{node.evidence.map((e, i) => <blockquote key={i} className="mt-2 border-l-2 border-violet/30 pl-3">{e.quote}</blockquote>)}</details>}</section>)}</div>
      {map?.opening && <section className="rounded-2xl border border-violet/25 bg-white/90 p-6"><h4 className="text-[22px] font-semibold">开篇：读者为什么在意</h4><p className="mt-2 text-[14px] text-ink-muted">这是阅读体验的设计意图；实际效果需在正文中检验。</p><dl className="mt-5 grid gap-5 sm:grid-cols-2">{([['situation', '进入什么情境'], ['anchor', '在意谁、在意什么'], ['reader_experience', '希望唤起的读者感受'], ['genre_cause', '题材如何推动冲突'], ['first_value', '开篇就兑现的价值'], ['next_expectation', '为什么继续往下读']] as const).map(([key, label]) => <div key={key}><dt className="text-[14px] text-violet">{label}</dt><dd className="mt-2 text-[16px] leading-relaxed">{map.opening![key]}</dd></div>)}</dl>{map.opening.preview && <details className="mt-5 border-t border-paper-line pt-4"><summary className="cursor-pointer text-[15px] font-medium">查看试演片段 · 非正式正文</summary><p className="mt-3 whitespace-pre-wrap text-[16px] leading-[1.9]">{map.opening.preview}</p></details>}</section>}
      {!!decisions.length && <details className="rounded-2xl border border-paper-line bg-white/75 p-5"><summary className="cursor-pointer text-[16px] font-semibold">已作出的重大决定 · {decisions.length} 次</summary>{decisions.map((action, i) => <div key={i} className="mt-4 border-t border-paper-line pt-4"><p className="text-[16px] font-medium">{action.question.topic}</p><p className="mt-2 text-[14px] text-ink-muted">第 {action.revision} 版 · {action.mode === 'delegate' ? '委托模型在本题范围内决定' : action.mode === 'custom' ? '作者自定义方向' : action.question.proposals.filter(p => action.proposal_ids.includes(p.id)).map(p => p.title).join('、')}{action.note && ` · ${action.note}`}</p><button type="button" className="btn-ghost mt-2 !px-0 !text-[14px] !text-violet" disabled={disabled || !!pending} onClick={() => onReopen(action.question.id)}>重新讨论这个决定</button></div>)}</details>}
      <details className="rounded-xl border border-paper-line p-4 text-[14px] text-ink-muted"><summary className="cursor-pointer">历次故事概要</summary>{session.creative_design?.rounds.filter(r => r.story).map(r => <details key={r.source_revision} className="mt-3"><summary className="cursor-pointer">第 {r.source_revision} 版输入的地图</summary><p className="mt-2 whitespace-pre-wrap leading-relaxed">{r.story?.synopsis}</p></details>)}</details>
    </div>}
    {tab === 'assumptions' && <div role="tabpanel" id="story-panel-assumptions" aria-labelledby="story-tab-assumptions"><h4 className="text-[22px] font-semibold">哪些已决定，哪些还只是设想</h4><p className="mb-5 mt-2 text-[15px] leading-relaxed text-ink-muted">你决定重大前提；模型负责解释因果与创作细节。现实资料是否可信，需要独立核实，选择方案不能替代验证。</p>{state.assumptions.length ? <div className="space-y-3">{state.assumptions.map(item => <article key={item.id} className="rounded-2xl border border-paper-line bg-white/90 p-5"><div className="flex flex-wrap items-center justify-between gap-2 text-[14px]"><span className="font-medium text-violet">{KINDS[item.kind]}</span><span className="text-ink-muted">{ASSUMPTION_STATES[item.status] ?? '待核查'} · 第 {item.source_revision} 版输入</span></div><p className="mt-3 text-[16px] leading-relaxed">{item.text}</p>{item.resolution && <p className="mt-3 text-[14px] text-ink-muted">模型解释：{item.resolution}</p>}</article>)}</div> : <p className="rounded-xl bg-white/75 p-5 text-[15px] text-ink-muted">尚无结构化待定事项；这不代表现实资料已经核实。讨论原文仍可查阅。</p>}</div>}
  </section>;
}
