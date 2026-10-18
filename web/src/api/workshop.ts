import type { JobStatus } from "./client";
import { API_BASE as BASE } from "./base";

const ROOT = "/api/workshop/sessions";

export type NarrativeMode = "short_novel" | "standalone_long" | "multi_volume" | "series_installment";
export type WorkshopDeliveryStage = "story_design" | "chapter_plan" | "assembly";
export type WorkshopTurnIntent = "discuss" | "deliver";

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
  assembly?: WorkshopAssemblyProgress;
}

export interface WorkshopInput {
  title: string;
  author: string;
  genres: string[];
  premise: string;
  chapters?: number;
  words_per_chapter?: number;
  language: string;
  mode?: NarrativeMode | "" | null;
  audience: string;
  market: string;
  constraints: string;
  method_mode?: "off" | "advisory";
}

export interface WorkshopRuntimeProgress {
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
  id: string;
  revision: number;
  input: WorkshopInput;
  initial_input?: WorkshopInput;
  messages: Array<{ role: "user" | "assistant"; content: string; questions?: string[] }>;
  prompt_md: string;
  questions: string[];
  ready_for_confirmation: boolean;
  status: "draft" | "running" | "paused" | "ready" | "error" | "launched";
  last_error: string | null;
  job_id: string | null;
  skill_version: string;
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
    candidates: Array<{ title: string; reason: string }>;
    confirmed_title: string | null;
    requests: Array<{ instructions: string; current_title?: string; created_at: string; candidates?: Array<{ title: string; reason: string }>; error?: string }>;
  } | null;
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

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${BASE}${path}`, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`;
    try {
      const result = await response.json();
      if (typeof result.detail === "string") detail = result.detail;
      else if (typeof result.detail?.message === "string") detail = result.detail.message;
    } catch { /* Keep the HTTP status when the proxy does not return JSON. */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

const sessionPath = (id: string) => `${ROOT}/${encodeURIComponent(id)}`;

export const workshopApi = {
  list: (query = "", limit = 20, offset = 0, status = "") => request<{ sessions: WorkshopSessionSummary[]; total: number }>(
    `${ROOT}?${new URLSearchParams({ q: query, limit: String(limit), offset: String(offset), ...(status ? { status } : {}) })}`,
  ),
  create: (input: WorkshopInput) => request<WorkshopSession>(ROOT, input),
  get: (id: string) => request<WorkshopSession>(sessionPath(id)),
  turn: (id: string, message: string, revision: number, intent: WorkshopTurnIntent = "discuss") =>
    request<JobStatus>(`${sessionPath(id)}/turns`, { message, revision, intent }),
  pause: (id: string, revision: number, job_id: string) =>
    request<WorkshopSession>(`${sessionPath(id)}/pause`, { revision, job_id }),
  resume: (id: string, revision: number) =>
    request<JobStatus>(`${sessionPath(id)}/resume`, { revision }),
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
  launch: (id: string, revision: number) =>
    request<{ project_id: string; job_id: string }>(`${sessionPath(id)}/launch`, { revision }),
  promptUrl: (id: string) => `${BASE}${sessionPath(id)}/prompt`,
};
