import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, ApiError, type LibraryPage, type ProjectSummary } from "../api/client";
import { LibraryResource, LIBRARY_ACTIVE_INTERVAL, LIBRARY_IDLE_INTERVAL } from "../lib/libraryResource";
import { libraryPage, libraryDelta } from "./libraryFixtures";

const book: ProjectSummary = { id: "a", title: "A story", genre: "Drama", chapter_count: 100, status: "completed", epoch: 1, library_revision: 1 };
const query = { q: "", status: "all", cover: "all", limit: 20 };
let store: LibraryResource;
const settle = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };
beforeEach(() => {
  store = new LibraryResource();
  vi.spyOn(api, "libraryContext").mockResolvedValue({ scope: "test-library" });
  vi.spyOn(api, "library").mockResolvedValue(libraryPage([book]));
  vi.spyOn(api, "libraryChanges").mockResolvedValue(libraryDelta());
});
afterEach(() => { store.reset(); vi.useRealTimers(); vi.restoreAllMocks(); });

test("deduplicates subscribers, scopes cache and returns stored cards immediately on remount", async () => {
  const old = vi.spyOn(api, "projects");
  const offA = store.subscribe(query, vi.fn());
  const offB = store.subscribe(query, vi.fn());
  await settle();
  expect(api.libraryContext).toHaveBeenCalledTimes(1);
  expect(api.library).toHaveBeenCalledTimes(1);
  expect(store.snapshot(query).items).toEqual([book]);
  offA(); offB();
  store.saveScroll(query, 1400);
  const offC = store.subscribe(query, vi.fn());
  expect(store.snapshot(query).items).toEqual([book]);
  expect(store.scrollPosition(query)).toBe(1400);
  await settle();
  expect(api.library).toHaveBeenCalledTimes(1);
  expect(api.libraryChanges).toHaveBeenCalledTimes(1);
  expect(old).not.toHaveBeenCalled();
  offC();
});

test("server pagination never loads unrequested pages and rejects a stale page cursor explicitly", async () => {
  vi.mocked(api.library).mockResolvedValueOnce(libraryPage([book], { next_cursor: "page-2", total: 1000, filtered_total: 1000 }))
    .mockRejectedValueOnce(new ApiError("cursor expired", 409))
    .mockResolvedValueOnce(libraryPage([{ ...book, title: "Current" }], { total: 1001, filtered_total: 1001 }));
  store.subscribe(query, vi.fn());
  await settle();
  expect(api.library).toHaveBeenCalledTimes(1);
  await store.load(query, true);
  expect(vi.mocked(api.library).mock.calls[1][0]?.cursor).toBe("page-2");
  expect(vi.mocked(api.library).mock.calls[2][0]?.cursor).toBeUndefined();
  expect(store.snapshot(query).items?.[0].title).toBe("Current");
  expect(store.snapshot(query).refreshError).toBeNull();
});

test("a live job change updates a card even with the same persisted summary revision", async () => {
  store.subscribe(query, vi.fn());
  await settle();
  vi.mocked(api.libraryChanges).mockResolvedValueOnce(libraryDelta([{ ...book, status: "running", live_boot_epoch: "boot", live_revision: 2 }]));
  await store.synchronize();
  expect(store.snapshot(query).items?.[0].status).toBe("running");
  expect(api.library).toHaveBeenCalledTimes(1);
  vi.mocked(api.libraryChanges).mockResolvedValueOnce(libraryDelta([{ ...book, status: "paused", live_boot_epoch: "boot", live_revision: 1 }]));
  await store.synchronize();
  expect(store.snapshot(query).items?.[0].status).toBe("running");
});

test("membership changes reconcile filters from server; card-only progress does not refetch the page", async () => {
  store.subscribe({ ...query, status: "writing" }, vi.fn());
  await settle();
  vi.mocked(api.library).mockResolvedValue(libraryPage([], { total: 1 }));
  vi.mocked(api.libraryChanges).mockResolvedValue(libraryDelta([{ ...book, status: "completed" }], { membership_changed: true }));
  await store.synchronize();
  expect(api.library).toHaveBeenCalledTimes(2);
  expect(store.snapshot({ ...query, status: "writing" }).items).toEqual([]);
});

