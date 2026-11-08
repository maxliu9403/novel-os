import { API_BASE } from "./base";

export interface ProjectCreativeBasis {
  recorded: boolean;
  run_id: string | null;
  audience_channel: "female" | "male" | null;
  source_sha256: string | null;
  handoff_sha256: string | null;
  lock_sha256: string | null;
  selection_sha256: string | null;
  binding: "execution_lock" | "frozen_handoff" | null;
  documents: Array<{ id: string; kind: "genre" | "tag"; name: string; version: number; source_sha256: string }>;
  historical_designs: Array<{
    id: string; title: string; summary: string; status: "accepted" | "revised";
    author_note: string; choice: string; cost: string; payoff: string;
    tag_contributions: Array<{ tag_id: string; tag_name: string; contribution: string }>;
  }>;
  authority_note: string;
  evidence_note: string;
}

export const creativeBasisUrl = (id: string) => `${API_BASE}/api/projects/${encodeURIComponent(id)}/creative-basis`;
export const projectCreativeBasisApi = {
  async get(id: string, signal?: AbortSignal): Promise<ProjectCreativeBasis> {
    const response = await fetch(creativeBasisUrl(id), { signal, cache: "no-store" });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : `创作依据读取失败（${response.status}）`);
    }
    return response.json();
  },
};
