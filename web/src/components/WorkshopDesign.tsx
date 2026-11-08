import { useState } from "react";
import type { WorkshopDecisionStatus, WorkshopDesign, WorkshopDesignRound, WorkshopProposal } from "../api/workshop";
import { genreLabel } from "../lib/genres";
import WorkshopProposalImpact from "./WorkshopProposalImpact";

const DECISION_LABELS: Record<WorkshopDecisionStatus, string> = {
  accepted: "已采用", rejected: "不采用", revised: "已提出修改", deferred: "暂缓",
};
const ROLE_LABELS = { world: "世界机制", conflict: "冲突", relationship: "关系", tone: "基调", audience: "读者期待" };
type Decide = (id: string, status: WorkshopDecisionStatus, note?: string) => Promise<boolean>;

export function WorkshopDesignContext({ design }: { design: WorkshopDesign }) {
  const current = new Map(design.decisions.map((decision) => [decision.proposal_id, decision]));
  const proposals = new Map(design.rounds.flatMap((round) => round.proposals.map((proposal) => [proposal.id, proposal] as const)));
  return <details className="my-3 rounded-xl border border-paper-line bg-white/70 px-4 py-3 text-[12px] text-ink-muted">
    <summary className="cursor-pointer">题材与创作决定 · {current.size} 项已记录</summary>
    <p className="mt-3 leading-relaxed">题材资料用于提出选择，不会自动成为本书设定。方案卡选择与原始对话一起保留；后续明确修改以你的最新意见为准。</p>
    <div className="mt-3 flex flex-wrap gap-2">{design.policy.guidance.selections.map((item, index) => <span key={`${index}:${item.selection}`} className="rounded-full bg-violet-soft px-3 py-1 text-violet">
      {genreLabel(item.selection)} · {item.coverage === "specialized_guidance" ? "专项指导" : item.coverage === 'user_guidance' ? '所选资料' : "通用指导"}
    </span>)}</div>
    <p className="mt-2 text-[11px]">资料版本 {design.policy.guidance.version} · 设计指导尚未经过读者效果验证</p>
    {current.size > 0 ? <ul className="mt-3 space-y-2">{[...current.values()].map((decision) => <li key={decision.proposal_id} className="rounded-lg bg-white p-3">
      <span className="font-medium text-ink">{proposals.get(decision.proposal_id)?.title ?? decision.proposal_id}</span>
      <span className="ml-2 text-violet">{DECISION_LABELS[decision.status]}</span>
      {decision.note && <p className="mt-1 whitespace-pre-wrap">{decision.note}</p>}
    </li>)}</ul> : <p className="mt-3">还没有方案卡选择。自然语言意见保留在讨论中，无需额外点击确认。</p>}
  </details>;
}

export function WorkshopDesignRoundView({ round, design, disabled, onDecide }: {
  round: WorkshopDesignRound; design: WorkshopDesign; disabled: boolean; onDecide: Decide;
}) {
  if (round.status === "unavailable") return <p className="mt-4 text-[12px] text-ink-muted">{round.notice}</p>;
  return <div className="mt-5 border-t border-paper-line pt-4">
    {round.next_focus && <div className="rounded-xl bg-violet-soft/60 px-4 py-3">
      <p className="text-[13px] font-semibold text-ink">下一步建议 · {round.next_focus.topic}</p>
      <p className="mt-1 text-[12px] leading-relaxed text-ink-muted">{round.next_focus.why}</p>
    </div>}
    <details className="mt-3 text-[13px]">
      <summary className="cursor-pointer font-medium text-violet">本轮方案与取舍 · {round.proposals.length} 个方案</summary>
      <p className="my-3 text-[12px] text-ink-muted">可直接用文字讨论，也可保存下面的选择。保存选择不会调用模型；点击“发送并继续推导”后才开始下一轮。</p>
      {round.recommendation_reason && <p className="mb-3 leading-relaxed">推荐理由：{round.recommendation_reason}</p>}
      <div className="space-y-3">{round.proposals.map((proposal) => <ProposalCard key={proposal.id} proposal={proposal}
        decision={design.decisions.findLast((item) => item.proposal_id === proposal.id)} disabled={disabled} onDecide={onDecide} />)}</div>
      {Boolean(round.assumptions?.length) && <NoteList title="待定假设" items={round.assumptions!} />}
      {Boolean(round.conflicts?.length) && <NoteList title="尚需取舍" items={round.conflicts!} />}
    </details>
  </div>;
}