test("delete tombstones and generations prevent old in-flight responses from restoring a removed book", async () => {
  store.subscribe(query, vi.fn());
  await settle();
  let finish!: (page: LibraryPage) => void;
  vi.mocked(api.library).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const pending = store.load(query);
  store.remove(book.id);
  expect(store.snapshot(query).items).toEqual([]);
  finish(libraryPage([book]));
  await pending;
  expect(store.snapshot(query).items).toEqual([]);
  await store.refresh(query);
  expect(store.snapshot(query).items).toEqual([]);
  vi.mocked(api.library).mockResolvedValue(libraryPage([{ ...book, epoch: 2, title: "Recreated" }]));
  await store.refresh(query);
  expect(store.snapshot(query).items?.[0].title).toBe("Recreated");
});

test("successful resume prevents an older request from restoring failure state", async () => {
  vi.mocked(api.library).mockResolvedValue(libraryPage([{ ...book, status: "failed", task_can_resume: true }]));
  store.subscribe(query, vi.fn());
  await settle();
  let finish!: (page: LibraryPage) => void;
  vi.mocked(api.library).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const pending = store.load(query);
  store.patch(book.id, { status: "running", task_can_resume: false });
  finish(libraryPage([{ ...book, status: "failed" }]));
  await pending;
  expect(store.snapshot(query).items?.[0].status).toBe("running");
});

test("keeps successful cards when refresh fails and a restart cursor forces a bounded resync", async () => {
  store.subscribe(query, vi.fn());
  await settle();
  vi.mocked(api.libraryChanges).mockRejectedValueOnce(new Error("connection lost"));
  await store.synchronize();
  expect(store.snapshot(query).items).toEqual([book]);
  expect(store.snapshot(query).refreshError).toBe("connection lost");
  vi.mocked(api.libraryChanges).mockResolvedValueOnce(libraryDelta([], { reset_required: true }));
  await store.synchronize();
  expect(api.library).toHaveBeenCalledTimes(2);
  expect(store.snapshot(query).refreshError).toBeNull();
});

test("scope changes clear cached cards and ignore an older scope response", async () => {
  store.subscribe(query, vi.fn());
  await settle();
  let finish!: (page: LibraryPage) => void;
  vi.mocked(api.library).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const pending = store.load(query);
  vi.mocked(api.libraryContext).mockResolvedValueOnce({ scope: "another-workspace" });
  await store.context();
  expect(store.snapshot(query).items).toBeNull();
  finish(libraryPage([book]));
  await pending;
  expect(store.snapshot(query).items).toBeNull();
  expect(store.cachedNavigation()).toEqual([]);
});

test("idle libraries still discover changes and hidden tabs stop polling", async () => {
  vi.useFakeTimers();
  store.subscribe(query, vi.fn());
  await settle();
  await vi.advanceTimersByTimeAsync(LIBRARY_IDLE_INTERVAL);
  expect(api.libraryChanges).toHaveBeenCalledTimes(1);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  document.dispatchEvent(new Event("visibilitychange"));
  await vi.advanceTimersByTimeAsync(LIBRARY_IDLE_INTERVAL * 3);
  expect(api.libraryChanges).toHaveBeenCalledTimes(1);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  document.dispatchEvent(new Event("visibilitychange"));
  await settle();
  expect(api.libraryChanges).toHaveBeenCalledTimes(2);
});

test("one slow incremental request never overlaps another and failed polls back off", async () => {
  vi.useFakeTimers();
  vi.mocked(api.library).mockResolvedValue(libraryPage([{ ...book, status: "running" }]));
  store.subscribe(query, vi.fn());
  await settle();
  vi.mocked(api.libraryChanges).mockRejectedValue(new Error("offline"));
  await vi.advanceTimersByTimeAsync(LIBRARY_ACTIVE_INTERVAL);
  expect(api.libraryChanges).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(LIBRARY_ACTIVE_INTERVAL);
  expect(api.libraryChanges).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(LIBRARY_ACTIVE_INTERVAL);
  expect(api.libraryChanges).toHaveBeenCalledTimes(2);
});

