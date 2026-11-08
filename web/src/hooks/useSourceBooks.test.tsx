import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useSourceBook } from "./useSourceBooks";
import { sourceBooksApi, type SourceDetail } from "../api/sourceBooks";
vi.mock("../api/sourceBooks", () => ({ sourceBooksApi: { get: vi.fn() } }));
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
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
