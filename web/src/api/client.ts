const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export interface ProjectSummary {
  id: string; title: string; genre: string; chapter_count: number; status: string;
  author?: string; word_count?: number; drafted_count?: number;
  content_rating?: "general" | "mature"; updated_at?: string | null;
  genres?: string[]; premise?: string;
  target_word_count?: number; session_word_target?: number;
}

export interface ChapterSummary {
  number: number; title: string; status: string; word_count: number; pov: string;
  target_word_count?: number;
}
export interface ChapterDetail extends ChapterSummary {
  outline: string | null; draft: string | null;
}
export interface ProjectDetail {
  id: string; title: string; genre: string; author: string;
  chapter_count: number; status: string; style: Record<string, string>;
  content_rating?: "general" | "mature"; word_count?: number;
  genres?: string[]; premise?: string;
  target_word_count?: number; session_word_target?: number;
}

export interface StudioPreset {
  id: string; label: string; hint: string;
  provider: string; model: string; mature_capable: boolean;
}
export interface StudioLlmStatus {
  configured: boolean;
  provider: string;
  model: string;
  preset: string | null;
  mature_capable: boolean;
  error: string | null;
  presets: StudioPreset[];
  onboarding_completed: boolean;
}

export interface StudioCoverStatus {
  configured: boolean;
  has_api_key: boolean;
  base_url: string;
  model: string;
  size: string;
  quality: "low" | "medium" | "high" | "auto";
  output_format: "png" | "jpeg";
  count: number;
  timeout_seconds: number;
  inherits_base_url: boolean;
  inherits_api_key: boolean;
  director_provider?: string;
  director_model?: string;
  director_base_url?: string;
  director_has_api_key?: boolean;
  director_timeout_seconds?: number;
  director_inherits_writing?: boolean;
  error: string | null;
}

export type ModelCapability = "text_generation" | "image_generation";

export interface ProviderTemplate {
  id: string;
  label: string;
  auth_type: "api_key" | "codex_session" | "none";
  default_base_url: string;
  capabilities: ModelCapability[];
  requires_api_key: boolean;
  model_placeholder: string;
}

export interface ProviderConnection {
  id: string;
  name: string;
  provider: string;
  auth_type: "api_key" | "codex_session" | "none";
  base_url: string;
  image_base_url: string;
  capabilities: ModelCapability[];
  has_api_key: boolean;
  status: "ready" | "invalid" | "unavailable";
  error: string;
  last_tested_at: string;
  last_test_ok: boolean | null;
  last_test_error: string;
  discovered_models: string[];
}

export interface TextModelRoute {
  id: string;
  connection_id: string;
  model: string;
  max_tokens: number;
  inherits_default: boolean;
  effective_connection_id: string;
  effective_connection_name: string;
  effective_model: string;
  effective_source: string;
  configured: boolean;
}

export interface ImageModelProfile {
  id: string;
  connection_id: string;
  connection_name: string;
  model: string;
  size: string;
  quality: "low" | "medium" | "high" | "auto";
  output_format: "jpeg" | "png";
  count: number;
  timeout_seconds: number;
  configured: boolean;
  error: string;
}

export interface StudioModelConfiguration {
  schema_version: number;
  source: "legacy" | "v2";
  templates: ProviderTemplate[];
  connections: ProviderConnection[];
  text_routes: TextModelRoute[];
  image_profiles: { cover: ImageModelProfile };
}

export interface ProviderConnectionInput {
  name: string;
  provider: string;
  auth_type?: string;
  base_url?: string;
  image_base_url?: string;
  capabilities?: ModelCapability[];
  secret_action?: "keep" | "replace" | "clear";
  api_key?: string;
}

export interface ProviderTestResult {
  ok: boolean;
  status: string;
  message: string;
  models: string[];
  tested_at: string;
}

export interface ImageTestResult {
  ok: boolean;
  data_url: string;
  width: number;
  height: number;
  model: string;
  request_id: string;
}

export interface TextTestResult {
  route_id: string;
  provider: string;
  model: string;
  reply: string;
  duration_ms: number;
}

export interface CoverConcept {
  concept_id: string;
  visual_strategy: string;
  focal_scene: string;
  composition: string;
  palette: string;
  secondary_signal: string;
  title_treatment: string;
  generation_prompt?: string;
}

