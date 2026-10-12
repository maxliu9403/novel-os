import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { api, type CoverRepairTask, type CoverSet } from "../api/client";
import { repairTaskFixture, repairedSetFixture } from "../test/coverRepairFixtures";
import { useCoverRepairTask } from "./useCoverRepairTask";

afterEach(() => {cleanup(); vi.useRealTimers(); vi.restoreAllMocks();});

test("recovers planning into approval without starting or approving a paid operation", async () => {
  vi.useFakeTimers();
  const read = vi.spyOn(api, "coverRepairTask").mockResolvedValueOnce(repairTaskFixture("planning")).mockResolvedValue(repairTaskFixture());
  const start = vi.spyOn(api, "startCoverRepair");
  const approve = vi.spyOn(api, "approveCoverRepair");
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverRepairTask("book", onSets));
  await act(async () => {});
  expect(result.current.busy).toBe(true);
  await act(async () => {await vi.advanceTimersByTimeAsync(1500);});
  expect(read).toHaveBeenLastCalledWith("book", "repair-task-fixture");
  expect(result.current.task?.status).toBe("awaiting_approval");
  expect(result.current.busy).toBe(false);
  expect(start).not.toHaveBeenCalled(); expect(approve).not.toHaveBeenCalled();
});

test.each([0, 1] as const)("only explicitly approves the exact proposal and %i image calls once", async count => {
  const proposed = {...repairTaskFixture(), image_calls: count};
  const read = vi.spyOn(api, "coverRepairTask").mockResolvedValue(proposed);
  const approve = vi.spyOn(api, "approveCoverRepair").mockImplementation(async () => {
    const active = {...proposed, status: "generating" as const};read.mockResolvedValue(active);return active;
  });
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverRepairTask("book", onSets));
  await waitFor(() => expect(result.current.busy).toBe(false));
  expect(approve).not.toHaveBeenCalled();
  await act(async () => {await Promise.all([result.current.approve(), result.current.approve()]);});
  expect(approve).toHaveBeenCalledTimes(1);
  expect(approve).toHaveBeenCalledWith("book", "repair-task-fixture", "proposal-hash", count);
  expect(result.current.busy).toBe(true);
});

test("lost planning response is reconciled by GET and is never posted twice", async () => {
  const read = vi.spyOn(api, "coverRepairTask").mockResolvedValue(null);
  const post = vi.spyOn(api, "startCoverRepair").mockImplementation(async () => {read.mockResolvedValue(repairTaskFixture("planning"));throw new Error("response lost");});
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverRepairTask("book", onSets));
  await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => {await expect(result.current.start("parent-set", "candidate-1", 8)).rejects.toThrow("response lost");});
  await waitFor(() => expect(result.current.task?.status).toBe("planning"));
  expect(post).toHaveBeenCalledTimes(1);expect(result.current.busy).toBe(true);
});

test("keeps confirmed completion when its exact repaired batch cannot be loaded", async () => {
  vi.useFakeTimers();
  const completed = {...repairTaskFixture("completed"), cover_set_id: "repaired-set"};
  vi.spyOn(api, "coverRepairTask").mockResolvedValue(completed);
  const covers = vi.spyOn(api, "covers").mockResolvedValue([{...repairedSetFixture(), cover_set_id: "unrelated"}]);
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverRepairTask("book", onSets));
  await act(async () => {});
  expect(result.current.task?.status).toBe("completed");expect(result.current.busy).toBe(true);
  expect(result.current.error).toContain("候选批次尚未同步");expect(onSets).not.toHaveBeenCalled();
  covers.mockResolvedValue([repairedSetFixture()]);
  await act(async () => {await vi.advanceTimersByTimeAsync(1500);});
  expect(result.current.busy).toBe(false);expect(onSets).toHaveBeenCalledWith([repairedSetFixture()], completed, false);
});

