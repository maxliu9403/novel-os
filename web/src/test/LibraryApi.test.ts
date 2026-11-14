import { afterEach, expect, test, vi } from "vitest";
import { api } from "../api/client";
import { libraryPage } from "./libraryFixtures";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

test("sends bounded query and validators without falling back to the legacy project endpoint", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(libraryPage([])), { status: 200 }))
    .mockResolvedValueOnce(new Response(null, { status: 304 }));
  vi.stubGlobal("fetch", fetcher);
  const first = await api.library({ q: "A & B", status: "writing", limit: 20 });
  expect(first?.items).toEqual([]);
  expect(fetcher.mock.calls[0][0]).toContain("/api/library?q=A+%26+B&status=writing&limit=20");
  expect(await api.library({}, { etag: '"version-1"' })).toBeNull();
  expect(fetcher.mock.calls[1][1].headers).toEqual({ "If-None-Match": '"version-1"' });
});

test("forwards cancellation and enforces the catalog deadline", async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn((_url, options) => new Promise((_, reject) => {
    options.signal.addEventListener("abort", () => reject(options.signal.reason));
  }));
  vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  const request = api.library({}, { signal: controller.signal });
  const rejected = expect(request).rejects.toBeDefined();
  controller.abort();
  await rejected;
  const timed = api.library();
  const deadline = expect(timed).rejects.toThrow("作品库响应超时");
  await vi.advanceTimersByTimeAsync(15_000);
  await deadline;
  expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true);
});
