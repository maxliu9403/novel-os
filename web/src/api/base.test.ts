import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("API origin across build environments", () => {
  it.each([
    { name: "production without configuration", dev: false, configured: undefined, expected: "" },
    { name: "development without configuration", dev: true, configured: undefined, expected: "http://localhost:8000" },
    { name: "production with an explicit origin", dev: false, configured: "https://api.example.test", expected: "https://api.example.test" },
    { name: "development with an explicit origin", dev: true, configured: "https://api.example.test", expected: "https://api.example.test" },
    { name: "production with explicit same-origin", dev: false, configured: "", expected: "" },
    { name: "development with explicit same-origin", dev: true, configured: "", expected: "" },
  ])("uses the correct address for $name", async ({ dev, configured, expected }) => {
    vi.stubEnv("DEV", dev);
    vi.stubEnv("VITE_API_BASE", configured);
    vi.resetModules();
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("./client");
    const { workshopApi } = await import("./workshop");

    await api.projects();
    await workshopApi.list();

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      `${expected}/api/projects`,
      `${expected}/api/workshop/sessions?q=&limit=20&offset=0`,
    ]);
    expect(api.deliveryPackageUrl("novel-123")).toBe(`${expected}/api/projects/novel-123/deliverables/package`);
    expect(workshopApi.promptUrl("session-123")).toBe(`${expected}/api/workshop/sessions/session-123/prompt`);
  });
});
