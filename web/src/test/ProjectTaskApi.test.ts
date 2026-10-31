import { afterEach, vi } from "vitest";
import { api, ApiError } from "../api/client";

afterEach(() => vi.unstubAllGlobals());

test("pause uses the exact job id and encoded project/run route", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "running", cancel_requested: true }), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  const result = await api.pauseProjectTask("project a", "run/a", "job-exact");
  const [url, options] = fetch.mock.calls[0];
  expect(url).toMatch(/\/api\/projects\/project%20a\/tasks\/run%2Fa\/pause$/);
  expect(options).toMatchObject({ method: "POST", headers: { "Content-Type": "application/json" } });
  expect(JSON.parse(options.body)).toEqual({ job_id: "job-exact" });
  expect(result).toMatchObject({ status: "running", cancel_requested: true });
});

test("planning upgrade is explicit and the original resume body stays unchanged", async () => {
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ status: "running", job_id: "job-a" }), { status: 200 })));
  vi.stubGlobal("fetch", fetch);
  await api.resumeProjectTask("p", "r");
  expect(fetch.mock.calls[0][1].body).toBeUndefined();
  await api.resumeProjectTask("p", "r", { outline_planning_mode: "staged" });
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ outline_planning_mode: "staged" });
});

test("pause conflicts remain typed errors, never a successful pause result", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "当前任务已切换执行" }), { status: 409 })));
  await expect(api.pauseProjectTask("p", "r", "old-job")).rejects.toMatchObject<ApiError>({
    name: "ApiError", message: "当前任务已切换执行", status: 409,
  });
});
