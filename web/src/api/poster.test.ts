import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { confirmObservedPosterIntents, confirmPosterIntent, posterApi, posterAssetUrl, posterIntent, type PosterPlan, type PosterTask } from "./poster";

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.unstubAllGlobals());
test("an unconfirmed mutation keeps its request id across a second explicit attempt", () => {
  const first = posterIntent("book", "batch", {plan_hash: "one"});
  expect(posterIntent("book", "batch", {plan_hash: "one"})).toBe(first);
  expect(JSON.parse(sessionStorage.getItem("novel-os:poster-intent:book:batch") || "{}").id).toBe(first);
  confirmPosterIntent("book", "batch", first);
  expect(posterIntent("book", "batch", {plan_hash: "one"})).not.toBe(first);
});
test("changed input receives a different intent without reusing another plan approval", () => {
  const first = posterIntent("book", "plan", {notes: "relationship"});
  expect(posterIntent("book", "plan", {notes: "environment"})).not.toBe(first);
});
test("a late response cannot erase a newer pending request under the same scope", () => {
  const first = posterIntent("race-book", "plan", {notes: "first"});
  const second = posterIntent("race-book", "plan", {notes: "second"});
  confirmPosterIntent("race-book", "plan", first);
  expect(posterIntent("race-book", "plan", {notes: "second"})).toBe(second);
  expect(JSON.parse(sessionStorage.getItem("novel-os:poster-intent:race-book:plan") || "{}").id).toBe(second);
  confirmPosterIntent("race-book", "plan", second);
  expect(posterIntent("race-book", "plan", {notes: "second"})).not.toBe(second);
});
test("request IDs still work when randomUUID is unavailable on an HTTP LAN origin", () => {
  vi.stubGlobal("crypto", {getRandomValues: (bytes: Uint8Array) => { bytes.fill(42); return bytes; }});
  expect(posterIntent("lan", "plan", {})).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
});
test("generation binds exactly the plan id and hash and selection binds artwork identity", async () => {
  const fetch = vi.fn().mockResolvedValue({ok: true, json: async () => ({})}); vi.stubGlobal("fetch", fetch);
  await posterApi.generate("book / one", "request-a", {plan_id: "plan-a", plan_hash: "sha-a"} as PosterPlan);
  expect(fetch.mock.calls[0][0]).toMatch(/\/api\/projects\/book%20%2F%20one\/poster\/batches$/);
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({request_id: "request-a", plan_id: "plan-a", plan_hash: "sha-a"});
  await posterApi.select("book", "set", "candidate", 7, "image-sha", 9);
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({expected_revision: 7, expected_sha256: "image-sha", expected_active_revision: 9});
});
test("read-only typography preview returns rendered bytes without a generation request", async () => {
  const image = new Blob(["rendered"], {type: "image/png"});
  const fetch = vi.fn().mockResolvedValue({ok: true, blob: async () => image}); vi.stubGlobal("fetch", fetch);
  expect(await posterApi.typographyPreview("book", "set", "candidate", {position: "top"})).toBe(image);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][0]).toMatch(/typography-preview$/);
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({layout: {position: "top"}});
});
test("delivery recovery only calls the local package rebuild endpoint", async () => {
  const fetch = vi.fn().mockResolvedValue({ok: true, json: async () => ({delivery: {status: "ready"}})}); vi.stubGlobal("fetch", fetch);
  expect(await posterApi.rebuildDelivery("book / one")).toEqual({delivery: {status: "ready"}});
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][0]).toMatch(/\/api\/projects\/book%20%2F%20one\/deliverables\/package\/rebuild$/);
  expect(fetch.mock.calls[0][1].method).toBe("POST");
  expect(fetch.mock.calls[0][1].body).toBeUndefined();
});
test("asset URLs disallow executable and arbitrary relative URLs", () => {
  expect(posterAssetUrl("javascript:alert(1)")).toBeUndefined();
  expect(posterAssetUrl("outputs/private.png")).toBeUndefined();
  expect(posterAssetUrl("/api/projects/book/media/id/raw")).toContain("/api/projects/book/media/id/raw");
});

test("a recovered task alias confirms the pending receipt so the next explicit batch is fresh", () => {
  const id = posterIntent("alias-book", "batch", {plan_hash: "one"});
  confirmObservedPosterIntents("alias-book", [{request_id: "canonical", request_aliases: [id]} as PosterTask]);
  expect(posterIntent("alias-book", "batch", {plan_hash: "one"})).not.toBe(id);
});
