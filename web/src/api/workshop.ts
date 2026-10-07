import type { JobStatus } from "./client";

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
const ROOT = "/api/workshop/sessions";

export type NarrativeMode = "short_novel" | "standalone_long" | "multi_volume" | "series_installment";

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

export interface WorkshopSession {
  id: string;
  revision: number;
  input: WorkshopInput;
  messages: Array<{ role: "user" | "assistant"; content: string; questions?: string[] }>;
  prompt_md: string;
  questions: string[];
  ready_for_confirmation: boolean;
  status: "draft" | "running" | "ready" | "error" | "launched";
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
  list: (query = "", limit = 20, offset = 0) => request<{ sessions: WorkshopSessionSummary[]; total: number }>(
    `${ROOT}?${new URLSearchParams({ q: query, limit: String(limit), offset: String(offset) })}`,
  ),
  create: (input: WorkshopInput) => request<WorkshopSession>(ROOT, input),
  get: (id: string) => request<WorkshopSession>(sessionPath(id)),
  turn: (id: string, message: string, revision: number) =>
    request<JobStatus>(`${sessionPath(id)}/turns`, { message, revision }),
  prepare: (id: string, revision: number) =>
    request<WorkshopSession>(`${sessionPath(id)}/prepare`, { revision }),
  launch: (id: string, revision: number) =>
    request<{ project_id: string; job_id: string }>(`${sessionPath(id)}/launch`, { revision }),
  promptUrl: (id: string) => `${BASE}${sessionPath(id)}/prompt`,
};
