import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { api, ApiError, type CoverGenerationTask, type CoverSet, type JobStatus } from "../api/client";
import { useCoverGenerationTask } from "./useCoverGenerationTask";

const direction = {direction_id: "direction-a", approved_direction_sha256: "approved-hash"};
const task = (status: CoverGenerationTask["status"] = "running"): CoverGenerationTask => ({
  task_id: "task-a", job_id: "job-a", status, direction_id: "direction-a", count: 4, cover_set_id: null,
  result_status: null, error: status === "failed" ? "Image service failed" : status === "interrupted" ? "Worker restarted" : null,
  created_at: "2026-10-11T10:00:00Z", started_at: "2026-10-11T10:00:01Z", updated_at: "2026-10-11T10:01:00Z", finished_at: null, progress: null,
});
const job: JobStatus = {job_id: "job-a", task_id: "task-a", kind: "cover.generate", status: "running", error: null};
const set: CoverSet = {cover_set_id: "set-a", project_id: "book", revision: 1, active_revision: 1,
  status: "ready", requested_count: 4, brief: {title: "Fixture"}, candidates: [], concepts: [], selected_candidate_id: "",
  source_prompt_sha256: "source", foundation_sha256: "foundation",
  created_at: "2026-10-11T10:00:00Z", updated_at: "2026-10-11T10:01:00Z"};
afterEach(() => {cleanup(); sessionStorage.clear(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals();});

test("recovers an active job and polls its exact task without resubmitting", async () => {
  vi.useFakeTimers();
  const read = vi.spyOn(api, "coverGenerationTask").mockResolvedValueOnce(task()).mockResolvedValue({...task("completed"), cover_set_id: "set-a"});
  vi.spyOn(api, "covers").mockResolvedValue([set]);
  const post = vi.spyOn(api, "generateCovers");
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverGenerationTask("book", onSets));
  await act(async () => {});
  expect(result.current.busy).toBe(true);
  await act(async () => {await result.current.start(4, direction);});
  expect(post).not.toHaveBeenCalled();
  await act(async () => {await vi.advanceTimersByTimeAsync(1500);});
  expect(read).toHaveBeenLastCalledWith("book", "task-a");
  expect(result.current.task?.status).toBe("completed");
  expect(result.current.busy).toBe(false);
  expect(onSets).toHaveBeenCalledWith([set], expect.objectContaining({cover_set_id: "set-a"}), true);
});

test.each(["failed", "interrupted"] as const)("restores %s and its reason without automatic paid retries", async status => {
  vi.spyOn(api, "coverGenerationTask").mockResolvedValue(task(status));
  const post = vi.spyOn(api, "generateCovers");
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverGenerationTask("book", onSets));
  await waitFor(() => expect(result.current.busy).toBe(false));
  expect(result.current.task?.status).toBe(status);
  expect(result.current.task?.error).toBeTruthy();
  expect(post).not.toHaveBeenCalled();
});

test("keeps unknown state locked through offline polling and does not refresh its successful-sync timestamp", async () => {
  vi.useFakeTimers();
  const read = vi.spyOn(api, "coverGenerationTask").mockRejectedValueOnce(new Error("offline")).mockResolvedValue(task());
  const post = vi.spyOn(api, "generateCovers");
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverGenerationTask("book", onSets));
  await act(async () => {});
  expect(result.current.busy).toBe(true);
  expect(result.current.lastSyncedAt).toBeNull();
  expect(result.current.connectionError).toBe("offline");
  await act(async () => {await result.current.start(4, direction); await vi.advanceTimersByTimeAsync(1500);});
  const confirmedAt = result.current.lastSyncedAt;
  read.mockRejectedValueOnce(new Error("disconnected"));
  await act(async () => {await vi.advanceTimersByTimeAsync(1500);});
  expect(result.current.lastSyncedAt).toBe(confirmedAt);
  expect(result.current.task?.status).toBe("running");
  expect(result.current.busy).toBe(true);
  expect(post).not.toHaveBeenCalled();
});

test("keeps confirmed terminal state while saved images are unavailable and reconciles before unlocking", async () => {
  vi.useFakeTimers();
  vi.spyOn(api, "coverGenerationTask").mockResolvedValue({...task("completed"), cover_set_id: "set-a"});
  const covers = vi.spyOn(api, "covers").mockRejectedValue(new Error("saved images unavailable"));
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverGenerationTask("book", onSets));
  await act(async () => {});
  expect(result.current.task?.status).toBe("completed");
  expect(result.current.busy).toBe(true);
  expect(result.current.connectionError).toBe("saved images unavailable");
  expect(result.current.lastSyncedAt).toEqual(expect.any(Number));
  covers.mockResolvedValue([set]);
  await act(async () => {await vi.advanceTimersByTimeAsync(1500);});
  expect(result.current.busy).toBe(false);
  expect(onSets).toHaveBeenCalledWith([set], expect.anything(), false);
});

