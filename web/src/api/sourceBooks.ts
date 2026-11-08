import { API_BASE } from "./base";
export interface SourceSelection {
  source_id: string;
  analysis_sha256: string;
  mode: "source_development" | "reference_creation" | "original_rewrite";
  use_cover: boolean;
}
export interface SourceStudyState {
  status:
    | "pending"
    | "queued"
    | "running"
    | "failed"
    | "cancelled"
    | "interrupted"
    | "complete";
  completed: number;
  total: number;
  current?: number;
  stage?: string;
  error?: string | null;
  started_at?: string;
  finished_at?: string | null;
  updated_at?: string;
  checked_at?: string;
  worker_active?: boolean;
  cancel_requested?: boolean;
  reading_batches?: number;
  binding?: {
    model?: {
      model?: string;
      reasoning_effort?: string;
      timeout_seconds?: number;
    };
  };
  progress?: {
    phase?: string;
    request_started_at?: string;
    heartbeat_at?: string;
    input_sent_at?: string | null;
    source_files?: number;
    input_bytes?: number;
    received_chars?: number;
    last_content_at?: string | null;
    last_transport_at?: string | null;
    last_activity_at?: string | null;
    activity_kind?: string | null;
  };
}
export interface SourceBook {
  id: string;
  title: string;
  book_id: string;
  chapter_count: number;
  supplement_count: number;
  cover_url: string | null;
  warnings: string[];
  study: SourceStudyState;
}
export interface ContinuationPlan {
  premise: string;
  overall_arc: string;
  proposed_ending: string;
  cards: Array<{
    title: string;
    pov: string;
    source_anchor: string;
    core_plot: string[];
    reader_payoff: string;
    foreshadowing: string;
    ending_hook: string;
  }>;
  major_choices: string[];
  source_conflicts: string[];
}
export interface SourceVersion {
  analysis_sha256: string;
  created_at: string;
  has_plan: boolean;
}
export interface SourceUsage {
  counts: {
    discussions: number;
    projects: number;
    completed: number;
    in_progress: number;
  };
  sessions: Array<{
    id: string;
    title: string;
    status: string;
    source: { analysis_sha256: string };
  }>;
  projects: Array<{
    id: string;
    title: string;
    status: string;
    source: { analysis_sha256: string };
  }>;
}
export interface SourceDetail extends SourceBook {
  synopsis: string;
  versions?: SourceVersion[];
  result: null | {
    analysis_sha256: string;
    analysis: { story_summary: string; uncertainties: string[] };
    continuation_plan?: ContinuationPlan;
    coverage: {
      files: number;
      segments: number;
      all_supplied_text_sent: boolean;
    };
  };
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API_BASE}/api/source-books${path}`, init);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : `资料请求失败 (${r.status})`,
    );
  }
  return r.json();
}
export const sourceBooksApi = {
  list: (signal?: AbortSignal) =>
    request<{ items: SourceBook[] }>("", { signal }),
  get: (id: string, signal?: AbortSignal, version?: string) =>
    request<SourceDetail>(
      `/${id}${version ? `?version=${encodeURIComponent(version)}` : ""}`,
      { signal },
    ),
  usage: (id: string, signal?: AbortSignal) =>
    request<SourceUsage>(`/${id}/usage`, { signal }),
  upload: (files: File[]) => {
    const body = new FormData();
    files.forEach((file) =>
      body.append("files", file, file.webkitRelativePath || file.name),
    );
    return request<SourceBook>("", { method: "POST", body });
  },
  analyze: (id: string, restart = false) =>
    request<SourceStudyState>(`/${id}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ restart }),
    }),
  cancel: (id: string) =>
    request<SourceStudyState>(`/${id}/cancel`, { method: "POST" }),
  coverUrl: (book: SourceBook) =>
    book.cover_url ? `${API_BASE}${book.cover_url}` : "",
};
