import type { WorkshopSession } from '../api/workshop';

const categories: Record<string, string> = { author_statement: '作者要求', author_requirement: '作者要求', author: '作者要求', direction: '核心走向', core: '核心走向', relationship: '人物关系', ending: '结局方向', emotion: '情绪回报', emotional: '情绪回报', scene: '重要场面', payoff: '悬念与兑现', reader_promise: '读者期待', causal_arc: '故事因果', highlight: '故事看点', promise: '悬念与兑现', adopted_proposal: '已采用方案', author_action: '作者决定', source_mode: '创作方式' };

export default function WorkshopCreativeBasis({ session }: { session: WorkshopSession }) {
  const basis = session.development_view;
  if (!basis) return null;
  const phase = { exploring: '正在探索故事方向', developing: '沿当前方向深化', reviewing: '正在核对本轮扩展', needs_revision: '本轮扩展待协调', needs_author: '有一项核心方向需要你决定' }[basis.phase];
  return <section aria-label="当前创作依据" className="my-4 rounded-2xl border border-paper-line bg-white/85 px-5 py-4 sm:px-6">
    <p className="text-[12px] text-violet">{phase}</p><h3 className="mt-1 text-[18px] font-semibold">{basis.title || '从你的构想开始'}</h3>
    {basis.notice && <p className="mt-2 text-[14px] leading-7 text-ink-muted">{basis.notice}</p>}
    {!!basis.issues?.length && <details className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-[14px]" open><summary className="cursor-pointer font-medium text-amber-900">本轮需要协调的内容</summary><div className="mt-3 space-y-4">{basis.issues.map((item, i) => <article key={i}><p className="leading-7">{item.reason}</p>{item.quote && <blockquote className="mt-2 border-l-2 border-amber-300 pl-3 leading-7 text-ink-muted">{item.quote}</blockquote>}{item.repair && <p className="mt-2 leading-7">建议处理：{item.repair}</p>}</article>)}</div><p className="mt-3 text-[12px] leading-6 text-ink-muted">候选内容与历史方案均已保留。可以在下方补充你的决定，无需填写技术记录。</p></details>}
    <details className="mt-3 text-[14px]"><summary className="cursor-pointer text-violet">{basis.phase === 'exploring' ? '查看已明确的要求' : '查看当前核心与情绪看点'}</summary>
      <p className="mt-3 text-[13px] leading-6 text-ink-muted">已采用的方向会继续沿用；具体场景可深化，新的核心走向会单独提出。以下为创作设计，实际情绪效果仍需在正文中落实。</p>
      {basis.commitments.length ? <div className="mt-3 space-y-3">{basis.commitments.map(item => <article key={item.id} className="border-l-2 border-violet/25 pl-3"><p className="text-[12px] text-ink-muted">{categories[item.category] || '创作依据'}</p><p className="mt-1 whitespace-pre-wrap leading-7">{item.text}</p></article>)}</div> : <p className="mt-3 leading-7 text-ink-muted">尚未采用整书方向，未定部分可以自由探索。模型提案不会自动成为你的决定。</p>}
      {basis.emotion_designs.length > 0 && <div className="mt-5 space-y-4"><h4 className="text-[16px] font-semibold">情绪与看点 · 等待故事逐步兑现</h4>{basis.emotion_designs.map((item, i) => <article key={i} className="rounded-xl bg-violet-soft/35 p-4"><h5 className="font-medium">{item.expectation}</h5><p className="mt-2 leading-7">{item.scene}</p><dl className="mt-3 grid gap-3 sm:grid-cols-2">{([['choice', '人物选择'], ['cost', '实际代价'], ['change', '关系与局势变化'], ['payoff', '期待的兑现']] as const).map(([key, label]) => <div key={key}><dt className="text-[12px] text-ink-muted">{label}</dt><dd className="mt-1 leading-7">{item[key]}</dd></div>)}</dl></article>)}</div>}
    </details>
  </section>;
}
