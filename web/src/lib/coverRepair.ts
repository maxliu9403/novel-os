import { ApiError, type CoverCandidate, type CoverConcept, type CoverRefinementReplanContext, type CoverRepairTask } from "../api/client";

export const repairKindNames = {design: "故事与构图", render: "画面绘制", typography: "标题排版", evaluation: "质量审核", refinement: "原图精修"};
export const refinementPromptLimit = 2000;

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value)
  ? value as Record<string, unknown> : {};

export function coverArtworkUrl(candidate?: CoverCandidate): string | null {
  const url = record(candidate?.quality_report?.assets).artwork_url;
  return typeof url === "string" && url.trim() ? url : null;
}

export function refinementReferenceUrl(task: CoverRepairTask, original?: CoverCandidate): string | null {
  if (task.reference_image?.kind === "artwork") return coverArtworkUrl(original);
  if (task.reference_image?.kind === "cover") return original?.url || null;
  return coverArtworkUrl(original) || original?.url || null;
}

export function isRefinementConcept(concept?: CoverConcept): boolean {
  const scene = concept?.scene_plan;
  const binding = record(record(scene?._source_binding).single_candidate_repair);
  return Boolean(scene?._image_refinement || binding.refinement_prompt_sha256 || binding.reference_image_sha256);
}

export function matchesRefinementTask(task: CoverRepairTask | null, setId: string, candidateId: string): boolean {
  return task?.kind === "refinement" && task.result_candidate_id === candidateId && Boolean(task.cover_set_id)
    && (task.cover_set_id === setId || (task.lookup_cover_set_id === setId && task.lookup_candidate_id === candidateId));
}

export function refinementReplanContext(error: unknown): CoverRefinementReplanContext | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = record(error.detail), context = record(detail.replan_context);
  if (detail.code !== "cover_refinement_replan_required"
    || typeof context.parent_cover_set_id !== "string" || typeof context.parent_candidate_id !== "string"
    || !Number.isInteger(context.expected_revision) || Number(context.expected_revision) < 0
    || typeof context.refinement_prompt !== "string" || !context.refinement_prompt.trim()) return null;
  return context as unknown as CoverRefinementReplanContext;
}

export function refinementSavedResult(error: unknown): {cover_set_id: string; candidate_id: string} | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = record(error.detail), context = record(detail.result_context);
  return detail.code === "cover_refinement_result_available" && typeof context.cover_set_id === "string" && typeof context.candidate_id === "string"
    ? {cover_set_id: context.cover_set_id, candidate_id: context.candidate_id} : null;
}

export function refinementUnavailableReason(candidate: CoverCandidate, concept?: CoverConcept): string | null {
  if (!["ready", "selected", "rejected"].includes(candidate.status)) return "候选图片尚未生成完成，暂不能精修。";
  if (candidate.quality_report?.typography_amendment || concept?.scene_plan?._cover_import) {
    return "这张图片含独立排版修订或导入绑定，请先使用原修订流程核验。";
  }
  if (!record(concept?.scene_plan?._source_binding).direction_id) return "历史图片缺少完整来源绑定，请先创建并批准来源绑定方向。";
  if (!candidate.url) return "未保留可用于编辑的图片，暂不能基于原图精修。";
  return null;
}
export function repairKind(candidate: CoverCandidate) {
  const kind = (candidate.quality_report?.repair_decision as {kind?: string} | undefined)?.kind;
  return kind && kind !== "refinement" && Object.hasOwn(repairKindNames, kind) ? kind as CoverRepairTask["kind"] : null;
}
