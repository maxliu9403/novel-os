import { API_BASE } from "./base";
import type { WorkshopInput, WorkshopSession } from "./workshop";

export type CreativeStage =
  | "brainstorm"
  | "story_design"
  | "chapter_plan"
  | "draft"
  | "review"
  | "publication"
  | "cover";
export type KnowledgeKind = "genre" | "tag";
export type AudienceChannel = "female" | "male";
export interface AudienceGuidance {
  schema: "audience-guidance.v1";
  audience_channels: AudienceChannel[];
  guides: Partial<Record<AudienceChannel, string>>;
  original_sha256: string;
}
export type KnowledgeRole = "conflict" | "relationship" | "character" | "world" | "setting" | "mood" | "voice" | "pacing" | "ending";
export interface CreativeTypeRef {
  id: string;
  version: number;
  source_sha256: string;
}
/** Pre-Genre library receipts remain historical data, never inferred modern IDs. */
export interface LegacyCreativeTypeRef {
  profile_id: string;
  version: number;
  source_sha256: string;
  role?: string;
}
export function isCreativeTypeRef(value: unknown): value is CreativeTypeRef {
  if (!value || typeof value !== "object") return false;
  const ref = value as Partial<CreativeTypeRef>;
  return typeof ref.id === "string" && (ref.id.startsWith("genre.") || ref.id.startsWith("tag.")) &&
    typeof ref.version === "number" && Number.isInteger(ref.version) && ref.version > 0 &&
    typeof ref.source_sha256 === "string" && /^[a-f0-9]{64}$/.test(ref.source_sha256);
}
export interface KnowledgeDocument {
  audience_guidance?: AudienceGuidance | null;
  schema?: "novel-knowledge.v1" | "novel-knowledge.v2" | "novel-knowledge.v3";
  roles?: KnowledgeRole[];
  format?: "tag-prompt.v1" | "markdown-guide.v1";
  body?: string;
  preamble?: string;
  id: string;
  kind: KnowledgeKind;
  name: string;
  aliases: string[];
  genre_ids: string[];
  sections: Record<string, string>;
  missing_sections: string[];
  appendices: string[];
}
export interface CreativeSummary {
  audience_channels?: AudienceChannel[];
  audience_ready?: boolean;
  audience_issue?: string | null;
  id: string;
  kind: KnowledgeKind;
  revision: number;
  status: "draft" | "enabled" | "disabled";
  origin: string;
  updated_at: string;
  active_version: number | null;
  source_sha256: string | null;
  name: string;
  aliases: string[];
  genre_ids?: string[];
  promise: string;
  has_draft_changes: boolean;
  missing_sections: string[];
}
export interface CreativeRecord {
  id: string;
  kind: KnowledgeKind;
  revision: number;
  status: CreativeSummary["status"];
  draft: string;
  active_version: number | null;
  updated_at: string;
  origin: string;
  versions: Array<{
    version: number;
    markdown: string;
    sha256: string;
    created_at: string;
  }>;
  document?: KnowledgeDocument;
}
export interface CreativeLibrary {
  items: CreativeSummary[];
}
export interface KnowledgeIdentity {
  id: string;
  kind: KnowledgeKind;
  name: string;
  genre_ids: string[];
}
export type PreparedMarkdown =
  | { needs_identity: true; body: string }
  | { needs_identity: false; markdown: string; document: KnowledgeDocument };
