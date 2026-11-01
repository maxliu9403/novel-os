import { API_BASE } from "./base";
import type { WorkshopSession } from "./workshop";

export type CreativeStage =
  | "brainstorm"
  | "story_design"
  | "chapter_plan"
  | "draft"
  | "review";
export type Importance = "essential" | "recommended" | "optional";
export interface Guidance {
  id: string;
  text: string;
  importance: Importance;
  stages: CreativeStage[];
}
export interface Mechanism {
  id: string;
  name: string;
  principle: string;
  importance: Importance;
  stages: CreativeStage[];
  trigger: string[];
  scope: string[];
  limits: string[];
  exceptions: string[];
  costs: string[];
  choices: string[];
  payoffs: string[];
}
export interface CreativeProfile {
  schema: "creative-type-profile.v1";
  profile_id: string;
  version: number;
  name: string;
  aliases: string[];
  dimension: string;
  summary: string;
  applicability: {
    readers: string[];
    markets: string[];
    languages: string[];
    lengths: string[];
  };
  reader_promises: Guidance[];
  mechanisms: Mechanism[];
  character_dynamics: Guidance[];
  voice_guidance: Guidance[];
  pitfalls: Guidance[];
  diagnostics: Guidance[];
  combinations: Array<{
    with_profile_id: string;
    opportunities: string[];
    tensions: string[];
  }>;
  publication_mapping: {
    status: "mapped" | "unmapped";
    catalog_version: string;
    primary_genre_ids: string[];
    facet_ids: string[];
  };
}
export interface CreativeTypeRef {
  profile_id: string;
  version: number;
  source_sha256: string;
  role: "unspecified" | "primary" | "supporting" | "atmosphere";
}
export interface CreativeVersion {
  profile: CreativeProfile;
  sha256: string;
  created_at: string;
}
export interface CreativeRecord {
  id: string;
  revision: number;
  status: "enabled" | "draft" | "disabled";
  origin: string;
  coverage: string;
  updated_at: string;
  draft: CreativeProfile;
  active_version: number | null;
  versions: CreativeVersion[];
}
export interface CreativeSummary extends Omit<
  CreativeRecord,
  "draft" | "versions"
> {
  name: string;
  summary: string;
  dimension: string;
  aliases: string[];
  mapping_status: string;
  publication_mapping?: CreativeProfile['publication_mapping'];
  retired?: boolean;
  source_sha256: string | null;
  has_draft_changes: boolean;
}
export interface CreativeLibrary {
  items: CreativeSummary[];
  dimensions: Record<string, string>;
  publication_catalog: {
    catalog_version: string;
    axes: Record<string, Array<{ id: string; labels: Record<string, string> }>>;
    combination_rules?: { primary_genre_count: number; secondary_genre_max: number; story_type_max: number; tone_max: number; setting_max: number; mutually_exclusive_story_types: string[][] };
  };
}
export interface CreativePreview {
  context: {
    schema: string;
    stage: CreativeStage;
    profiles: Array<{
      profile_id: string;
      version: number;
      source_sha256: string;
      role: string;
      content: Omit<
        CreativeProfile,
        "schema" | "profile_id" | "version" | "aliases" | "publication_mapping"
      >;
    }>;
  };
  sha256: string;
  bytes: number;
  preview_only: boolean;
}
export interface TypeSnapshot {
  schema: string;
  snapshot_sha256: string;
  profiles: Array<{
    profile: CreativeProfile;
    source_sha256: string;
    role: CreativeTypeRef["role"];
  }>;
}
export interface SessionTypeContext {
  snapshot: TypeSnapshot | null;
  contexts: Record<string, CreativePreview["context"]>;
  context_sha256: Record<string, string>;
  latest: CreativeSummary[];
  revision: number;
  last_request: null | {
    revision: number;
    submitted_at: string;
    user_sha256: string;
    context_sha256: string;
    stage: string;
    status: string;
  };
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
    let message = `请求失败（HTTP ${response.status}）`;
    try {
      const result = await response.json();
      if (typeof result.detail === "string") message = result.detail;
    } catch {
      /* preserve HTTP status */
    }
    throw new CreativeApiError(message, response.status);
  }
  return response.json() as Promise<T>;
}
const base = "/api/creative-types";
export function creativeRef(
  item: CreativeSummary,
  role: CreativeTypeRef["role"] = "unspecified",
): CreativeTypeRef {
  if (!item.active_version || !item.source_sha256)
    throw new Error("此类型尚无启用版本");
  return {
    profile_id: item.id,
    version: item.active_version,
    source_sha256: item.source_sha256,
    role,
  };
}
export const creativeTypesApi = {
  list: () => request<CreativeLibrary>(base),
  get: (id: string) =>
    request<CreativeRecord>(`${base}/${encodeURIComponent(id)}`),
  create: (operation_id: string, profile?: CreativeProfile) =>
    request<CreativeRecord>(base, { operation_id, profile }),
  mutate: (
    id: string,
    action: "draft" | "activate" | "disable",
    revision: number,
    operation_id: string,
    profile?: CreativeProfile,
  ) =>
    request<CreativeRecord>(`${base}/${encodeURIComponent(id)}/${action}`, {
      revision,
      operation_id,
      profile,
    }),
  preview: (body: {
    profile?: CreativeProfile;
    refs?: CreativeTypeRef[];
    stage: CreativeStage;
  }) => request<CreativePreview>(`${base}/preview`, body),
  sessionContext: (id: string) =>
    request<SessionTypeContext>(
      `/api/workshop/sessions/${encodeURIComponent(id)}/type-context`,
    ),
  adoptSession: (
    id: string,
    revision: number,
    refs: CreativeTypeRef[],
    operation_id: string,
  ) =>
    request<WorkshopSession>(
      `/api/workshop/sessions/${encodeURIComponent(id)}/type-snapshot`,
      { revision, refs, operation_id },
    ),
};

export function matchesCreativeLabel(item: CreativeSummary, value: string) {
  return [item.name, ...item.aliases].some(
    (label) =>
      label.trim().toLocaleLowerCase() === value.trim().toLocaleLowerCase(),
  );
}

export async function bindCreativeSelection<
  T extends { genres: string[]; creative_types?: CreativeTypeRef[] | null },
>(input: T): Promise<T> {
  if (input.creative_types != null) return input;
  const library = await creativeTypesApi.list();
  const refs: CreativeTypeRef[] = [];
  for (const genre of input.genres) {
    const matches = library.items.filter(
      (i) => i.status === "enabled" && matchesCreativeLabel(i, genre),
    );
    if (matches.length > 1)
      throw new Error(`“${genre}”对应多个类型，请在资料库中确认后重新选择。`);
    if (matches[0] && !refs.some((r) => r.profile_id === matches[0].id))
      refs.push(creativeRef(matches[0]));
  }
  return { ...input, creative_types: refs };
}
