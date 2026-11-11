import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useSourceBook, useSourceBooks } from "./useSourceBooks";
import { sourceBooksApi, type SourceDetail } from "../api/sourceBooks";
vi.mock("../api/sourceBooks", () => ({ sourceBooksApi: { get: vi.fn(), list: vi.fn() } }));
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});
const book: SourceDetail = {
  id: "s",
  title: "Book",
  book_id: "1",
  chapter_count: 5,
  supplement_count: 0,
  cover_url: null,
  warnings: [],
  synopsis: "",
  result: null,
  study: { status: "running", total: 1, completed: 0 },
};

const designing: SourceDetail = {
  ...book,
  study: { status: "complete", total: 1, completed: 1 },
  design: {
    status: "running", active_count: 1, total_runs: 1,
    latest_run_id: "route-run", mode: "original_rewrite", completed: 1, total: 3,
  },
};

it("polls design activity across the library and retains it through a failed poll until completion", async () => {
  vi.useFakeTimers();
  const ready: SourceDetail = {
    ...designing,
    design: { ...designing.design!, status: "ready", active_count: 0, completed: 3 },
  };
  vi.mocked(sourceBooksApi.list)
    .mockResolvedValueOnce({ items: [designing] })
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ items: [ready] });
  const { result, unmount } = renderHook(() => useSourceBooks());
  await act(async () => { await Promise.resolve(); });
  expect(result.current.data?.items[0].study.status).toBe("complete");
  expect(result.current.data?.items[0].design?.status).toBe("running");
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(sourceBooksApi.list).toHaveBeenCalledTimes(2);
  expect(result.current.error).toContain("offline");
  expect(result.current.data?.items[0].design?.status).toBe("running");
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(result.current.data?.items[0].design?.status).toBe("ready");
  expect(result.current.error).toBe("");
  await act(async () => { await vi.advanceTimersByTimeAsync(14999); });
  expect(sourceBooksApi.list).toHaveBeenCalledTimes(3);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(sourceBooksApi.list).toHaveBeenCalledTimes(4);
  unmount();
});

it("polls the selected design quickly but treats a paused design as idle", async () => {
  vi.useFakeTimers();
  vi.mocked(sourceBooksApi.get)
    .mockResolvedValueOnce(designing)
    .mockResolvedValue({
      ...designing,
      design: { ...designing.design!, status: "paused", active_count: 0 },
    });
  const { result, unmount } = renderHook(() => useSourceBook("s"));
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(sourceBooksApi.get).toHaveBeenCalledTimes(2);
  expect(result.current.data?.design?.status).toBe("paused");
  await act(async () => { await vi.advanceTimersByTimeAsync(14999); });
  expect(sourceBooksApi.get).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(sourceBooksApi.get).toHaveBeenCalledTimes(3);
  unmount();
});

it("stops design polling in hidden tabs and reconciles immediately when visible again", async () => {
  vi.useFakeTimers();
  const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  vi.mocked(sourceBooksApi.list).mockResolvedValue({ items: [designing] });
  const { unmount } = renderHook(() => useSourceBooks());
  await act(async () => { await Promise.resolve(); });
  visibility.mockReturnValue("hidden");
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(30000);
  });
  expect(sourceBooksApi.list).toHaveBeenCalledTimes(1);
  visibility.mockReturnValue("visible");
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
  expect(sourceBooksApi.list).toHaveBeenCalledTimes(2);
  unmount();
});
it("retries a failed poll, retaining saved state until recovery", async () => {
  vi.useFakeTimers();
  vi.mocked(sourceBooksApi.get)
    .mockResolvedValueOnce(book)
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({
      ...book,
      study: { status: "complete", total: 1, completed: 1 },
    });
  const { result, unmount } = renderHook(() => useSourceBook("s"));
  await act(async () => {
    await Promise.resolve();
  });
  expect(result.current.data?.study.status).toBe("running");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(result.current.error).toContain("offline");
  expect(result.current.data?.study.status).toBe("running");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(result.current.data?.study.status).toBe("complete");
  expect(result.current.error).toBe("");
  unmount();
  await vi.advanceTimersByTimeAsync(30000);
  expect(sourceBooksApi.get).toHaveBeenCalledTimes(3);
});
it("refreshes on focus and hides previous-book data immediately on switch", async () => {
  vi.useFakeTimers();
  vi.mocked(sourceBooksApi.get).mockResolvedValue(book);
  const { result, rerender, unmount } = renderHook(
    ({ id }) => useSourceBook(id),
    { initialProps: { id: "s" } },
  );
  await act(async () => {
    await Promise.resolve();
  });
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
  });
  expect(sourceBooksApi.get).toHaveBeenCalledTimes(2);
  vi.mocked(sourceBooksApi.get).mockImplementation(() => new Promise(() => {}));
  rerender({ id: "other" });
  expect(result.current.data).toBeUndefined();
  unmount();
});