export interface CoverCandidate {
  candidate_id: string;
  concept_id: string;
  status: "pending" | "ready" | "failed" | "rejected" | "selected";
  url: string | null;
  relative_path: string;
  media_id: string;
  sha256: string;
  width: number;
  height: number;
  content_type: string;
  error: string;
  prompt_revision?: number;
  attempt_history?: Array<Record<string, unknown>>;
  quality_report?: {
    status: "blocked" | "human_review_required" | "recommended_for_human_review";
    blockers: string[];
    repair_codes: string[];
    evidence: string[];
    [key: string]: unknown;
  } | null;
}

export interface CoverSet {
  cover_set_id: string;
  project_id: string;
  brief: Record<string, unknown> & {
    title?: string;
    target_audience?: string;
    core_conflict?: string;
    decisive_story_node?: string;
  };
  concepts: CoverConcept[];
  candidates: CoverCandidate[];
  source_prompt_sha256: string;
  foundation_sha256: string;
  status: "generating" | "partial" | "ready" | "selected" | "failed" | "stale";
  requested_count: number;
  selected_candidate_id: string;
  revision: number;
  active_revision: number;
  created_at: string;
  updated_at: string;
  brief_schema_version?: number;
  compiler_version?: string;
}

export interface CoverDirectionPlan {
  concept_id: string;
  visual_strategy: string;
  cast: string[];
  focal_character_id: string;
  frozen_action: string;
  blocking: string;
  primary_prop: string;
  visual_hook: {
    hook_type: string;
    first_glance_subject: string;
    open_question: string;
    reader_promise: string;
    expected_thumbnail_read: string;
  };
}

export interface CoverDirection {
  direction_id: string;
  schema_version: number;
  director_model: string;
  profile_version: string;
  brief_sha256: string;
  direction_sha256: string;
  created_at?: string;
  status: "awaiting_approval" | "approved" | "stale" | "rejected";
  plans: CoverDirectionPlan[];
  visual_assumptions: Array<{
    field: string; proposed_value: string; reason: string;
    status: "pending_confirmation" | "approved"; critical: boolean;
  }>;
  brief?: Record<string, unknown> & {
    title?: string;
    genre?: string;
    target_audience?: string;
    principal_characters?: Array<Record<string, unknown> & {
      character_id?: string;
      name?: string;
      age?: string | number;
      age_band?: string;
      occupation_and_status?: string;
      lived_environment?: string;
      daily_wardrobe?: string;
    }>;
    lived_environment?: Record<string, unknown>;
  };
}

export interface ContinuityFinding {
  severity: string;
  category: string;
  message: string;
  suggestion?: string;
  chapter?: number | null;
  entity_id?: string | null;
  /** Stable identity of the fact, for marking it intentional. */
  key: string;
}

/** One named appearance in the compile stylesheet (P5.2). Typography only. */
export interface CompileStyle {
  font: string;
  size_pt: number;
  line_height: number;
  align: string;
  bold: boolean;
  italic: boolean;
  small_caps: boolean;
  first_line_indent_em: number;
  space_before_em: number;
  space_after_em: number;
  page_break_before: boolean;
}

export interface StyleSheet {
  styles: Record<string, CompileStyle>;
  scene_break_marker: string;
}

export type CompileFormat = "html" | "markdown" | "docx" | "epub" | "pdf";

/** One chapter's measurable movement — the shape strip's unit (§4.3). */
export interface ChapterActivity {
  number: number;
  title: string;
  pov: string;
  written: boolean;
  plot_advances: number;
  character_development: number;
  emotional_beats: number;
  new_information: number;
  threads_touched: number;
  word_count: number;
  movement: number;
  flat: boolean;
}

export interface StallRun {
  start: number;
  end: number;
  reason: string;
  chapters: number[];
  length: number;
}

export interface BookShapeReport {
  chapters: ChapterActivity[];
  stalls: StallRun[];
}

export interface ContinuityExemption {
  key: string;
  reason: string;
  at: string;
}
export interface ContinuityReport {
  findings: ContinuityFinding[];
  critical: number;
  warning: number;
  info: number;
}

export interface WordFreq {
  word: string;
  count: number;
}

export interface EchoHit {
  word: string;
  count: number;
  close_pairs: number;
}

export interface ProjectStatistics {
  word_count: number;
  chapter_count: number;
  chapters_with_prose: number;
  reading_minutes: number;
  avg_sentence_length: number;
  unique_content_words: number;
  top_words: WordFreq[];
  echoes: EchoHit[];
}