test.each(["failed", "interrupted"] as const)("restores %s without automatic model calls", async status => {
  vi.spyOn(api, "coverRepairTask").mockResolvedValue({...repairTaskFixture(status), error: "saved reason"});
  const post = vi.spyOn(api, "startCoverRepair");const approve = vi.spyOn(api, "approveCoverRepair");
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverRepairTask("book", onSets));
  await waitFor(() => expect(result.current.busy).toBe(false));
  expect(result.current.task?.error).toBe("saved reason");expect(post).not.toHaveBeenCalled();expect(approve).not.toHaveBeenCalled();
});

test("unknown offline state locks new operations until a successful query", async () => {
  vi.useFakeTimers();
  const read = vi.spyOn(api, "coverRepairTask").mockRejectedValue(new Error("offline"));
  const post = vi.spyOn(api, "startCoverRepair");
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverRepairTask("book", onSets));
  await act(async () => {});
  expect(result.current.error).toBe("offline");expect(result.current.syncedAt).toBeNull();
  await act(async () => {await result.current.start("set", "candidate", 1);});
  expect(post).not.toHaveBeenCalled();
  read.mockResolvedValue(null);
  await act(async () => {await vi.advanceTimersByTimeAsync(1500);});
  expect(result.current.busy).toBe(false);
});

test("old owner POST response cannot install a task on a different owner", async () => {
  vi.spyOn(api, "coverRepairTask").mockResolvedValue(null);
  let finish!: (value: CoverRepairTask) => void;
  vi.spyOn(api, "startCoverRepair").mockReturnValue(new Promise(resolve => {finish = resolve;}));
  const onSets = vi.fn();
  const {result, rerender} = renderHook(({owner}) => useCoverRepairTask(owner, onSets), {initialProps:{owner:"a"}});
  await waitFor(() => expect(result.current.busy).toBe(false));
  let pending!: Promise<CoverRepairTask | undefined>;
  act(() => {pending = result.current.start("set", "candidate", 1);});
  rerender({owner:"b"});await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => {finish(repairTaskFixture());await pending;});
  expect(result.current.task).toBeNull();expect(onSets).not.toHaveBeenCalled();
});

test("old owner image response cannot replace the new owner's list", async () => {
  const read = vi.spyOn(api, "coverRepairTask").mockResolvedValue({...repairTaskFixture("completed"),cover_set_id:"repaired-set"});
  let finish!: (value: CoverSet[]) => void;
  vi.spyOn(api, "covers").mockReturnValue(new Promise(resolve => {finish = resolve;}));
  const onSets = vi.fn();
  const {result, rerender} = renderHook(({owner}) => useCoverRepairTask(owner, onSets), {initialProps:{owner:"a"}});
  await waitFor(() => expect(result.current.task?.status).toBe("completed"));
  read.mockResolvedValue(null);rerender({owner:"b"});await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => {finish([repairedSetFixture()]);});
  expect(result.current.task).toBeNull();expect(onSets).not.toHaveBeenCalled();
});

test("refinement submits the chosen image and prompt once and recovers a lost response without replay", async () => {
  const task={...repairTaskFixture("planning"),kind:"refinement" as const,refinement_prompt:"保留人物，让光线更柔和。"};
  const read=vi.spyOn(api,"coverRepairTask").mockResolvedValue(null);
  const post=vi.spyOn(api,"startCoverRepair").mockImplementation(async () => {read.mockResolvedValue(task);throw new Error("response lost");});
  const onSets=vi.fn();const {result}=renderHook(() => useCoverRepairTask("workshop:discussion",onSets));
  await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => {
    const first=result.current.start("parent-set","candidate-1",8,task.refinement_prompt);
    const second=result.current.start("parent-set","candidate-1",8,task.refinement_prompt);
    await expect(first).rejects.toThrow("response lost");await second;
  });
  await waitFor(() => expect(result.current.task?.refinement_prompt).toBe(task.refinement_prompt));
  expect(post).toHaveBeenCalledExactlyOnceWith("workshop:discussion","parent-set","candidate-1",8,task.refinement_prompt);
  expect(result.current.busy).toBe(true);
});

