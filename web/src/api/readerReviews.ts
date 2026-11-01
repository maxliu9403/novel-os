import { API_BASE } from "./base";
import type { CostTotals } from "./client";

export type ReviewStatus = "queued" | "reading" | "synthesizing" | "verifying" | "completed" | "paused" | "interrupted" | "failed" | "budget_exhausted";
export const REVIEW_STATUS: Record<ReviewStatus, string> = { queued: "等待开始", reading: "逐段阅读", synthesizing: "综合阅读观察", verifying: "核对报告引用", completed: "评议已完成", paused: "已暂停", interrupted: "执行已中断", failed: "评议未完成", budget_exhausted: "预算已用尽" };
export const DIMENSIONS: Record<string, string> = { plot: "情节与因果", character: "人物与选择", prose: "文字与叙述", world: "世界与规则", theme: "主题表达", emotion: "情绪投入", engagement: "阅读动力", expectation: "读者期待" };
export const DIMENSION_STATUS: Record<string, string> = { strength: "值得保留", improve: "可以改善", disagreement: "存在不同读法", insufficient: "依据不足", not_applicable: "不适用" };
export interface ReviewOptions {
  first_chapter: number; last_chapter: number | null; language: string; audience: string; public_description: string;
  segment_tokens: number; input_tokens: number; output_tokens: number; max_calls: number; request_seconds: number; total_seconds: number; max_estimated_cny: number | null;
}
export const DEFAULT_REVIEW_OPTIONS: ReviewOptions = { first_chapter: 1, last_chapter: null, language: "中文", audience: "", public_description: "", segment_tokens: 8000, input_tokens: 24000, output_tokens: 6000, max_calls: 40, request_seconds: 300, total_seconds: 1800, max_estimated_cny: null };
export interface ReviewPreview {
  proposal_sha256: string; model: { provider: string; model: string }; options: ReviewOptions;
  available_chapters: number; expected_chapters: number; missing_chapters: number[]; complete_book: boolean;
  units: number; minimum_calls: number; source_reference_tokens: number; maximum_reserved_cny: number | null; notes: string[];
  versions?: Record<string, number>; minimum_calls_timeout_ceiling?: number;
}
export interface ReviewEvidence { id: string; chapter: number; unit_id: string; start: number; end: number; quote: string; body_sha256: string }
export interface ReviewFinding { dimension: string; scope: string; observation: string; reader_impact: string; suggested_action: string; preserve: string; uncertainty: string; evidence_ids: string[] }
export interface ReaderReport {
  strengths: ReviewFinding[]; issues: ReviewFinding[]; dimensions: Array<{ dimension: string; status: string; explanation: string; evidence_ids: string[] }>;
  evidence: Record<string, ReviewEvidence>; uncertainties: string[]; limitations: string[]; lossy_summary_levels: number;
}
export interface ReaderReview {
  id: string; status: ReviewStatus; created_at: number; updated_at: number; deadline: number;
  title?: string; can_resume?: boolean;
  worker_active: boolean; pause_requested: boolean; completed_units: number; unit_count: number; call_count: number;
  draining?: boolean;
  model: { provider: string; model: string }; options: ReviewOptions; source_sha256: string; source_current?: boolean | null;
  complete_book: boolean; missing_chapters: number[]; available_chapters: number; expected_chapters: number;
  reviewed_chapters?: number[];
  report_ready: boolean; error: string | null; usage: CostTotals; unrecorded_calls: number; report?: ReaderReport | null;
  last_call_started_at: number | null;
}
export interface ReviewSource { number: number; version: string; body: string; body_sha256: string; snapshot_sha256: string; offset_unit: string }
async function request<T>(projectId: string, path = "", body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(projectId)}/reader-reviews${path}`, {
    ...(body !== undefined ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "评议请求未完成，请检查范围和预算后重试。");
  return data as T;
}
export const readerReviewsApi = {
  list: (id: string) => request<{ reviews: ReaderReview[] }>(id),
  preview: (id: string, options: ReviewOptions) => request<ReviewPreview>(id, "/preview", options),
  create: (id: string, preview: ReviewPreview, key: string) => request<ReaderReview>(id, "", { options: preview.options, proposal_sha256: preview.proposal_sha256, idempotency_key: key }),
  get: (id: string, reviewId: string) => request<ReaderReview>(id, `/${encodeURIComponent(reviewId)}`),
  pause: (id: string, reviewId: string) => request<ReaderReview>(id, `/${encodeURIComponent(reviewId)}/pause`, {}),
  resume: (id: string, reviewId: string) => request<ReaderReview>(id, `/${encodeURIComponent(reviewId)}/resume`, {}),
  source: (id: string, reviewId: string, chapter: number) => request<ReviewSource>(id, `/${encodeURIComponent(reviewId)}/source?chapter=${chapter}`),
};