export interface FinalDoc {
  doc: { type: string; content?: unknown[] };
  markdown: string;
  word_count: number;
}

export interface PredictedConsequence {
  message: string;
  kind?: string;
}

export interface ConsequencePreview {
  preview_id: string;
  selection: string;
  instruction: string;
  rewritten: string;
  state_delta: Record<string, unknown>;
  changelog: string[];
  deterministic: ContinuityFinding[];
  predicted: PredictedConsequence[];
  word_count: number;
}

export interface ConsequenceAcceptResult {
  final: FinalDoc;
  changelog: string[];
  continuity: ContinuityReport;
}

export interface StageProvenance {
  produced_by_agent?: string;
  produced_by_model?: string;
  reviewed_by?: string;
  reviewed_at?: string;
  updated_at?: string;
  word_count?: number;
}

export interface ChapterStages {
  number: number; status: string;
  outline: string | null; draft: string | null;
  revised: string | null; final: string | null;
  continuity: Record<string, unknown> | null;
  provenance?: Record<string, StageProvenance>;
}

export interface StageDiff {
  from_stage: string;
  to_stage: string;
  from_words: number;
  to_words: number;
  added_lines: string[];
  removed_lines: string[];
  summary: string;
}

export interface StageReviewResult {
  stage: string;
  decision: string;
  reviewed_by: string;
  reviewed_at: string;
  promoted_final: boolean;
  message: string;
}

export interface FinalResult {
  final: string; word_count: number;
}
export interface JobStatus {
  job_id: string; kind: string;
  status: "running" | "done" | "error"; error: string | null;
  meta?: Record<string, unknown>;
}
export interface SnapshotMeta {
  id: string; label: string; created_at: string; word_count: number; source: string;
}
export interface SnapshotText extends SnapshotMeta { text: string; }
export interface CommentItem {
  id: string; body: string; quote: string; created_at: string; resolved: boolean;
  from_pos?: number | null; to_pos?: number | null; anchor_status?: string;
  persona?: "author" | "editor" | "beta" | string;
}
export interface CharacterSummary {
  id: string; full_name: string; role: string;
  portrait_media_id?: string; portrait_url?: string | null;
}
export interface BinderNode {
  id: string;
  type: string;
  title: string;
  parent_id?: string | null;
  order?: number;
  chapter_number?: number | null;
  synopsis?: string;
  status?: string;
  label?: string;
  pov?: string;
  word_count?: number;
  derived?: {
    tension?: number;
    emotional_intensity?: number;
    pacing?: number;
    source?: string;
    updated_at?: string;
    [key: string]: unknown;
  };
  children?: BinderNode[];
}

// Mirrors CODEX_TYPES in core/state_manager.py, which rejects anything else at
// write time - so a value off the wire is always one of these four.
export type CodexEntryType = "character" | "location" | "worldbuilding" | "item";

/** A candidate Codex entry found in the prose, awaiting confirmation (P2.2). */
export interface CodexProposal {
  name: string;
  entry_type: CodexEntryType;
  mentions: number;
  /** Why it was proposed, in words a writer can judge: "12 mentions, speaks". */
  evidence: string;
  chapters: number[];
  excerpt: string;
}

export interface CodexEntry {
  id: string;
  entry_type: CodexEntryType;
  name: string;
  summary?: string;
  notes?: string;
  tags?: string[];
  portrait_media_id?: string;
  portrait_url?: string | null;
  role?: string;
  fields?: Record<string, unknown>;
}

export interface RelationshipEdge {
  id: string;
  source_id: string;
  target_id: string;
  label: string;
  kind?: string;
  strength?: number;
  status?: string;
  since_chapter?: number;
  notes?: string;
  directed?: boolean;
  source_name?: string;
  target_name?: string;
}

export interface SearchHit {
  kind: string;
  id: string;
  label: string;
  subtitle?: string;
  chapter?: number | null;
  score?: number;
}

export interface Collection {
  id: string;
  name: string;
  query: string;
  kinds?: string[];
  notes?: string;
}

/** What an image is for. Lets Codex/research/manuscript views filter server-side. */
export type MediaKind = "general" | "portrait" | "location" | "research" | "inline" | "cover";

export interface MediaItem {
  id: string; project_id: string; filename: string; content_type: string;
  size: number; width: number; height: number;
  kind: MediaKind; alt: string; url: string; created_at: string;
}

