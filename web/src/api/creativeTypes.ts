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
export type KnowledgeRole = "conflict" | "relationship" | "character" | "world" | "setting" | "mood" | "voice" | "pacing" | "ending";
export interface CreativeTypeRef {
  id: string;
  version: number;
  source_sha256: string;
}
export interface KnowledgeDocument {
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
  genre_ids: string[];
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
  schema: "selection-snapshot.v1";
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
  refs: CreativeTypeRef[] | null | undefined,
): boolean {
  return (
    !!refs &&
    refs.filter((r) => r.id.startsWith("genre.")).length === 1 &&
    refs.filter((r) => r.id.startsWith("tag.")).length >= 1 &&
    refs.length <= 21 &&
    new Set(refs.map((r) => r.id)).size === refs.length
  );
}
export async function bindCreativeSelection(
  input: WorkshopInput,
): Promise<WorkshopInput> {
  if (!validSelection(input.creative_types))
    throw new Error("请选择一个 Genre 和至少一个 Tag。");
  // Server resolves and validates the exact versions, associations and full input budget.
  return input;
}
