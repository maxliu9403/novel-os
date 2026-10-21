const categories: Record<string, string> = {
  marketing_appeal: "阅读吸引力",
  visual_ambiguity: "画面表达",
  composition_craft: "构图与细节",
  thumbnail_readability: "缩略图辨识",
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Only the versioned, saved advisory classification is displayed as optional. */
export default function CoverCommercialReview({review, commercial = false, concepts, conceptId}: {
  review?: unknown;
  commercial?: boolean;
  concepts: Array<{concept_id: string; label: string}>;
  conceptId?: string;
}) {
  const labels = new Map(concepts.map(concept => [concept.concept_id, concept.label]));
  const rows = record(review) && review.policy_version === "cover-commercial-review.v1" && Array.isArray(review.advisories)
    ? review.advisories.filter(row => record(row)
      && typeof row.category === "string" && Object.hasOwn(categories, row.category)
      && typeof row.concept_id === "string" && (row.concept_id === "portfolio" || labels.has(row.concept_id))
      && (!conceptId || row.concept_id === conceptId || row.concept_id === "portfolio")
      && typeof row.message === "string" && row.message.trim()
      && Array.isArray(row.evidence_refs) && row.evidence_refs.length > 0
      && row.evidence_refs.every((ref: unknown) => typeof ref === "string" && ref.trim())) as Array<{
        category: string; concept_id: string; message: string; evidence_refs: string[];
      }> : [];
  if (!commercial && rows.length === 0) return null;
  return <div className="mt-3 rounded-xl border border-paper-line bg-white/45 px-4 py-3 text-xs leading-relaxed text-ink-muted">
    {commercial && <p>封面以传达故事核心、情绪与阅读吸引力为目标，可采用象征或人物关系表达，不必逐幕复现情节；核心事实与明确要求仍需保持。</p>}
    {rows.length > 0 && <details className={commercial ? "mt-3 border-t border-paper-line pt-3" : ""}>
      <summary className="cursor-pointer font-semibold text-ink-text"><span>可选优化建议</span><span className="ml-2 font-normal text-ink-muted">{rows.length} 条</span></summary>
      <p className="mt-2">这些建议供你取舍，不影响批准；展开或查看不会调用模型。</p>
      <ul className="mt-3 space-y-3">
        {rows.map((row, index) => <li key={`${row.concept_id}:${index}`} className="border-l-2 border-paper-line pl-3">
          <p className="text-[11px]">{row.concept_id === "portfolio" ? "整组方案" : labels.get(row.concept_id)} · {categories[row.category]}</p>
          <p className="mt-1 whitespace-pre-wrap break-words text-ink-text">{row.message}</p>
          <details className="mt-1 text-[11px]"><summary className="cursor-pointer">故事证据引用</summary><p className="mt-1 break-all">{row.evidence_refs.join("、")}</p></details>
        </li>)}
      </ul>
    </details>}
  </div>;
}
