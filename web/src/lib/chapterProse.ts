import type { ChapterSummary } from "../api/client";

const PROSE_STATUSES = new Set([
  "draft", "drafted", "editing", "edited", "validated", "revised",
  "final", "approved", "complete", "completed",
]);

export function chapterHasProse(chapter: ChapterSummary) {
  return chapter.word_count > 0 || PROSE_STATUSES.has(chapter.status);
}