test("an older page cannot erase a membership event that arrives during its request", async () => {
  store.subscribe(query, vi.fn());
  await settle();
  let finish!: (page: LibraryPage) => void;
  vi.mocked(api.library).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }))
    .mockResolvedValueOnce(libraryPage([{ ...book, title: "New membership" }]));
  const pending = store.load(query);
  vi.mocked(api.libraryChanges).mockResolvedValueOnce(libraryDelta([], { membership_changed: true }));
  await store.synchronize();
  finish(libraryPage([book]));
  await pending;
  expect(store.snapshot(query).items?.[0].title).toBe("New membership");
});

test("incomparable catalog/job versions reconcile instead of acknowledging a permanently lost update", async () => {
  vi.mocked(api.library).mockResolvedValueOnce(libraryPage([{ ...book, library_revision: 2, live_boot_epoch: "boot", live_revision: 2 }]))
    .mockResolvedValueOnce(libraryPage([{ ...book, title: "New title", library_revision: 3, live_boot_epoch: "boot", live_revision: 2 }]));
  store.subscribe(query, vi.fn());
  await settle();
  vi.mocked(api.libraryChanges).mockResolvedValueOnce(libraryDelta([{ ...book, title: "New title", library_revision: 3, live_boot_epoch: "boot", live_revision: 1 }]));
  await store.synchronize();
  expect(api.library).toHaveBeenCalledTimes(2);
  expect(store.snapshot(query).items?.[0].title).toBe("New title");
  expect(store.snapshot(query).items?.[0].live_revision).toBe(2);
});

test("membership reconciliation preserves the loaded page window", async () => {
  const first = Array.from({ length: 20 }, (_, i) => ({ ...book, id: `a-${i}` }));
  const second = Array.from({ length: 20 }, (_, i) => ({ ...book, id: `b-${i}` }));
  vi.mocked(api.library).mockResolvedValueOnce(libraryPage(first, { next_cursor: "page-2", total: 100, filtered_total: 100 }))
    .mockResolvedValueOnce(libraryPage(second, { next_cursor: "page-3", total: 100, filtered_total: 100 }))
    .mockResolvedValueOnce(libraryPage(first, { next_cursor: "new-page-2", total: 101, filtered_total: 101 }))
    .mockResolvedValueOnce(libraryPage(second, { next_cursor: "new-page-3", total: 101, filtered_total: 101 }));
  store.subscribe(query, vi.fn());
  await settle();
  await store.load(query, true);
  expect(store.snapshot(query).items).toHaveLength(40);
  vi.mocked(api.libraryChanges).mockResolvedValueOnce(libraryDelta([], { membership_changed: true }));
  await store.synchronize();
  expect(store.snapshot(query).items).toHaveLength(40);
  expect(store.snapshot(query).nextCursor).toBe("new-page-3");
  expect(vi.mocked(api.library).mock.calls[3][0]?.cursor).toBe("new-page-2");
});

test("changing scope detaches an aborted sync without letting its finally erase the new request", async () => {
  store.subscribe(query, vi.fn());
  await settle();
  let oldFinish!: (delta: Awaited<ReturnType<typeof api.libraryChanges>>) => void;
  vi.mocked(api.libraryChanges).mockImplementationOnce(() => new Promise((resolve) => { oldFinish = resolve; }));
  const oldSync = store.synchronize();
  vi.mocked(api.libraryContext).mockResolvedValueOnce({ scope: "new-scope" });
  await store.context();
  vi.mocked(api.library).mockResolvedValueOnce(libraryPage([{ ...book, title: "Other workspace" }], { scope: "new-scope" }));
  await store.synchronize();
  expect(store.snapshot(query).items?.[0].title).toBe("Other workspace");
  oldFinish(libraryDelta([book]));
  await oldSync;
  expect(store.snapshot(query).items?.[0].title).toBe("Other workspace");
});
