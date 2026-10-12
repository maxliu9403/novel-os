import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { api, type CoverCandidate, type CoverRepairTask } from "../api/client";
import { repairTaskFixture, repairedSetFixture } from "../test/coverRepairFixtures";
import CoverRefinementRetryActions from "./CoverRefinementRetryActions";

afterEach(()=>{cleanup();vi.restoreAllMocks();});
const original: CoverCandidate={candidate_id:"candidate-1",concept_id:"concept-1",status:"ready",url:"/original.png",relative_path:"original.png",media_id:"original",sha256:"sha",width:1024,height:1536,content_type:"image/png",error:""};
const task: CoverRepairTask={...repairTaskFixture("failed"),kind:"refinement",cover_set_id:"failed-set",result_candidate_id:"failed-candidate",refinement_prompt:"保留人物"};
const parent={...repairedSetFixture(),cover_set_id:task.parent_cover_set_id,candidates:[original]};
const base={owner:"book",setId:"failed-set",candidateId:"failed-candidate",number:1,knownTask:null,sets:[parent],disabled:false};

test("a historical card resolves its exact failed task, independently of the latest task", async()=>{
  const lookup=vi.spyOn(api,"coverRepairTaskForCandidate").mockResolvedValue(task);const retry=vi.fn();const replan=vi.fn();const user=userEvent.setup();
  render(<CoverRefinementRetryActions {...base} knownTask={{...repairTaskFixture("completed"),task_id:"unrelated"}} onRetry={retry} onReplan={replan}/>);
  await waitFor(()=>expect(screen.getByRole("button",{name:"重试精修候选图 1"})).toBeEnabled());
  expect(lookup).toHaveBeenCalledExactlyOnceWith("book","failed-set","failed-candidate");expect(retry).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button",{name:"重试精修候选图 1"}));expect(retry).toHaveBeenCalledExactlyOnceWith(task);
  await user.click(screen.getByRole("button",{name:"修改候选图 1 的精修要求并重新规划"}));expect(replan).toHaveBeenCalledExactlyOnceWith(task);
});

test("offline lookup stays disabled and its explicit refresh only performs another GET", async()=>{
  const lookup=vi.spyOn(api,"coverRepairTaskForCandidate").mockRejectedValueOnce(new Error("offline")).mockResolvedValue(task);
  const retry=vi.fn();const user=userEvent.setup();render(<CoverRefinementRetryActions {...base} onRetry={retry} onReplan={vi.fn()}/>);
  expect(await screen.findByText(/暂时无法读取精修任务：offline/)).toBeInTheDocument();expect(screen.getByRole("button",{name:"重试精修候选图 1"})).toBeDisabled();
  await user.click(screen.getByRole("button",{name:"重新查询精修任务"}));
  await waitFor(()=>expect(screen.getByRole("button",{name:"重试精修候选图 1"})).toBeEnabled());
  expect(lookup).toHaveBeenCalledTimes(2);expect(retry).not.toHaveBeenCalled();
});

test.each(["missing", "wrong"] as const)("%s original/task never enables an unsafe retry",async kind=>{
  vi.spyOn(api,"coverRepairTaskForCandidate").mockResolvedValue(kind==="wrong"?{...task,result_candidate_id:"another"}:task);
  render(<CoverRefinementRetryActions {...base} sets={kind==="missing"?[]:[parent]} onRetry={vi.fn()} onReplan={vi.fn()}/>);
  await screen.findByText(kind==="missing"?/原始参考图片暂不可用/:/未找到这张图对应的精修任务/);
  expect(screen.getByRole("button",{name:"重试精修候选图 1"})).toBeDisabled();
});

test("a later lookup cannot install another owner's task",async()=>{
  let finish!:(task:CoverRepairTask)=>void;const lookup=vi.spyOn(api,"coverRepairTaskForCandidate").mockReturnValueOnce(new Promise(resolve=>{finish=resolve;})).mockResolvedValue(null);
  const view=render(<CoverRefinementRetryActions {...base} onRetry={vi.fn()} onReplan={vi.fn()}/>);
  view.rerender(<CoverRefinementRetryActions {...base} owner="another" onRetry={vi.fn()} onReplan={vi.fn()}/>);
  await screen.findByText(/未找到这张图对应/);await act(async()=>finish(task));
  expect(lookup).toHaveBeenCalledTimes(2);expect(screen.getByRole("button",{name:"重试精修候选图 1"})).toBeDisabled();
});

test("an interrupted successor with no output is the next retry target on its older card",async()=>{
  vi.spyOn(api,"coverRepairTaskForCandidate").mockResolvedValue(task);
  const successor={...task,task_id:"retry-b",retry_of_task_id:task.task_id,status:"interrupted" as const,cover_set_id:null,result_candidate_id:null};
  const retry=vi.fn();const user=userEvent.setup();render(<CoverRefinementRetryActions {...base} knownTask={successor} onRetry={retry} onReplan={vi.fn()}/>);
  await waitFor(()=>expect(screen.getByRole("button",{name:"重试精修候选图 1"})).toBeEnabled());
  await user.click(screen.getByRole("button",{name:"重试精修候选图 1"}));expect(retry).toHaveBeenCalledExactlyOnceWith(successor);
});

test("a failed slot retained in a later batch uses only the resolver's verified display binding",async()=>{
  const resolved={...task,lookup_cover_set_id:"later-set",lookup_candidate_id:task.result_candidate_id!};
  const lookup=vi.spyOn(api,"coverRepairTaskForCandidate").mockResolvedValue(resolved);
  const retry=vi.fn();const user=userEvent.setup();render(<CoverRefinementRetryActions {...base} setId="later-set" onRetry={retry} onReplan={vi.fn()}/>);
  await waitFor(()=>expect(screen.getByRole("button",{name:"重试精修候选图 1"})).toBeEnabled());
  expect(lookup).toHaveBeenCalledExactlyOnceWith("book","later-set","failed-candidate");
  await user.click(screen.getByRole("button",{name:"重试精修候选图 1"}));expect(retry).toHaveBeenCalledExactlyOnceWith(resolved);
});

test.each(["missing", "wrong-batch", "wrong-candidate"])("an ancestor without matching %s display binding cannot be used",async invalid=>{
  const response={...task,...(invalid==="missing"?{}:{lookup_cover_set_id:invalid==="wrong-batch"?"unrelated":"later-set",lookup_candidate_id:invalid==="wrong-candidate"?"unrelated":task.result_candidate_id!})};
  vi.spyOn(api,"coverRepairTaskForCandidate").mockResolvedValue(response);
  render(<CoverRefinementRetryActions {...base} setId="later-set" onRetry={vi.fn()} onReplan={vi.fn()}/>);
  await screen.findByText(/未找到这张图对应/);expect(screen.getByRole("button",{name:"重试精修候选图 1"})).toBeDisabled();
});
