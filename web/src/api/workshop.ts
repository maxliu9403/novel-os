import { ApiError, type JobStatus } from "./client";
import { API_BASE as BASE } from "./base";
import type { CreativeTypeRef, LegacyCreativeTypeRef, TypeSnapshot } from './creativeTypes';
import type { RequestRecovery, RequestRecoveryRetry } from './requestRecovery';

const ROOT = "/api/workshop/sessions";

export type NarrativeMode = "short_novel" | "standalone_long" | "multi_volume" | "series_installment";
export type WorkshopDeliveryStage = "story_design" | "chapter_plan" | "assembly";
export type WorkshopTurnIntent = "discuss" | "deliver";
export type WorkshopDecisionStatus = "accepted" | "rejected" | "revised" | "deferred";

export interface WorkshopProposal {
  combination_status?: "complete" | "partial";
  missing_contributions?: string[];
  missing_contribution_names?: string[];
  tag_contributions?: Array<{ tag_id: string; tag_name?: string; contribution: string }>;
  choice?: string;
  cost?: string;
  payoff?: string;
  id: string;
  title: string;
  story: string;
  tradeoff: string;
  recommended: boolean;
  genre_roles: Array<{ selection: string; role: "world" | "conflict" | "relationship" | "tone" | "audience"; contribution: string }>;
  mechanisms: Array<{ selection: string; name: string; rule: string; boundary: string; cost: string;
    choice: string; consequence: string; payoff: string; removal_effect: string }>;
}

export interface WorkshopDesignRound {
  source_revision: number;
  message_index: number;
  status: "recorded" | "unavailable";
  proposals: WorkshopProposal[];
  notice?: string;
  recommendation_reason?: string;
  assumptions?: string[];
  conflicts?: string[];
  next_focus?: { topic: string; why: string };
  guidance?: WorkshopGuidance;
  story?: StoryMap;
}

export interface WorkshopGuidance {
  status: 'assessed' | 'unavailable';
  reviewed_revision: number;
  notice?: string;
  groups?: Array<{ id: string; topic: string; why: string; option_ids: string[]; recommended_id: string | null }>;
  coverage?: Array<{ area: 'premise' | 'characters' | 'plot' | 'ending'; status: 'missing' | 'proposed' | 'settled' | 'conflict'; summary: string;
    evidence: Array<{ message_index?: number; input_field?: string; quote: string }> }>;
  next_action?: 'choose' | 'refine' | 'generate';
  reason?: string;
  missing?: string[];
}

export type StoryAction = { mode: 'select' | 'custom' | 'delegate' | 'question' | 'reframe' | 'reopen'; question_id: string };
export type StoryArea = 'direction' | 'opening' | 'middle' | 'climax' | 'ending';
export interface StoryMap {
  synopsis: string; focus: StoryArea; reviewed_revision: number; source_revision: number;
  changes: string[];
  nodes: Array<{ area: StoryArea; summary: string; status: 'missing' | 'proposed' | 'settled' | 'conflict'; evidence: Array<{ quote: string }> }>;
  opening?: { situation: string; anchor: string; reader_experience: string; genre_cause: string; first_value: string; next_expectation: string; preview?: string };
}
export interface StoryQuestion {
  id: string; topic: string; why: string; option_ids: string[]; recommended_id: string | null;
  proposals: WorkshopProposal[]; recommendation_reason?: string;
}
export interface StoryState {
  map: StoryMap | null; pending: StoryQuestion | null; notice?: string;
  validation?: {
    schema: 'story-validation.v1'; source_revision: number; reviewed_revision: number;
    status: 'complete' | 'partial' | 'unavailable'; map_status: 'updated' | 'preserved' | 'unavailable';
    opening_status: 'updated' | 'unavailable'; candidate_path?: string; candidate_save_failed?: boolean;
    issues: Array<{ component: string; path: string; code: string; message: string }>;
  };
  actions: Array<{ mode: StoryAction['mode']; question: StoryQuestion; proposal_ids: string[]; note: string; revision: number; created_at: string }>;
  assumptions: Array<{ id: string; text: string; kind: 'major_premise' | 'causal_gap' | 'detail' | 'external_fact'; status: string; source_revision: number; proposal_id?: string | null; question_id?: string | null; resolution?: string }>;
}
export interface GuidedTurn {
  advance?: 'develop_story' | 'plan_capacity';
  capacity_replan?: { proposal_sha256: string; option_id: string; chapters: number; total_words?: number; words_per_chapter?: number; chapter_targets?: Array<{ number: number; target_words: number }> };
  capacity_choice?: { proposal_sha256: string; option_id: string };
  story_action?: StoryAction;
  revision: number;
  message: string;
  intent: WorkshopTurnIntent;
  proposal_ids: string[];
  operation_id: string;
}