test("does not adopt unrelated saved sets as the current job result", async () => {
  vi.spyOn(api, "coverGenerationTask").mockResolvedValue({...task("completed"), cover_set_id: "different-set"});
  vi.spyOn(api, "covers").mockResolvedValue([set]);
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverGenerationTask("book", onSets));
  await waitFor(() => expect(result.current.connectionError).toContain("批次尚未同步"));
  expect(result.current.busy).toBe(true);
  expect(onSets).not.toHaveBeenCalled();
});

test("coalesces double submissions and binds the POST task id", async () => {
  const read = vi.spyOn(api, "coverGenerationTask").mockResolvedValue(null);
  const post = vi.spyOn(api, "generateCovers").mockImplementation(async () => {read.mockResolvedValue(task()); return job;});
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverGenerationTask("book", onSets));
  await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => {await Promise.all([result.current.start(4, direction), result.current.start(4, direction)]);});
  expect(post).toHaveBeenCalledTimes(1);
  expect(read).toHaveBeenLastCalledWith("book", "task-a");
  expect(result.current.busy).toBe(true);
});

test("uses cryptographic random bytes when randomUUID is unavailable on a LAN HTTP origin", async () => {
  const originalCrypto = globalThis.crypto;
  const getRandomValues = vi.fn((values: Uint8Array) => originalCrypto.getRandomValues(values));
  vi.stubGlobal("crypto", {getRandomValues});
  const read = vi.spyOn(api, "coverGenerationTask").mockResolvedValue(null);
  const post = vi.spyOn(api, "generateCovers").mockImplementation(async () => {read.mockResolvedValue(task()); return job;});
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverGenerationTask("book", onSets));
  await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => {await result.current.start(4, direction);});
  expect(getRandomValues).toHaveBeenCalledTimes(1);
  expect(post.mock.calls[0][3]).toMatch(/^[a-f0-9]{32}$/);
});

test("recovers an accepted POST with a lost response through GET only", async () => {
  const read = vi.spyOn(api, "coverGenerationTask").mockResolvedValue(null);
  const post = vi.spyOn(api, "generateCovers").mockImplementation(async () => {read.mockResolvedValue(task()); throw new Error("connection lost");});
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverGenerationTask("book", onSets));
  await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => {await expect(result.current.start(4, direction)).rejects.toThrow("connection lost");});
  await waitFor(() => expect(result.current.task?.status).toBe("running"));
  expect(result.current.busy).toBe(true);
  expect(post).toHaveBeenCalledTimes(1);
});

test("a manually requested batch after failure gets a fresh intent and coalesces rapid clicks", async () => {
  const read = vi.spyOn(api, "coverGenerationTask").mockResolvedValue(task("failed"));
  const post = vi.spyOn(api, "generateCovers").mockImplementation(async (_owner, _count, _direction, requestId) => {
    read.mockResolvedValue({...task("failed"), task_id: `task-${post.mock.calls.length}`, request_id: requestId});
    return {...job, task_id: `task-${post.mock.calls.length}`};
  });
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverGenerationTask("book", onSets));
  await waitFor(() => expect(result.current.busy).toBe(false));
  expect(post).not.toHaveBeenCalled();
  await act(async () => {await Promise.all([result.current.start(4, direction), result.current.start(4, direction)]);});
  await waitFor(() => expect(result.current.busy).toBe(false));
  const first = post.mock.calls[0][3];
  expect(first).toMatch(/^[\da-f-]{36}$/);
  expect(post).toHaveBeenCalledTimes(1);
  await act(async () => {await result.current.start(4, direction);});
  expect(post).toHaveBeenCalledTimes(2);
  expect(post.mock.calls[1][3]).not.toBe(first);
});

test("keeps an ambiguous intent across remount and an unrelated latest result until an explicit retry", async () => {
  const read = vi.spyOn(api, "coverGenerationTask").mockResolvedValue(task("failed"));
  const post = vi.spyOn(api, "generateCovers").mockRejectedValue(new Error("connection lost"));
  const onSets = vi.fn();
  const first = renderHook(() => useCoverGenerationTask("book", onSets));
  await waitFor(() => expect(first.result.current.busy).toBe(false));
  await act(async () => {await expect(first.result.current.start(4, direction)).rejects.toThrow("connection lost");});
  await waitFor(() => expect(first.result.current.busy).toBe(false));
  const requestId = post.mock.calls[0][3];
  first.unmount();
  const second = renderHook(() => useCoverGenerationTask("book", onSets));
  await waitFor(() => expect(second.result.current.busy).toBe(false));
  expect(post).toHaveBeenCalledTimes(1);
  post.mockImplementation(async () => {read.mockResolvedValue({...task(), request_id: requestId}); return job;});
  await act(async () => {await second.result.current.start(4, direction);});
  expect(post).toHaveBeenLastCalledWith("book", 4, direction, requestId);
  expect(second.result.current.busy).toBe(true);
});

