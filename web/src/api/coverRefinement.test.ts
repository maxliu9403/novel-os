import { afterEach, expect, test, vi } from "vitest";
import { api, ApiError } from "./client";

afterEach(() => vi.unstubAllGlobals());

test.each([
  ["book/one", "/api/projects/book%2Fone/covers/set%2Fone/candidates/image%2Fone/repair-tasks"],
  ["workshop:discussion/one", "/api/workshop/sessions/discussion%2Fone/covers/set%2Fone/candidates/image%2Fone/repair-tasks"],
])("%s refinement request binds the revision and prompt to its specific owner and candidate", async (owner, path) => {
  const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({task_id:"refinement"})});vi.stubGlobal("fetch",fetcher);
  await api.startCoverRepair(owner,"set/one","image/one",11,"保留人物\n柔和光线");
  expect(fetcher).toHaveBeenCalledExactlyOnceWith(expect.stringContaining(path), {
    method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({expected_revision:11,refinement_prompt:"保留人物\n柔和光线"}),
  });
});

test("automatic repair omits refinement instructions and keeps its existing contract", async () => {
  const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({})});vi.stubGlobal("fetch",fetcher);
  await api.startCoverRepair("book","set","image",11);
  expect(fetcher).toHaveBeenCalledWith(expect.any(String),expect.objectContaining({body:JSON.stringify({expected_revision:11})}));
});

test.each([
  ["book/one", "/api/projects/book%2Fone/covers"],
  ["workshop:discussion/one", "/api/workshop/sessions/discussion%2Fone/covers"],
])("%s resolves exact historical refinement and retries only its task without approval", async (owner, path) => {
  const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({task_id:"retry-proposal"})});vi.stubGlobal("fetch",fetcher);
  await api.coverRepairTaskForCandidate(owner,"set/one","image/one");
  expect(fetcher).toHaveBeenLastCalledWith(expect.stringContaining(`${path}/repair-tasks/for-candidate?cover_set_id=set%2Fone&candidate_id=image%2Fone`));
  await api.retryCoverRefinement(owner,"task/one");
  expect(fetcher).toHaveBeenLastCalledWith(expect.stringContaining(`${path}/repair-tasks/task%2Fone/retry`),{method:"POST",headers:undefined,body:undefined});
  expect(fetcher).toHaveBeenCalledTimes(2);
});

test("retry retains a readable structured conflict and its replan context", async () => {
  const detail={code:"cover_refinement_replan_required",message:"来源已变化，请重新规划。",replan_context:{parent_cover_set_id:"original",parent_candidate_id:"image",expected_revision:3,refinement_prompt:"保留人物"}};
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:false,status:409,json:async()=>({detail})}));
  const failure=await api.retryCoverRefinement("book","task").catch(error=>error);
  expect(failure).toBeInstanceOf(ApiError);expect(failure.message).toBe(detail.message);expect(failure.status).toBe(409);expect(failure.detail).toEqual(detail);
});
