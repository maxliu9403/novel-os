import { API_BASE } from "./base";

export type StorySourceKind = "adopted_skeleton" | "outline_fallback" | "state_id_map";

export interface StorySourceRef {
  kind: StorySourceKind;
  path: string;
  section: string | null;
  line_start: number | null;
  line_end: number | null;
}

export interface StoryDependency {
  kind: StorySourceKind;
  path: string;
  sha256: string;
  bytes: number;
  modified_ns: number;
}

export interface StorySource {
  available: boolean;
  path: string;
  sha256: string;
  bytes: number;
  modified_ns: number;
  media_type: "text/markdown";
  dependencies: StoryDependency[];
}

export interface StoryJsonBlock {
  kind: string;
  raw: string;
  data: unknown | null;
  valid: boolean;
  warning: string | null;
  source: StorySourceRef;
}

export interface StorySection {
  id: string;
  number: number | null;
  title: string;
  markdown: string;
  json_blocks: StoryJsonBlock[];
  source: StorySourceRef;
}

export interface StoryChapter {
  number: number;
  title: string;
  summary: string;
  source: StorySourceRef;
}

export interface StoryRelationshipText {
  text: string;
  source: StorySourceRef;
}

export interface StoryCharacter {
  id: string;
  name: string;
  role: string;
  description: string;
  relationships: StoryRelationshipText[];
  source: StorySourceRef;
}

export interface StoryRelationshipEdge {
  id: string;
  source_character_id: string;
  target_character_id: string;
  label: string;
  description: string;
  source: StorySourceRef;
}

export interface StoryNarrative {
  title: string;
  text: string;
  source: StorySourceRef;
}

export interface StoryUnstructuredSection {
  kind: "characters" | "relationships" | "chapters";
  title: string;
  text: string;
  source: StorySourceRef;
}

export interface ProjectStory {
  project_id: string;
  chapters: StoryChapter[];
  characters: StoryCharacter[];
  relationship_edges: StoryRelationshipEdge[];
  narrative: StoryNarrative | null;
  sections: StorySection[];
  unstructured_sections: StoryUnstructuredSection[];
  source: StorySource;
  warnings: string[];
}

export class ProjectStoryError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ProjectStoryError";
    this.status = status;
  }
}

async function responseMessage(response: Response, fallback: string) {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (body.detail && typeof body.detail === "object" && "message" in body.detail) {
      return String(body.detail.message);
    }
  } catch {
    // Proxies and the exact-source endpoint may return plain text.
  }
  return fallback;
}

export function projectStorySourceUrl(projectId: string) {
  return `${API_BASE}/api/projects/${encodeURIComponent(projectId)}/story/source`;
}

export const projectStoryApi = {
  async get(projectId: string, signal?: AbortSignal): Promise<ProjectStory> {
    const response = await fetch(
      `${API_BASE}/api/projects/${encodeURIComponent(projectId)}/story`,
      { signal },
    );
    if (!response.ok) {
      throw new ProjectStoryError(
        await responseMessage(response, `故事骨架读取失败（HTTP ${response.status}）`),
        response.status,
      );
    }
    return response.json() as Promise<ProjectStory>;
  },

  async source(projectId: string, signal?: AbortSignal): Promise<string> {
    const response = await fetch(projectStorySourceUrl(projectId), { signal });
    if (!response.ok) {
      throw new ProjectStoryError(
        await responseMessage(response, `骨架原文读取失败（HTTP ${response.status}）`),
        response.status,
      );
    }
    return response.text();
  },
};
