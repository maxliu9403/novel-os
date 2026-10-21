import { afterEach, expect, test, vi } from "vitest";
import { api } from "./client";

afterEach(() => vi.unstubAllGlobals());

test.each([
  ["book/one", "/api/projects/book%2Fone/covers"],
  ["workshop:discussion/one", "/api/workshop/sessions/discussion%2Fone/covers"],
])("%s selection omits quality reasons while preserving revision and stale confirmation", async (owner, path) => {
  const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({})});vi.stubGlobal("fetch",fetcher);
  await api.selectCover(owner,"set/one","image/one",11,2,true);
  expect(fetcher).toHaveBeenCalledExactlyOnceWith(expect.stringContaining(`${path}/set%2Fone/candidates/image%2Fone/select`), {
    method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({expected_revision:11,expected_active_revision:2,confirm_stale:true}),
  });
});
