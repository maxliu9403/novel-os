import { API_BASE } from "./base";
import { ApiError, type CoverCandidate, type CoverSet } from "./client";

export interface PosterDirection {
  id: string; title: string; story_promise: string; composition: string;
  medium: string; palette: string | string[]; characters: string[];
  image_prompt: string; title_position: "top" | "bottom" | "left";
}
export interface PosterPlan {
  plan_id: string; plan_hash: string; pipeline_kind: "skeleton_poster";
  created_at: string; source_sha256: string; status: "draft" | "approved"; stale?: boolean;
  story: { premise: string; core_conflict: string; emotional_promise: string;
    cast: Array<{name: string; role: string; identity: string; relationship: string}>;
    world: string; hard_constraints: string[] };
  directions: PosterDirection[];
}
export interface PosterTask {
  task_id: string; kind: string; status: "queued" | "running" | "complete" | "failed" | "interrupted";
  stage: string; created_at: string; updated_at: string; error?: string;
  request_id?: string; request_aliases?: string[];
  result?: {plan_id?: string; cover_set_id?: string; candidate_id?: string};
  completed?: number; total?: number;
  completed_model_calls?: number;
  last_model_event?: PosterModelEvent; last_model_event_at?: string;
  last_upstream_event?: PosterModelEvent; last_upstream_event_at?: string;
  input?: {cover_set_id?: string; candidate_id?: string; prompt?: string; keep_composition?: boolean};
}
export interface PosterModelEvent {
  event: string; source_timestamp?: string; observed_at?: string; received_at?: string;
  upstream_event?: string; event_kind?: string; activity_kind?: string;
}
export interface PosterCandidate extends CoverCandidate {
  artwork_url?: string | null; typography_available?: boolean; requested_model?: string;
  observed_model?: string; parent_candidate_id?: string; typography?: PosterLayout;
  poster_metadata?: {requested_model?: string; observed_model?: string; typography_available?: boolean; parent_candidate_id?: string; title_layout?: PosterLayout};
}
export interface PosterSet extends Omit<CoverSet, "candidates"> {
  candidates: PosterCandidate[]; plan_id?: string; pipeline_kind?: string;
  advisory?: {status: "queued" | "running" | "complete" | "failed" | "interrupted"; candidates?: Array<{candidate_id: string; summary: string; suggestions: string[]}>; portfolio_note?: string};
  repair_lineage?: {parent_cover_set_id?: string; parent_candidate_id?: string; target_concept_id?: string; operation?: string};
}
export interface PosterOverview {
  enabled: boolean; title: string;
  source: {bytes: number; sha256: string; preview: string; path?: string; error?: string};
  plans: PosterPlan[]; sets: PosterSet[]; tasks: PosterTask[];
  active_cover_set_id: string; selected_candidate_id: string; active_revision?: number; legacy_active?: boolean;
  delivery?: PosterDelivery;
  advisory_enabled?: boolean;
}
export interface PosterDelivery {status: "ready" | "failed" | "pending" | "stale" | "missing"; error?: string; retryable?: boolean}
export interface PosterPreferences {market: string; audience: string; emphasis: string; notes: string}
export interface PosterLayout {
  lines?: string[]; font_id?: "serif-bold" | "sans-bold" | "cjk-sans";
  font_sha256?: string; fonts?: Record<string, string>;
  position?: "top" | "middle" | "bottom"; region?: [number, number, number, number];
  font_size_min?: number; font_size_max?: number; color?: string;
  align?: "left" | "center" | "right"; line_spacing?: number; letter_spacing?: number;
  line_styles?: Array<{font_id?: "serif-bold" | "sans-bold" | "cjk-sans"; scale?: number; color?: string}>;
  backdrop?: {color: string; opacity: number; mode?: "solid" | "gradient"; direction?: "top" | "bottom" | "left" | "right"};
  stroke?: {color: string; width: number};
  shadow?: {color: string; opacity: number; blur: number; offset_x: number; offset_y: number};
}

const path = (id: string) => `/api/projects/${encodeURIComponent(id)}/poster`;
const candidatePath = (id: string, setId: string, candidateId: string) =>
  `${path(id)}/sets/${encodeURIComponent(setId)}/candidates/${encodeURIComponent(candidateId)}`;