test("a confirmed validation rejection does not pin a stale direction intent", async () => {
  vi.spyOn(api, "coverGenerationTask").mockResolvedValue(null);
  const post = vi.spyOn(api, "generateCovers").mockRejectedValue(new ApiError("Direction expired", 409));
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverGenerationTask("book", onSets));
  await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => {await expect(result.current.start(4, direction)).rejects.toThrow("Direction expired");});
  await waitFor(() => expect(result.current.busy).toBe(false));
  const requestId = post.mock.calls[0][3];
  post.mockResolvedValue(job);
  await act(async () => {await result.current.start(4, {...direction, direction_id: "direction-b"});});
  expect(post.mock.calls[1][3]).not.toBe(requestId);
});

test.each(["request_id", "request_aliases"] as const)("a saved task with matching %s resolves lost acceptance before the next paid batch", async field => {
  const read = vi.spyOn(api, "coverGenerationTask").mockResolvedValue(null);
  const post = vi.spyOn(api, "generateCovers").mockImplementation(async (_owner, _count, _direction, requestId) => {
    read.mockResolvedValue({...task("failed"), ...(field === "request_id" ? {request_id: requestId}
      : {request_id: "another-tab-original-intent", request_aliases: [requestId!]})});
    throw new Error("response lost after acceptance");
  });
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverGenerationTask("book", onSets));
  await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => {await expect(result.current.start(4, direction)).rejects.toThrow("response lost");});
  await waitFor(() => expect(result.current.task?.status).toBe("failed"));
  const requestId = post.mock.calls[0][3];
  post.mockResolvedValue(job);
  await act(async () => {await result.current.start(4, direction);});
  expect(post.mock.calls[1][3]).not.toBe(requestId);
});

test("keeps unresolved intents separate when explicitly switching approved direction", async () => {
  vi.spyOn(api, "coverGenerationTask").mockResolvedValue(null);
  const post = vi.spyOn(api, "generateCovers").mockRejectedValue(new Error("offline"));
  const onSets = vi.fn();
  const {result} = renderHook(() => useCoverGenerationTask("book", onSets));
  await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => {await expect(result.current.start(4, direction)).rejects.toThrow("offline");});
  await waitFor(() => expect(result.current.busy).toBe(false));
  const firstId = post.mock.calls[0][3];
  await act(async () => {await expect(result.current.start(4, {...direction, direction_id: "direction-b"})).rejects.toThrow("offline");});
  await waitFor(() => expect(result.current.busy).toBe(false));
  expect(post.mock.calls[1][3]).not.toBe(firstId);
  expect(post).toHaveBeenCalledTimes(2);
  await act(async () => {await expect(result.current.start(4, direction)).rejects.toThrow("offline");});
  expect(post.mock.calls[2][3]).toBe(firstId);
});

test("ignores an old owner's pending POST and clears its local submission lock", async () => {
  const read = vi.spyOn(api, "coverGenerationTask").mockResolvedValue(null);
  let finish!: (value: JobStatus) => void;
  vi.spyOn(api, "generateCovers").mockReturnValue(new Promise(resolve => {finish = resolve;}));
  const onSets = vi.fn();
  const {result, rerender} = renderHook(({owner}) => useCoverGenerationTask(owner, onSets), {initialProps: {owner: "a"}});
  await waitFor(() => expect(result.current.busy).toBe(false));
  let pending!: Promise<void>;
  act(() => {pending = result.current.start(4, direction);});
  rerender({owner: "b"});
  await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => {finish(job); await pending;});
  expect(result.current.task).toBeNull();
  expect(onSets).not.toHaveBeenCalled();
  read.mockResolvedValue(task("completed"));
  rerender({owner: "a"});
  await waitFor(() => expect(result.current.task?.status).toBe("completed"));
  expect(result.current.busy).toBe(false);
});

test("does not apply an old owner's delayed image response to the new owner", async () => {
  const read = vi.spyOn(api, "coverGenerationTask").mockResolvedValue({...task(), cover_set_id: "set-a"});
  let finish!: (value: CoverSet[]) => void;
  vi.spyOn(api, "covers").mockReturnValue(new Promise(resolve => {finish = resolve;}));
  const onSets = vi.fn();
  const {result, rerender} = renderHook(({owner}) => useCoverGenerationTask(owner, onSets), {initialProps: {owner: "a"}});
  await waitFor(() => expect(result.current.task?.status).toBe("running"));
  read.mockResolvedValue(null);
  rerender({owner: "b"});
  await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => {finish([set]);});
  expect(result.current.task).toBeNull();
  expect(onSets).not.toHaveBeenCalled();
});