async function get<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE}${path}`);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json() as Promise<T>;
}

async function send<T>(path: string, method: "POST" | "PUT" | "PATCH", body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const j = await resp.json();
      if (j?.detail) detail = j.detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return resp.json() as Promise<T>;
}

async function del(path: string): Promise<void> {
  const resp = await fetch(`${BASE}${path}`, { method: "DELETE" });
  if (!resp.ok && resp.status !== 204) throw new Error(`${resp.status} ${resp.statusText}`);
}

// Multipart upload. The browser must set its own Content-Type (it has to append
// the multipart boundary), so no headers are passed here.
async function upload<T>(path: string, form: FormData): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, { method: "POST", body: form });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const j = await resp.json();
      if (j?.detail) detail = j.detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  projects: () => get<ProjectSummary[]>("/api/projects"),
  project: (id: string) => get<ProjectDetail>(`/api/projects/${id}`),
  updateProject: (id: string, body: {
    content_rating?: string; title?: string; genre?: string;
    genres?: string[]; premise?: string;
    target_word_count?: number; session_word_target?: number;
  }) =>
    send<ProjectDetail>(`/api/projects/${id}`, "PATCH", body),
  studioLlm: () => get<StudioLlmStatus>("/api/studio/llm"),
  updateStudioLlm: (body: {
    preset?: string; provider?: string; model?: string;
    api_key?: string; base_url?: string; onboarding_completed?: boolean;
  }) => send<StudioLlmStatus>("/api/studio/llm", "PUT", body),
  studioCover: () => get<StudioCoverStatus>("/api/studio/cover"),
  updateStudioCover: (body: {
    base_url?: string; api_key?: string; model?: string; size?: string;
    quality?: string; output_format?: string; count?: number;
    timeout_seconds?: number;
    director_provider?: string; director_model?: string;
    director_base_url?: string; director_api_key?: string;
    director_timeout_seconds?: number;
  }) => send<StudioCoverStatus>("/api/studio/cover", "PUT", body),
  studioModels: () => get<StudioModelConfiguration>("/api/studio/models"),
  createProvider: (body: ProviderConnectionInput) =>
    send<ProviderConnection>("/api/studio/providers", "POST", body),
  updateProvider: (id: string, body: ProviderConnectionInput) =>
    send<ProviderConnection>(`/api/studio/providers/${encodeURIComponent(id)}`, "PATCH", body),
  deleteProvider: (id: string) =>
    del(`/api/studio/providers/${encodeURIComponent(id)}`),
  testProvider: (id: string) =>
    send<ProviderTestResult>(`/api/studio/providers/${encodeURIComponent(id)}/test`, "POST"),
  updateTextRoutes: (routes: Array<Pick<TextModelRoute, "id" | "connection_id" | "model" | "max_tokens" | "inherits_default">>) =>
    send<TextModelRoute[]>("/api/studio/model-routes", "PUT", { routes }),
  testTextRoute: (routeId: string, prompt: string) =>
    send<JobStatus>(`/api/studio/model-routes/${encodeURIComponent(routeId)}/test`, "POST", { prompt }),
  updateCoverProfile: (body: {
    connection_id: string; model: string; size: string;
    quality: ImageModelProfile["quality"];
    output_format: ImageModelProfile["output_format"];
    count: number; timeout_seconds: number;
  }) => send<ImageModelProfile>("/api/studio/image-profiles/cover", "PUT", body),
  testCoverProfile: () =>
    send<JobStatus>("/api/studio/image-profiles/cover/test", "POST"),
  covers: (id: string) => get<CoverSet[]>(`/api/projects/${id}/covers`),
  cover: (id: string, coverSetId: string) =>
    get<CoverSet>(`/api/projects/${id}/covers/${encodeURIComponent(coverSetId)}`),
  coverQuality: (id: string, coverSetId: string) =>
    get<Record<string, unknown>>(
      `/api/projects/${id}/covers/${encodeURIComponent(coverSetId)}/quality`,
    ),
  generateCovers: (id: string, count?: number, direction?: {
    direction_id: string;
    approved_direction_sha256: string;
  }) =>
    send<JobStatus>(`/api/projects/${id}/covers/generate`, "POST", direction
      ? { count, ...direction }
      : { count }),
  coverDirections: (id: string) =>
    get<CoverDirection[]>(`/api/projects/${id}/covers/directions`),
  createCoverDirection: (id: string, count = 4) =>
    send<CoverDirection>(`/api/projects/${id}/covers/directions`, "POST", { count }),
  approveCoverDirection: (
    id: string, directionId: string, briefSha256: string, directionSha256: string,
  ) => send<CoverDirection>(
    `/api/projects/${id}/covers/directions/${encodeURIComponent(directionId)}/approve`,
    "POST",
    { expected_brief_sha256: briefSha256, approved_direction_sha256: directionSha256 },
  ),
  selectCover: (
    id: string, coverSetId: string, candidateId: string,
    expectedRevision: number, expectedActiveRevision: number, confirmStale = false,
  ) => send<CoverSet>(
    `/api/projects/${id}/covers/${encodeURIComponent(coverSetId)}/candidates/${encodeURIComponent(candidateId)}/select`,
    "POST",
    {
      expected_revision: expectedRevision,
      expected_active_revision: expectedActiveRevision,
      confirm_stale: confirmStale,
    },
  ),
  rejectCover: (
    id: string, coverSetId: string, candidateId: string, expectedRevision: number,
  ) => send<CoverSet>(
    `/api/projects/${id}/covers/${encodeURIComponent(coverSetId)}/candidates/${encodeURIComponent(candidateId)}/reject`,
    "POST",
    { expected_revision: expectedRevision },
  ),
  retryCover: (
    id: string, coverSetId: string, candidateId: string, expectedRevision: number,
    repairCodes: string[] = [],
  ) => send<JobStatus>(
    `/api/projects/${id}/covers/${encodeURIComponent(coverSetId)}/candidates/${encodeURIComponent(candidateId)}/retry`,
    "POST",
    { expected_revision: expectedRevision, repair_codes: repairCodes },
  ),
  deliveryPackageUrl: (id: string) => `${BASE}/api/projects/${id}/deliverables/package`,
  continuity: (id: string) => get<ContinuityReport>(`/api/projects/${id}/continuity`),
  /** Edit an entry. Send only what changed - absent fields are left alone. */
  updateCodexEntry: (
    id: string, entryId: string,
    changes: Partial<Pick<CodexEntry, "name" | "summary" | "notes" | "role">>,
  ) => send<CodexEntry>(`/api/projects/${id}/codex/${entryId}`, "PATCH", changes),
  bookShape: (id: string) => get<BookShapeReport>(`/api/projects/${id}/shape`),
  styles: (id: string) => get<StyleSheet>(`/api/projects/${id}/styles`),
  saveStyles: (id: string, sheet: StyleSheet) =>
    send<StyleSheet>(`/api/projects/${id}/styles`, "PUT", sheet),
  compileUrl: (id: string, format: CompileFormat = "html") =>
    `${BASE}/api/projects/${id}/compile?format=${format}`,
  exemptions: (id: string) =>
    get<ContinuityExemption[]>(`/api/projects/${id}/continuity/exemptions`),
  exemptFinding: (id: string, key: string, reason: string) =>
    send<ContinuityExemption>(
      `/api/projects/${id}/continuity/exemptions`, "POST", { key, reason },
    ),
  unexemptFinding: (id: string, key: string) =>
    del(`/api/projects/${id}/continuity/exemptions/${encodeURIComponent(key)}`),
  statistics: (id: string) => get<ProjectStatistics>(`/api/projects/${id}/statistics`),
  chapterContinuity: (id: string, n: number) =>
    get<ContinuityReport>(`/api/projects/${id}/chapters/${n}/continuity`),
  chapters: (id: string) => get<ChapterSummary[]>(`/api/projects/${id}/chapters`),
  binder: (id: string) => get<BinderNode[]>(`/api/projects/${id}/binder`),
  moveBinderNode: (
    id: string,
    body: { node_id: string; parent_id?: string | null; index: number },
  ) => send<BinderNode[]>(`/api/projects/${id}/binder/move`, "POST", body),
  patchBinderNode: (
    id: string,
    nodeId: string,
    body: {
      synopsis?: string; title?: string; label?: string;
      status?: string; pov?: string; target_words?: number;
    },
  ) => send<BinderNode[]>(`/api/projects/${id}/binder/${encodeURIComponent(nodeId)}`, "PATCH", body),
  refreshSynopsis: (id: string, n: number) =>
    send<{
      chapter: number; node_id: string; synopsis: string; source: string; model: string;
    }>(`/api/projects/${id}/chapters/${n}/synopsis/refresh`, "POST"),
  refreshOutlinerMetrics: (id: string, chapter?: number) =>
    send<{
      chapters: Array<{
        chapter: number; node_id: string; tension: number;
        emotional_intensity: number; pacing: number; source: string; word_count: number;
      }>;
      source: string;
    }>(
      `/api/projects/${id}/outliner/metrics/refresh${
        chapter != null ? `?chapter=${chapter}` : ""
      }`,
      "POST",
    ),
  chapter: (id: string, n: number) => get<ChapterDetail>(`/api/projects/${id}/chapters/${n}`),
  stages: (id: string, n: number) => get<ChapterStages>(`/api/projects/${id}/chapters/${n}/stages`),
  stageDiff: (id: string, n: number, fromStage: string, toStage: string) =>
    get<StageDiff>(
      `/api/projects/${id}/chapters/${n}/stages/diff?from_stage=${encodeURIComponent(fromStage)}&to_stage=${encodeURIComponent(toStage)}`,
    ),
  reviewStage: (id: string, n: number, stage: string, decision: "accept" | "reject") =>
    send<StageReviewResult>(
      `/api/projects/${id}/chapters/${n}/stages/${stage}/review`,
      "POST",
      { decision },
    ),
  promoteFinal: (id: string, n: number, force = false) =>
    send<FinalResult>(
      `/api/projects/${id}/chapters/${n}/final/promote${force ? "?force=true" : ""}`,
      "POST",
    ),
  saveFinal: (id: string, n: number, text: string) =>
    send<FinalResult>(`/api/projects/${id}/chapters/${n}/final`, "PUT", { text }),
  getFinalDoc: (id: string, n: number) =>
    get<FinalDoc>(`/api/projects/${id}/chapters/${n}/final/doc`),
  saveFinalDoc: (id: string, n: number, doc: FinalDoc["doc"]) =>
    send<FinalDoc>(`/api/projects/${id}/chapters/${n}/final/doc`, "PUT", { doc }),
  createProject: (body: {
    title: string; genre?: string; author: string;
    genres?: string[]; premise?: string;
  }) =>
    send<ProjectSummary>("/api/projects", "POST", body),
  createSampleProject: () => send<ProjectSummary>("/api/projects/sample", "POST"),
  characters: (id: string) => get<CharacterSummary[]>(`/api/projects/${id}/characters`),
  addCharacter: (id: string, name: string, role: string) =>
    send<CharacterSummary[]>(`/api/projects/${id}/characters`, "POST", { name, role }),
  codex: (id: string, entryType?: CodexEntryType | string) =>
    get<CodexEntry[]>(`/api/projects/${id}/codex${entryType ? `?entry_type=${entryType}` : ""}`),
  addCodexEntry: (id: string, body: {
    entry_type: CodexEntryType | string; name: string; summary?: string;
    notes?: string; role?: string; tags?: string[];
  }) => send<CodexEntry[]>(`/api/projects/${id}/codex`, "POST", body),
  codexProposals: (id: string, minMentions = 3, limit = 60) =>
    get<CodexProposal[]>(
      `/api/projects/${id}/codex/proposals?min_mentions=${minMentions}&limit=${limit}`,
    ),
  setPortrait: (id: string, entryId: string, mediaId: string, entryType = "character") =>
    send<CodexEntry>(`/api/projects/${id}/codex/${entryId}/portrait`, "PUT", {
      media_id: mediaId, entry_type: entryType,
    }),
  relationships: (id: string, entryId?: string) =>
    get<RelationshipEdge[]>(`/api/projects/${id}/relationships${entryId ? `?entry_id=${entryId}` : ""}`),
  addRelationship: (id: string, body: {
    source_id: string; target_id: string; label?: string;
    notes?: string; directed?: boolean; since_chapter?: number;
  }) => send<RelationshipEdge[]>(`/api/projects/${id}/relationships`, "POST", body),
  deleteRelationship: (id: string, edgeId: string) =>
    del(`/api/projects/${id}/relationships/${edgeId}`),
  search: (id: string, q: string, limit = 24) =>
    get<SearchHit[]>(`/api/projects/${id}/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  collections: (id: string) => get<Collection[]>(`/api/projects/${id}/collections`),
  addCollection: (id: string, body: { name?: string; query: string; kinds?: string[]; notes?: string }) =>
    send<Collection[]>(`/api/projects/${id}/collections`, "POST", body),
  deleteCollection: (id: string, collectionId: string) =>
    del(`/api/projects/${id}/collections/${collectionId}`),
  collectionResults: (id: string, collectionId: string, limit = 40) =>
    get<SearchHit[]>(`/api/projects/${id}/collections/${collectionId}/results?limit=${limit}`),
  /** Absolutise a root-relative API asset path (e.g. portrait_url). */
  assetUrl: (path: string | null | undefined) =>
    path ? (path.startsWith("http") ? path : `${BASE}${path}`) : null,
  runPhase: (id: string, stage: string, params: Record<string, unknown> = {}) =>
    send<JobStatus>(`/api/projects/${id}/run`, "POST", { stage, params }),
  getJob: (jobId: string) => get<JobStatus>(`/api/jobs/${jobId}`),
  continueParagraph: (id: string, n: number, instruction: string) =>
    send<{ paragraph: string; instruction: string; word_count: number }>(
      `/api/projects/${id}/chapters/${n}/continue`,
      "POST",
      { instruction },
    ),
  previewConsequence: (
    id: string,
    n: number,
    body: {
      selection: string;
      instruction: string;
      before_context?: string;
      after_context?: string;
    },
  ) =>
    send<ConsequencePreview>(
      `/api/projects/${id}/chapters/${n}/consequence/preview`,
      "POST",
      body,
    ),
  acceptConsequence: (
    id: string,
    n: number,
    body: {
      preview_id: string;
      rewritten: string;
      doc: { type: string; content?: unknown[] };
      state_delta?: Record<string, unknown>;
    },
  ) =>
    send<ConsequenceAcceptResult>(
      `/api/projects/${id}/chapters/${n}/consequence/accept`,
      "POST",
      body,
    ),
  exportUrl: (id: string) => `${BASE}/api/projects/${id}/export`,
  // snapshots
  snapshots: (id: string, n: number) =>
    get<SnapshotMeta[]>(`/api/projects/${id}/chapters/${n}/snapshots`),
  createSnapshot: (id: string, n: number, label: string) =>
    send<SnapshotMeta>(`/api/projects/${id}/chapters/${n}/snapshots`, "POST", { label }),
  getSnapshot: (id: string, n: number, sid: string) =>
    get<SnapshotText>(`/api/projects/${id}/chapters/${n}/snapshots/${sid}`),
  restoreSnapshot: (id: string, n: number, sid: string) =>
    send<FinalResult>(`/api/projects/${id}/chapters/${n}/snapshots/${sid}/restore`, "POST"),
  deleteSnapshot: (id: string, n: number, sid: string) =>
    del(`/api/projects/${id}/chapters/${n}/snapshots/${sid}`),
  // comments
  comments: (id: string, n: number) =>
    get<CommentItem[]>(`/api/projects/${id}/chapters/${n}/comments`),
  addComment: (
    id: string, n: number, body: string, quote: string,
    from_pos?: number | null, to_pos?: number | null,
    persona: string = "author",
  ) =>
    send<CommentItem>(`/api/projects/${id}/chapters/${n}/comments`, "POST", {
      body, quote, from_pos: from_pos ?? null, to_pos: to_pos ?? null, persona,
    }),
  updateComment: (id: string, n: number, cid: string, resolved: boolean) =>
    send<CommentItem>(`/api/projects/${id}/chapters/${n}/comments/${cid}`, "PATCH", { resolved }),
  deleteComment: (id: string, n: number, cid: string) =>
    del(`/api/projects/${id}/chapters/${n}/comments/${cid}`),
  // media
  media: (id: string, kind?: MediaKind) =>
    get<MediaItem[]>(`/api/projects/${id}/media${kind ? `?kind=${kind}` : ""}`),
  uploadMedia: (id: string, file: File, kind: MediaKind = "general", alt = "") => {
    const form = new FormData();
    form.append("file", file);
    form.append("kind", kind);
    form.append("alt", alt);
    return upload<MediaItem>(`/api/projects/${id}/media`, form);
  },
  deleteMedia: (id: string, mediaId: string) => del(`/api/projects/${id}/media/${mediaId}`),
  updateMedia: (id: string, mediaId: string, body: { alt?: string; kind?: string }) =>
    send<MediaItem>(`/api/projects/${id}/media/${mediaId}`, "PATCH", body),
  // Media URLs come back root-relative; absolutise for <img src>.
  mediaUrl: (item: MediaItem) => `${BASE}${item.url}`,
};