export interface WorkshopDesign {
  schema: string;
  policy_sha256: string;
  story_state?: StoryState;
  policy: { capacity_version?: string; story_workshop_version?: string; guided_choices_version?: string; type_snapshot?: TypeSnapshot; guidance: {
    version: string;
    selections: Array<{ selection: string; coverage: "specialized_guidance" | "general_guidance" | "user_guidance" }>;
  } };
  rounds: WorkshopDesignRound[];
  decisions: Array<{ proposal_id: string; status: WorkshopDecisionStatus; note: string; source: "author_action";
    revision: number; message_index: number; created_at: string }>;
}

export interface WorkshopAssemblyProgress {
  completed_parts: number;
  total_parts: number;
  active_part: string | null;
  parts: Array<{ part: string; label: string; status: "pending" | "running" | "saved"; content_md?: string }>;
}

export interface WorkshopDeliveryProgress {
  status: "running" | "paused" | "error" | "complete";
  source_revision?: number;
  completed_stages: WorkshopDeliveryStage[];
  active_stage: WorkshopDeliveryStage | null;
  resumable: boolean;
  invalid_reason?: string;
  stages?: Array<{ stage: WorkshopDeliveryStage; status: "pending" | "running" | "saved"; content_md?: string }>;
  chapter_windows?: { saved_chapters: number; total_chapters: number; saved_batches: number };
  assembly?: WorkshopAssemblyProgress;
}

export interface WorkshopInput {
  length_planning?: 'story_first' | null;
  title: string;
  author: string;
  genres: string[];
  creative_types?: (CreativeTypeRef | LegacyCreativeTypeRef)[] | null;
  creation_flow?: 'genre_tags.v1' | 'genre_tags.v2' | 'h5_guided.v1' | null;
  audience_channel?: import('./creativeTypes').AudienceChannel | null;
  source_book?: import('./sourceBooks').SourceSelection | null;
  premise: string;
  chapters?: number;
  words_per_chapter?: number;
  target_total_words?: number;
  language: string;
  mode?: NarrativeMode | "" | null;
  audience: string;
  market: string;
  constraints: string;
  method_mode?: "off" | "advisory";
}

export interface BookClassificationSelection {
  catalog_version: 'novel-types.2026-10';
  primary_genre_id: string;
  secondary_genre_ids: string[];
  story_type_ids: string[];
  tone_ids: string[];
  setting_ids: string[];
  audience_channel: 'female' | 'male' | 'general';
}

export interface WorkshopRuntimeProgress {
  completion_grace_seconds?: number;
  completion_grace_limit?: number;
  failure_summary?: { kind: 'no_content_timeout' | 'timeout'; consecutive_count: number } | null;
  upstream_error_event_count?: number;
  last_upstream_failure_kind?: string | null;
  last_upstream_failure_at?: string | null;
  last_diagnostic_code?: string | null;
  last_diagnostic_component?: string | null;
  last_diagnostic_level?: string | null;
  last_diagnostic_at?: string | null;
  revision: number;
  operation: "brainstorm" | "titles";
  job_id: string | null;
  attempt: number | null;
  request_id: string | null;
  started_at: string | null;
  deadline_at: string | null;
  task_budget_seconds?: number | null;
  request_timeout_seconds?: number | null;
  phase: "preparing" | "starting" | "waiting" | "receiving" | "parsing" | "validating" | "repairing" | "delivery" | "completed" | "failed" | "cancelled";
  request_started_at?: string | null;
  last_reasoning_observed_at?: string | null;
  last_text_observed_at?: string | null;
  last_transport_observed_at?: string | null;
  reasoning_event_count?: number;
  text_event_count?: number;
  transport_event_count?: number;
  last_activity_observed_at?: string | null;
  activity_kind?: "reasoning_summary" | "text" | null;
  activity_time_basis?: "batch_observed" | null;
  delivery_stage?: WorkshopDeliveryStage | null;
  stage_index?: number | null;
  completed_stages?: number;
  current_stage_status?: "started" | "completed" | "reused" | null;
  last_worker_check_at: string | null;
  last_cli_event_at: string | null;
  last_content_at: string | null;
  content_chars: number;
  cumulative_content_chars?: number;
  last_turn_content_at?: string | null;
  last_completed_attempt?: number | null;
  repairing_response?: boolean;
  last_upstream_event_at: string | null;
  upstream_event: string | null;
  upstream_endpoint_category?: "models" | "responses" | "usage" | "instructions" | "other" | null;
  configured_max_tokens: number | null;
  effective_max_tokens: number | null;
  output_limit_supported: boolean | null;
}

