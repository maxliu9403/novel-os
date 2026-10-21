import { afterEach, expect, test, vi } from "vitest";
import { workshopApi } from "./workshop";

afterEach(() => vi.unstubAllGlobals());

test.each(["discuss", "deliver"] as const)("sends the explicit %s workshop turn intent", async (intent) => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ job_id: "turn-job" }) });
  vi.stubGlobal("fetch", fetch);

  await workshopApi.turn("discussion / one", "作者的本轮要求", 7, intent);

  expect(fetch).toHaveBeenCalledTimes(1);
  const [url, options] = fetch.mock.calls[0];
  expect(url).toMatch(/\/api\/workshop\/sessions\/discussion%20%2F%20one\/turns$/);
  expect(options.method).toBe("POST");
  expect(JSON.parse(options.body)).toEqual({ message: "作者的本轮要求", revision: 7, intent });
});
