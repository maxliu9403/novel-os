import type { SourceSelection } from "../api/sourceBooks";
export type SourceDraft = {
  source_id: string;
  mode: SourceSelection["mode"] | "original";
  use_cover: boolean;
  analysis_sha256?: string;
};
const KEY = "novelos-source-draft-v1";
export function readSourceDraft(): SourceDraft | undefined {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (
      v &&
      typeof v.source_id === "string" &&
      ["original", "source_development", "reference_creation", "original_rewrite"].includes(v.mode) &&
      typeof v.use_cover === "boolean"
    )
      return v;
  } catch {
    /* Storage is optional; server task remains discoverable. */
  }
}
export function saveSourceDraft(value: SourceDraft) {
  try {
    localStorage.setItem(KEY, JSON.stringify(value));
  } catch {
    /* optional */
  }
}