test("refresh restores refinement prompt without creating or approving another task", async () => {
  vi.spyOn(api,"coverRepairTask").mockResolvedValue({...repairTaskFixture(),kind:"refinement",refinement_prompt:"保留构图，减少杂物"});
  const start=vi.spyOn(api,"startCoverRepair");const approve=vi.spyOn(api,"approveCoverRepair");const onSets=vi.fn();
  const first=renderHook(() => useCoverRepairTask("book",onSets));
  await waitFor(() => expect(first.result.current.busy).toBe(false));first.unmount();
  const second=renderHook(() => useCoverRepairTask("book",onSets));
  await waitFor(() => expect(second.result.current.task?.refinement_prompt).toBe("保留构图，减少杂物"));
  expect(start).not.toHaveBeenCalled();expect(approve).not.toHaveBeenCalled();
});

test("retry is explicit, locked against duplicate clicks, and restores its exact proposal after refresh", async () => {
  const failed={...repairTaskFixture("failed"),kind:"refinement" as const,refinement_prompt:"保留人物"};
  const proposed={...failed,task_id:"retry-task",status:"awaiting_approval" as const,retry_of_task_id:failed.task_id,proposal_reused:true};
  const read=vi.spyOn(api,"coverRepairTask").mockResolvedValue(failed);
  let finish!: (task: CoverRepairTask) => void;
  const retry=vi.spyOn(api,"retryCoverRefinement").mockReturnValue(new Promise(resolve=>{finish=resolve;}));
  const approve=vi.spyOn(api,"approveCoverRepair"); const start=vi.spyOn(api,"startCoverRepair"); const onSets=vi.fn();
  const view=renderHook(()=>useCoverRepairTask("book",onSets));
  await waitFor(()=>expect(view.result.current.busy).toBe(false));
  let pending!: Promise<CoverRepairTask|undefined>;
  act(()=>{pending=view.result.current.retry(failed.task_id);void view.result.current.retry(failed.task_id);});
  expect(retry).toHaveBeenCalledExactlyOnceWith("book",failed.task_id);
  read.mockResolvedValue(proposed);
  await act(async()=>{finish(proposed);await pending;});
  await waitFor(()=>expect(view.result.current.busy).toBe(false));
  expect(read).toHaveBeenLastCalledWith("book",proposed.task_id);
  expect(view.result.current.task?.proposal_reused).toBe(true);
  view.unmount();const restored=renderHook(()=>useCoverRepairTask("book",onSets));
  await waitFor(()=>expect(restored.result.current.task?.task_id).toBe(proposed.task_id));
  expect(retry).toHaveBeenCalledTimes(1);expect(start).not.toHaveBeenCalled();expect(approve).not.toHaveBeenCalled();
});

test("lost retry response recovers by GET without duplicating the retry or approving it", async () => {
  const failed={...repairTaskFixture("failed"),kind:"refinement" as const};
  const proposed={...failed,task_id:"retry-task",status:"awaiting_approval" as const,retry_of_task_id:failed.task_id};
  const read=vi.spyOn(api,"coverRepairTask").mockResolvedValue(failed);
  const retry=vi.spyOn(api,"retryCoverRefinement").mockImplementation(async()=>{read.mockResolvedValue(proposed);throw new Error("offline");});
  const approve=vi.spyOn(api,"approveCoverRepair");const onSets=vi.fn();const view=renderHook(()=>useCoverRepairTask("book",onSets));
  await waitFor(()=>expect(view.result.current.busy).toBe(false));
  await act(async()=>{await expect(view.result.current.retry(failed.task_id)).rejects.toThrow("offline");});
  await waitFor(()=>expect(view.result.current.task?.task_id).toBe(proposed.task_id));
  expect(retry).toHaveBeenCalledTimes(1);expect(approve).not.toHaveBeenCalled();
});
