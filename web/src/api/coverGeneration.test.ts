import {afterEach, expect, test, vi} from "vitest";
import {api} from "./client";

afterEach(() => vi.unstubAllGlobals());

test.each([
  {owner: "book-a", path: "/api/projects/book-a/covers/generate"},
  {owner: "workshop:session-a", path: "/api/workshop/sessions/session-a/covers/generate"},
])("sends the explicit batch intent to the $owner endpoint", async ({owner, path}) => {
  const fetch = vi.fn().mockResolvedValue({ok: true, json: async () => ({job_id: "job-a"})});
  vi.stubGlobal("fetch", fetch);
  const direction = {direction_id: "direction-a", approved_direction_sha256: "approved-sha"};
  const requestId = "b7da565a-6123-4bc6-8c10-f16c32f7d909";
  await api.generateCovers(owner, 4, direction, requestId);
  expect(fetch).toHaveBeenCalledTimes(1);
  const [url, options] = fetch.mock.calls[0];
  expect(url).toMatch(new RegExp(`${path}$`));
  expect(options.method).toBe("POST");
  expect(JSON.parse(options.body)).toEqual({count: 4, ...direction, request_id: requestId});
});
