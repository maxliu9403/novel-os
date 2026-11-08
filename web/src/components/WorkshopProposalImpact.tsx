import type { WorkshopProposal } from "../api/workshop";

/** The proposed story's concrete choices, not a claim that the manuscript has fulfilled them. */
export default function WorkshopProposalImpact({ proposal }: { proposal: WorkshopProposal }) {
  const hasImpact = proposal.combination_status === "partial" || proposal.choice || proposal.cost || proposal.payoff || proposal.tag_contributions?.length;
  if (!hasImpact) return null;
  return <span className="workshop-proposal-impact">
    {proposal.combination_status === "partial" && <span className="workshop-proposal-consequence">
      <strong>组合说明待补全</strong><span>{proposal.missing_contribution_names?.length
        ? proposal.missing_contribution_names.map(name => `${name}：待说明`).join("；")
        : proposal.missing_contributions?.length ? "已选标签作用待说明" : "人物选择、代价或回报尚需补充"}</span>
    </span>}
    {proposal.tag_contributions?.map((item, index) => <span key={item.tag_id} className="workshop-proposal-contribution">
      <strong>{item.tag_name || `标签 ${index + 1}`}</strong><span>{item.contribution}</span>
    </span>)}
    {([['choice', '人物选择'], ['cost', '要付出的代价'], ['payoff', '看点与回报']] as const).map(([key, label]) => proposal[key] && <span key={key} className="workshop-proposal-consequence">
      <strong>{label}</strong><span>{proposal[key]}</span>
    </span>)}
  </span>;
}