export interface TypeSnapshot {
  schema: "selection-snapshot.v1" | "selection-snapshot.v2";
  audience_channel?: AudienceChannel;
  genre_id: string;
  tag_ids: string[];
  snapshot_sha256: string;
  documents: Array<CreativeTypeRef & { markdown: string }>;
}
export interface SessionTypeContext {
  snapshot: TypeSnapshot | null;
  contexts: Record<string, unknown>;
  context_sha256: Record<string, string>;
  latest: CreativeSummary[];
  revision: number;
  last_request?: { submitted_at: string } | null;
}
export class CreativeApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}
async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    `${API_BASE}${path}`,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const json = await response.json().catch(() => ({}));
    throw new CreativeApiError(
      typeof json.detail === "string"
        ? json.detail
        : `请求失败 (${response.status})`,
      response.status,
    );
  }
  return response.json();
}
const root = "/api/creative-types";
export const creativeTypesApi = {
  audience: (id: string, revision: number, operationId: string, audience_channels: AudienceChannel[], guides: Partial<Record<AudienceChannel, string>>, activate = true) =>
    request<CreativeRecord>(`${root}/${encodeURIComponent(id)}/audience`, {
      revision, operation_id: operationId, audience_channels, guides, activate,
    }),
  prepare: (markdown: string, options?: { base_markdown?: string; identity?: KnowledgeIdentity }) =>
    request<PreparedMarkdown>(`${root}/prepare`, { markdown, ...options }),
  list: () => request<CreativeLibrary>(root),
  get: (id: string) =>
    request<CreativeRecord>(`${root}/${encodeURIComponent(id)}`),
  template: (kind: KnowledgeKind, id: string, name: string, genreId?: string) =>
    request<{ markdown: string }>(
      `${root}/template?${new URLSearchParams({ kind, id, name, ...(genreId ? { genre_id: genreId } : {}) })}`,
    ),
  create: (markdown: string, operationId: string) =>
    request<CreativeRecord>(root, { markdown, operation_id: operationId }),
  mutate: (
    id: string,
    action: "draft" | "activate" | "disable" | "delete" | "restore",
    revision: number,
    operationId: string,
    markdown?: string,
    version?: number,
  ) =>
    request<CreativeRecord>(`${root}/${encodeURIComponent(id)}/${action}`, {
      revision,
      operation_id: operationId,
      markdown,
      version,
    }),
  preview: (markdown: string) =>
    request<{ document: KnowledgeDocument; bytes: number }>(`${root}/preview`, {
      markdown,
    }),
  context: (refs: CreativeTypeRef[], stage: CreativeStage) =>
    request<{ context: unknown; bytes: number }>(`${root}/preview`, {
      refs,
      stage,
    }),
  downloadUrl: (id: string) =>
    `${API_BASE}${root}/${encodeURIComponent(id)}/markdown`,
  sessionContext: (id: string) =>
    request<SessionTypeContext>(
      `/api/workshop/sessions/${encodeURIComponent(id)}/type-context`,
    ),
  adoptSession: (
    id: string,
    revision: number,
    refs: CreativeTypeRef[],
    operationId: string,
  ) =>
    request<WorkshopSession>(
      `/api/workshop/sessions/${encodeURIComponent(id)}/type-snapshot`,
      { revision, refs, operation_id: operationId },
    ),
};
export function creativeRef(item: CreativeSummary): CreativeTypeRef {
  if (!item.active_version || !item.source_sha256 || item.status !== "enabled")
    throw new Error("该资料尚未启用");
  return {
    id: item.id,
    version: item.active_version,
    source_sha256: item.source_sha256,
  };
}
export function validSelection(
  refs: unknown[] | null | undefined,
  maximumTags = 2,
): boolean {
  return (
    !!refs && refs.every(isCreativeTypeRef) &&
    refs.filter((r) => r.id.startsWith("genre.")).length === 1 &&
    refs.filter((r) => r.id.startsWith("tag.")).length >= 1 &&
    refs.length <= maximumTags + 1 &&
    refs.every((r) => r.id.startsWith("genre.") || r.id.startsWith("tag.")) &&
    new Set(refs.map((r) => r.id)).size === refs.length
  );
}
export function validCreativeInput(input: WorkshopInput): boolean {
  return validSelection(input.creative_types, input.creation_flow === "genre_tags.v1" ? 20 : 2) &&
    (input.creation_flow !== "genre_tags.v2" || input.audience_channel === "female" || input.audience_channel === "male");
}
export async function bindCreativeSelection(
  input: WorkshopInput,
): Promise<WorkshopInput> {
  if (!validCreativeInput(input))
    throw new Error("请选择男频或女频、一个 Genre 和一至两个 Tags。");
  // The server binds exact versions and verifies channel guidance and the input budget.
  return input;
}
