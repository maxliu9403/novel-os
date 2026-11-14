import { vi } from "vitest";
import { api, type LibraryPage, type LibraryChanges, type LibraryQuery, type ProjectSummary } from "../api/client";

export const libraryPage = (items: ProjectSummary[], overrides: Partial<LibraryPage> = {}): LibraryPage => ({
  items, next_cursor: null, total: items.length, filtered_total: items.length,
  collection_revision: "collection-1", changes_cursor: "changes-1", observed_at: "2026-11-14T00:00:00Z",
  catalog_status: "ready", scope: "test-library", ...overrides,
});
export const libraryDelta = (items: ProjectSummary[] = [], overrides: Partial<LibraryChanges> = {}): LibraryChanges => ({
  items, deleted: [], reset_required: false, membership_changed: false, has_more: false,
  changes_cursor: "changes-2", collection_revision: "collection-1", scope: "test-library",
  observed_at: "2026-11-14T00:00:02Z", catalog_status: "ready", ...overrides,
});
export function mockLibrary(items: ProjectSummary[]) {
  return vi.spyOn(api, "library").mockImplementation(async (query: LibraryQuery = {}) => {
    const filtered = items.filter((p) => {
      const search = `${p.title} ${p.author ?? ""} ${p.genre} ${p.task_id ?? ""}`.toLowerCase().includes(query.q?.trim().toLowerCase() ?? "");
      const status = !query.status || query.status === "all" || p.status === query.status
        || (query.status === "writing" && ["running", "pending", "queued", "in_progress"].includes(p.status))
        || (query.status === "attention" && ["failed", "paused", "interrupted", "cancelled"].includes(p.status));
      return search && status && (!query.cover || query.cover === "all" || (p.cover?.status ?? "not_started") === query.cover);
    });
    return libraryPage(filtered, { total: items.length });
  });
}