export interface WorkshopSession {
  request_recovery?: RequestRecovery | null;
  recovery?: { kind: 'saved_generation'; can_resume: boolean; label: string; detail: string };
  source_context?: { schema?: string; selected_proposal?: import('./sourceRoutes').StoryRoute; [key: string]: unknown } | null;
  development_view?: {
    phase: 'exploring' | 'developing' | 'reviewing' | 'needs_revision' | 'needs_author'; title: string; notice?: string;
    issues?: Array<{ reason: string; quote: string; repair?: string }>;
    commitments: Array<{ id: string; category: string; text: string; adoption: string | { kind: string; ref?: string; scope?: string; status?: string; scope_rule?: string } }>;
    emotion_designs: Array<{ expectation: string; scene: string; choice: string; cost: string; change: string; payoff: string }>;
  };
  next_step?: WorkshopNextStep;
  capacity_replan?: { request: NonNullable<GuidedTurn['capacity_replan']>; chapter_words_limit: number; total_words: number } | null;
  skeleton_review?: SkeletonReview;
  capacity?: StoryCapacity;
  id: string;
  revision: number;
  input: WorkshopInput;
  initial_input?: WorkshopInput;
  messages: Array<{ role: "user" | "assistant"; content: string; questions?: string[]; action?: GuidedTurn['advance']; author_note?: string }>;
  prompt_md: string;
  questions: string[];
  ready_for_confirmation: boolean;
  status: "draft" | "running" | "paused" | "ready" | "error" | "launched";
  last_error: string | null;
  job_id: string | null;
  skill_version: string;
  creative_design?: WorkshopDesign;
  prepared: null | {
    revision: number;
    command: string;
    project_name: string;
    prompt_filename: string;
    validation: Record<string, unknown>;
    output_formats: string[];
  };
  project_id: string | null;
  logs: Array<{ time: string; event: string; message: string }>;
  active_operation?: "brainstorm" | "titles";
  turn_intent?: WorkshopTurnIntent;
  runtime_progress?: WorkshopRuntimeProgress | null;
  delivery_progress?: WorkshopDeliveryProgress | null;
  paused_at?: string | null;
  input_needs_revision?: boolean;
  restart_required?: boolean;
  context_start_revision?: number;
  history?: Array<{ revision: number; archived_at: string; title: string; message_count: number; has_prompt: boolean }>;
  naming?: {
    source_sha256: string;
    candidates: Array<{ title: string; reason: string; angle?: string; source_ids?: string[] }>;
    confirmed_title: string | null;
    requests: Array<{ instructions: string; current_title?: string; created_at: string; candidates?: Array<{ title: string; reason: string; angle?: string }>; error?: string;
      metrics?: { input_bytes: number; context_bytes: number; skeleton_bytes: number; prompt_version?: number; attempts: number } }>;
  } | null;
}

export interface WorkshopNextStep {
  readiness?: { state: 'exploring' | 'ready_for_capacity' | 'ready_for_skeleton' | 'blocked' | 'delivered'; title: string; detail: string };
  kind: 'discuss' | 'resolve_question' | 'plan_capacity' | 'choose_capacity' | 'deliver' | 'wait' | 'recover' | 'edit_basics' | 'complete';
  label: string;
  reason: string;
  can_continue: boolean;
  can_deliver: boolean;
  can_plan_capacity: boolean;
  delivery_blocked_reason: string | null;
}

export interface WorkshopSessionSummary {
  id: string;
  title: string;
  status: WorkshopSession["status"];
  revision: number;
  updated_at: string;
  project_id: string | null;
  has_prompt: boolean;
  message_count: number;
  premise_preview: string;
  restart_required?: boolean;
}

export interface CapacityOption {
  id: string; title: string; tradeoff: string; recommended: boolean; total_words: number;
  chapters: Array<{ number: number; scene_ids: string[]; target_words: number }>;
}