function NoteList({ title, items }: { title: string; items: string[] }) {
  return <div className="mt-4"><p className="font-medium text-ink">{title}</p>
    <ul className="mt-2 list-disc space-y-1 pl-5 text-ink-muted">{items.map((item, index) => <li key={index}>{item}</li>)}</ul></div>;
}

function ProposalCard({ proposal, decision, disabled, onDecide }: {
  proposal: WorkshopProposal; decision?: WorkshopDesign["decisions"][number]; disabled: boolean; onDecide: Decide;
}) {
  const [editing, setEditing] = useState(false);
  const [note, setNote] = useState("");
  return <section className="rounded-xl border border-paper-line bg-white/80 p-4" aria-label={proposal.title}>
    <div className="flex flex-wrap items-center gap-2"><h4 className="font-semibold text-ink">{proposal.title}</h4>
      {proposal.recommended && <span className="rounded-full bg-violet-soft px-2 py-0.5 text-[11px] text-violet">建议方向</span>}
      {decision && <span role="status" className="text-[12px] text-violet">{DECISION_LABELS[decision.status]}</span>}
    </div>
    <p className="mt-2 whitespace-pre-wrap leading-relaxed">{proposal.story}</p>
    <WorkshopProposalImpact proposal={proposal} />
    <p className="mt-2 text-[12px] leading-relaxed text-ink-muted">取舍：{proposal.tradeoff}</p>
    {decision?.note && <p className="mt-2 whitespace-pre-wrap text-[12px] text-violet">你的意见：{decision.note}</p>}
    {(proposal.genre_roles.length > 0 || proposal.mechanisms.length > 0) && <details className="mt-3 text-[12px]">
      <summary className="cursor-pointer text-ink-muted">题材如何推动这个故事</summary>
      {proposal.genre_roles.map((item, i) => <p key={i} className="mt-2 leading-relaxed"><strong>{genreLabel(item.selection)} · {ROLE_LABELS[item.role]}</strong>：{item.contribution}</p>)}
      {proposal.mechanisms.map((mechanism, i) => <div key={i} className="mt-3 rounded-lg bg-paper/60 p-3">
        <p className="font-semibold">{mechanism.name}</p>
        <dl className="mt-2 space-y-2">{([
          ["规则", mechanism.rule], ["限制与例外", mechanism.boundary], ["代价", mechanism.cost],
          ["人物选择", mechanism.choice], ["后续影响", mechanism.consequence], ["回报", mechanism.payoff],
          ["移除这个机制会怎样", mechanism.removal_effect],
        ] as const).map(([label, value]) => <div key={label}><dt className="font-medium text-ink">{label}</dt><dd className="mt-0.5 text-ink-muted">{value}</dd></div>)}</dl>
      </div>)}
    </details>}
    <div className="mt-3 flex flex-wrap gap-2">
      {([ ["accepted", "采用这条"], ["rejected", "不采用"], ["deferred", "暂缓"] ] as const).map(([status, label]) => <button key={status} type="button"
        className={status === "accepted" ? "btn-secondary !text-violet" : "btn-ghost"} disabled={disabled || decision?.status === status}
        onClick={() => void onDecide(proposal.id, status)}>{label}</button>)}
      <button type="button" className="btn-ghost" disabled={disabled} aria-expanded={editing} onClick={() => setEditing(!editing)}>修改方案</button>
    </div>
    {editing && <div className="mt-3">
      <label htmlFor={`proposal-${proposal.id}`} className="block text-[12px] text-ink-muted">写下要采用的修改内容</label>
      <textarea id={`proposal-${proposal.id}`} value={note} maxLength={6000} disabled={disabled} onChange={(event) => setNote(event.target.value)}
        className="mt-2 w-full rounded-xl border border-paper-line bg-white p-3 text-[13px]" rows={3} />
      <button type="button" className="btn-secondary mt-2" disabled={disabled || !note.trim()} onClick={async () => {
        if (await onDecide(proposal.id, "revised", note.trim())) { setEditing(false); setNote(""); }
      }}>保存修改意见</button>
    </div>}
  </section>;
}
