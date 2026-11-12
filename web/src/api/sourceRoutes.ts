import { API_BASE } from './base';
import { ApiError } from './client';
import type { AudienceChannel, CreativeTypeRef } from './creativeTypes';
import type { SourceSelection } from './sourceBooks';
import type { WorkshopInput, WorkshopSession } from './workshop';

export type SourceRouteOperation = 'initial_routes' | 'revise_routes' | 'expand_routes' | 'revisit_source';
export interface SourceAuthorContext {
  premise?: string;
  title?: string;
  constraints?: string;
  language?: string;
  audience_channel?: AudienceChannel | null;
  creative_types?: CreativeTypeRef[];
  genres?: string[];
  [key: string]: unknown;
}
export interface StoryRoute {
  length_recommendation?: { total_words: number; words_per_chapter: number; chapters: number; language: string; count_unit: 'words' | 'characters'; reason: string };
  route_id: string;
  sha256: string;
  title: string;
  reader_promise: string;
  continuity_basis: string;
  source_refs: string[];
  causal_arc: { opening: string; development: string; turning_point: string; climax: string; ending: string };
  character_relationship_arcs: string[];
  emotional_design: Array<{ expectation: string; scene: string; choice: string; cost: string; change: string; payoff: string }>;
  highlights: Array<{ scene: string; story_value: string }>;
  long_hooks_and_payoffs: Array<{ question: string; development: string; payoff: string }>;
  type_suggestion: { audience: AudienceChannel; genre_id: string; tag_ids: string[]; reason: string };
  proposed_assumptions: string[];
  tradeoffs: string[];
  difference_from_existing: string;
  changes_from_parent: string;
}
export interface StoryRouteSet {
  schema: string;
  sha256: string;
  source_id: string;
  source_sha256: string;
  understanding_sha256: string;
  mode: SourceSelection['mode'];
  parent_route_set_sha256?: string | null;
  author_context: SourceAuthorContext;
  feedback_history: Array<{ exact_text?: string; feedback?: string; text?: string; [key: string]: unknown }>;
  routes: StoryRoute[];
  recommended_route_id: string;
  recommendation_reason: string;
  created_at: string;
  understanding?: { narrative_endpoint?: unknown; opening_analysis?: unknown; summary_projection?: unknown; open_threads?: unknown };
}
export interface SourceRouteRunRequest {
  operation_id: string;
  operation: SourceRouteOperation;
  mode: SourceSelection['mode'];
  author_context: SourceAuthorContext;
  feedback: string;
  base_route_set_sha256?: string;
  target_route_ids?: string[];
  requested_count?: number;
  requested_actions?: Array<{ operation: SourceRouteOperation; target_route_ids?: string[]; requested_count?: number }>;
}
export interface SourceRouteExecution {
  local_outcome?: string;
  reason?: string;
  message?: string;
  timeout_seconds?: number;
  timeout_scope?: string;
  elapsed_seconds?: number;
  observed_at?: string;
  attempt_count?: number;
}
export interface SourceRouteRecovery {
  can_resume: boolean;
  can_retry_unknown: boolean;
  reason: string;
  message: string;
  request_sha256?: string;
  receipt_sha256?: string;
  execution?: SourceRouteExecution;
}
export interface SourceRouteRetryRequest {
  action: 'retry_unknown';
  operation_id: string;
  request_sha256: string;
  receipt_sha256: string;
}
export interface SourceRouteRun {
  run_id: string;
  status: string;
  stage: string;
  completed: number;
  total: number;
  model_calls: number;
  route_set_sha256?: string;
  understanding_sha256?: string;
  error?: string | null;
  request: SourceRouteRunRequest;
  worker_active?: boolean;
  job_id?: string;
  cancel_requested?: boolean;
  pause_requested?: boolean;
  interrupted?: boolean;
  recovery?: SourceRouteRecovery;
  execution?: SourceRouteExecution;
}
export interface SourceRouteListing {
  items: Array<StoryRouteSet | Pick<StoryRouteSet, 'sha256' | 'created_at' | 'mode'>>;
  active_run?: SourceRouteRun | null;
  runs?: SourceRouteRun[];
}
export interface SourceRouteChoice {
  adoption_scope?: 'core_and_payoffs';
  target_total_words?: number;
  words_per_chapter?: number;
  chapters?: number;
  language?: string;
  source_id: string;
  route_set_sha256: string;
  route_id: string;
  route_title: string;
  mode: SourceSelection['mode'];
  author_note: string;
  creative_types: CreativeTypeRef[];
  genres: string[];
  audience_channel: AudienceChannel | null;
  latest_route_set_sha256?: string;
  use_cover?: boolean;
  pending_feedback_run_id?: string;
}
export interface SourceRouteHandoff {
  adoption_scope?: 'core_and_payoffs';
  operation_id: string;
  draft_id?: string;
  source_id: string;
  route_set_sha256: string;
  route_id: string;
  input: WorkshopInput;
  author_note: string;
  session_id?: string;
  revision?: number;
  latest_route_set_sha256?: string;
  use_cover?: boolean;
  mode?: SourceSelection['mode'];
  pending_feedback_run_id?: string;
}
export interface SourceRouteHandoffResult {
  operation_id: string;
  session_id: string;
  status: 'ready' | 'running' | 'error';
  session: WorkshopSession;
}
async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const r = await fetch(`${API_BASE}/api${path}`, {
    signal,
    ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new ApiError(typeof data.detail === 'string' ? data.detail : `故事路线请求未完成 (${r.status})`, r.status);
  }
  return r.json();
}
const sourcePath = (id: string) => `/source-books/${encodeURIComponent(id)}`;
export const sourceRoutesApi = {
  list: (id: string, signal?: AbortSignal) => request<SourceRouteListing>(`${sourcePath(id)}/route-sets`, undefined, signal),
  get: (id: string, sha: string, signal?: AbortSignal) => request<StoryRouteSet>(`${sourcePath(id)}/route-sets/${encodeURIComponent(sha)}`, undefined, signal),
  start: (id: string, body: SourceRouteRunRequest) => request<SourceRouteRun>(`${sourcePath(id)}/route-runs`, body),
  run: (id: string, runId: string, signal?: AbortSignal) => request<SourceRouteRun>(`${sourcePath(id)}/route-runs/${encodeURIComponent(runId)}`, undefined, signal),
  pause: (id: string, runId: string) => request<SourceRouteRun>(`${sourcePath(id)}/route-runs/${encodeURIComponent(runId)}/pause`, {}),
  resume: (id: string, runId: string) => request<SourceRouteRun>(`${sourcePath(id)}/route-runs/${encodeURIComponent(runId)}/resume`, {}),
  retryUnknown: (id: string, runId: string, body: SourceRouteRetryRequest) => request<SourceRouteRun>(`${sourcePath(id)}/route-runs/${encodeURIComponent(runId)}/resume`, body),
  handoff: (body: SourceRouteHandoff) => request<SourceRouteHandoffResult>('/workshop/source-route-handoffs', body),
  getHandoff: (id: string) => request<SourceRouteHandoffResult>(`/workshop/source-route-handoffs/${encodeURIComponent(id)}`),
};