export interface SkeletonReview {
  schema: 'skeleton-review.v1'; status: 'proposed' | 'clear' | 'unavailable' | 'resolved';
  source_sha256: string; source_revision: number; review_sha256?: string;
  summary?: string; notice?: string; notes?: string[];
  issues: Array<{ id: string; chapter: number; category: string; quote: string; replacement: string; problem: string }>;
  selected_ids?: string[]; decision?: 'apply_selected' | 'keep_original'; resolved_revision?: number;
}
export interface SkeletonRevisionRequest {
  revision: number; review_sha256: string; selected_ids: string[]; operation_id: string;
}
export interface StoryCapacity {
  length_request?: { target_total_words: number; matches_requested_total: boolean };
  adjustment?: { status: 'needs_more_space'; reason: string; minimum_total_words: number; requested_total_words: number; requested_chapters: number; chapter_words_limit: number };
  schema: 'story-capacity.v1'; status: 'proposed' | 'confirmed' | 'stale' | 'unavailable';
  notice?: string; proposal_sha256?: string; reviewed_revision?: number;
  plan?: { language: string; count_unit: 'words' | 'characters'; rationale: string;
    scenes: Array<{ id: string; title: string; partition_label?: string; source_quote: string; presentation: 'on_page' | 'summary'; reader_value: string; minimum: number; maximum: number; reason: string }>;
    options: CapacityOption[] };
  confirmed?: { option: CapacityOption; language: string; count_unit: 'words' | 'characters' } | null;
}

async function request<T>(path: string, body?: unknown, method?: "DELETE"): Promise<T> {
  const response = await fetch(`${BASE}${path}`, method ? { method } : body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`;
    try {
      const result = await response.json();
      if (typeof result.detail === "string") detail = result.detail;
      else if (typeof result.detail?.message === "string") detail = result.detail.message;
    } catch { /* Keep the HTTP status when the proxy does not return JSON. */ }
    throw new ApiError(detail, response.status);
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}

const sessionPath = (id: string) => `${ROOT}/${encodeURIComponent(id)}`;

export const workshopApi = {
  list: (query = "", limit = 20, offset = 0, status = "") => request<{ sessions: WorkshopSessionSummary[]; total: number }>(
    `${ROOT}?${new URLSearchParams({ q: query, limit: String(limit), offset: String(offset), ...(status ? { status } : {}) })}`,
  ),
  create: (input: WorkshopInput) => request<WorkshopSession>(ROOT, input),
  get: (id: string) => request<WorkshopSession>(sessionPath(id)),
  delete: (id: string, revision: number) =>
    request<void>(`${sessionPath(id)}?revision=${revision}`, undefined, "DELETE"),
  turn: (id: string, message: string, revision: number, intent: WorkshopTurnIntent = "discuss") =>
    request<JobStatus>(`${sessionPath(id)}/turns`, { message, revision, intent }),
  guidedTurn: (id: string, body: GuidedTurn) => request<JobStatus>(`${sessionPath(id)}/turns`, body),
  saveChoices: (id: string, body: GuidedTurn) => request<WorkshopSession>(`${sessionPath(id)}/choices`, body),
  upgradeGuidance: (id: string, revision: number, operation_id: string, protocol: 'guided-choices.v1' | 'story-workshop.v1' = 'guided-choices.v1') =>
    request<WorkshopSession>(`${sessionPath(id)}/guidance`, { revision, operation_id, protocol }),
  decide: (id: string, revision: number, proposal_id: string, status: WorkshopDecisionStatus, note = "") =>
    request<WorkshopSession>(`${sessionPath(id)}/decisions`, { revision, proposal_id, status, note }),
  pause: (id: string, revision: number, job_id: string) =>
    request<WorkshopSession>(`${sessionPath(id)}/pause`, { revision, job_id }),
  resume: (id: string, revision: number, request_recovery?: RequestRecoveryRetry) =>
    request<JobStatus>(`${sessionPath(id)}/resume`, { revision, ...(request_recovery ? { request_recovery } : {}) }),
  restart: (id: string, revision: number) =>
    request<JobStatus>(`${sessionPath(id)}/restart`, { revision }),
  history: (id: string, revision: number) =>
    request<WorkshopSession>(`${sessionPath(id)}/history/${revision}`),
  titles: (id: string, revision: number, instructions: string, current_title = "") =>
    request<JobStatus>(`${sessionPath(id)}/titles`, { revision, instructions, current_title }),
  updateInput: (id: string, revision: number, input: WorkshopInput) =>
    request<WorkshopSession>(`${sessionPath(id)}/input`, { revision, input }),
  prepare: (id: string, revision: number, title?: string) =>
    request<WorkshopSession>(`${sessionPath(id)}/prepare`, { revision, ...(title !== undefined ? { title } : {}) }),
  reviseSkeleton: (id: string, body: SkeletonRevisionRequest) =>
    request<WorkshopSession>(`${sessionPath(id)}/skeleton-revision`, body),
  launch: (id: string, revision: number) =>
    request<{ project_id: string; job_id: string }>(`${sessionPath(id)}/launch`, { revision }),
  promptUrl: (id: string) => `${BASE}${sessionPath(id)}/prompt`,
};