async function request<T>(url: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${url}`, {
    method, signal, headers: body === undefined ? undefined : {"Content-Type": "application/json"},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail: unknown;
    try { detail = (await response.json()).detail; } catch { /* A proxy may return plain text. */ }
    const message = typeof detail === "string" ? detail : detail && typeof detail === "object" && "message" in detail
      ? String(detail.message) : `请求失败（HTTP ${response.status}）`;
    throw new ApiError(message, response.status, detail);
  }
  return response.json() as Promise<T>;
}
export const posterApi = {
  overview: (id: string, signal?: AbortSignal) => request<PosterOverview>(path(id), "GET", undefined, signal),
  rebuildDelivery: (id: string) => request<{delivery: PosterDelivery}>(`/api/projects/${encodeURIComponent(id)}/deliverables/package/rebuild`, "POST"),
  plan: (id: string, requestId: string, preferences: PosterPreferences) => request<PosterTask>(`${path(id)}/plans`, "POST", {request_id: requestId, preferences}),
  editPlan: (id: string, plan: PosterPlan, directions: PosterDirection[]) => request<PosterPlan>(`${path(id)}/plans/${encodeURIComponent(plan.plan_id)}`, "PATCH", {expected_hash: plan.plan_hash, directions}),
  generate: (id: string, requestId: string, plan: PosterPlan) => request<PosterTask>(`${path(id)}/batches`, "POST", {request_id: requestId, plan_id: plan.plan_id, plan_hash: plan.plan_hash}),
  retry: (id: string, requestId: string, setId: string, candidateIds?: string[]) => request<PosterTask>(`${path(id)}/sets/${encodeURIComponent(setId)}/retry`, "POST", {request_id: requestId, ...(candidateIds ? {candidate_ids: candidateIds} : {})}),
  refine: (id: string, requestId: string, setId: string, candidateId: string, prompt: string, keepComposition: boolean) => request<PosterTask>(`${candidatePath(id, setId, candidateId)}/refine`, "POST", {request_id: requestId, prompt, keep_composition: keepComposition}),
  typography: (id: string, requestId: string, setId: string, candidateId: string, layout: PosterLayout) => request<PosterTask>(`${candidatePath(id, setId, candidateId)}/typography`, "POST", {request_id: requestId, layout}),
  select: (id: string, setId: string, candidateId: string, revision: number, sha256?: string, activeRevision?: number) => request<PosterSet>(`${candidatePath(id, setId, candidateId)}/select`, "POST", {expected_revision: revision, expected_sha256: sha256, expected_active_revision: activeRevision}),
  async typographyPreview(id: string, setId: string, candidateId: string, layout: PosterLayout, signal?: AbortSignal): Promise<Blob> {
    const response = await fetch(`${API_BASE}${candidatePath(id, setId, candidateId)}/typography-preview`, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({layout}), signal,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : "排版预览失败，请调整字号或文字区域。");
    }
    return response.blob();
  },
};

function uuid() {
  if (typeof globalThis.crypto?.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
const pending = new Map<string, {id: string; fingerprint: string}>();
/** A lost HTTP response must not turn a second click into a second paid call. */
export function posterIntent(owner: string, scope: string, payload: unknown): string {
  const key = `novel-os:poster-intent:${owner}:${scope}`;
  const fingerprint = JSON.stringify(payload);
  let saved = pending.get(key);
  try { saved = JSON.parse(sessionStorage.getItem(key) || "null") || saved; } catch { /* Storage can be disabled. */ }
  if (saved?.fingerprint === fingerprint && saved.id) return saved.id;
  const next = {id: uuid(), fingerprint}; pending.set(key, next);
  try { sessionStorage.setItem(key, JSON.stringify(next)); } catch { /* Keep the in-memory receipt. */ }
  return next.id;
}
export function confirmPosterIntent(owner: string, scope: string, requestId: string) {
  const key = `novel-os:poster-intent:${owner}:${scope}`;
  // A response from an unmounted page must not clear a newer pending intent.
  if (pending.get(key)?.id === requestId) pending.delete(key);
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || "null");
    if (saved?.id === requestId) sessionStorage.removeItem(key);
  } catch { /* Storage can be disabled. */ }
}
export function posterAssetUrl(value?: string | null): string | undefined {
  if (!value) return undefined;
  if (value.startsWith("/api/")) return `${API_BASE}${value}`;
  return /^(https?:|blob:|data:image\/)/.test(value) ? value : undefined;
}

export function confirmObservedPosterIntents(owner: string, tasks: PosterTask[]) {
  const ids = new Set(tasks.flatMap(task => [task.request_id, ...(task.request_aliases || [])]).filter(Boolean));
  const prefix = `novel-os:poster-intent:${owner}:`;
  const keys = new Set([...pending.keys()].filter(key => key.startsWith(prefix)));
  try { for (let index = 0; index < sessionStorage.length; index++) { const key = sessionStorage.key(index); if (key?.startsWith(prefix)) keys.add(key); } } catch { /* Storage may be unavailable. */ }
  for (const key of keys) {
    let item = pending.get(key);
    try { item = JSON.parse(sessionStorage.getItem(key) || "null") || item; } catch { /* Keep the in-memory receipt. */ }
    if (item?.id && ids.has(item.id)) {
      pending.delete(key); try { sessionStorage.removeItem(key); } catch { /* The receipt remains safely deduplicated by the server. */ }
    }
  }
}
