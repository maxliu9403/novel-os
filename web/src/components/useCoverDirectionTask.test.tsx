import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { api, type CoverDirectionTask } from "../api/client";
import { useCoverDirectionTask } from "./useCoverDirectionTask";

const task = (status: CoverDirectionTask["status"]): CoverDirectionTask => ({
  task_id: "task-a", status, analysis_id: "analysis-a", count: 4,
  freshness_policy: "require_current_head", direction_id: status === "completed" ? "direction-a" : null,
  error: status === "failed" ? "Provider timeout" : status === "interrupted" ? "Worker restarted" : null,
  created_at: "2026-10-11T10:00:00Z", updated_at: "2026-10-11T10:01:00Z", finished_at: null,
});

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

test("polls a recovered task to completion without starting another model call", async () => {
  vi.useFakeTimers();
  vi.spyOn(api, "coverDirectionTask").mockResolvedValueOnce(task("running")).mockResolvedValue(task("completed"));
  vi.spyOn(api, "coverDirections").mockResolvedValue([]);
  const start = vi.spyOn(api, "startCoverDirectionTask");
  const complete = vi.fn();
  const {result} = renderHook(() => useCoverDirectionTask("book", complete));
  await act(async () => {});
  expect(result.current.busy).toBe(true);
  await act(async () => { await result.current.start(4, "analysis-a"); });
  expect(start).not.toHaveBeenCalled();
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(result.current.task?.status).toBe("completed");
  expect(result.current.busy).toBe(false);
  expect(complete).toHaveBeenCalledWith([], true);
});

test.each(["failed", "interrupted"] as const)("restores %s without automatically retrying a paid operation", async status => {
  vi.spyOn(api, "coverDirectionTask").mockResolvedValue(task(status));
  const start = vi.spyOn(api, "startCoverDirectionTask");
  const complete = vi.fn();
  const {result} = renderHook(() => useCoverDirectionTask("book", complete));
  await waitFor(() => expect(result.current.task?.status).toBe(status));
  expect(result.current.task?.error).toBeTruthy();
  expect(result.current.busy).toBe(false);
  expect(start).not.toHaveBeenCalled();
});

test("keeps submission locked while task discovery fails and retries the read", async () => {
  vi.useFakeTimers();
  const read = vi.spyOn(api, "coverDirectionTask").mockRejectedValueOnce(new Error("offline")).mockResolvedValue(task("running"));
  const start = vi.spyOn(api, "startCoverDirectionTask");
  const complete = vi.fn();
  const {result} = renderHook(() => useCoverDirectionTask("book", complete));
  await act(async () => {});
  expect(result.current.busy).toBe(true);
  expect(result.current.connectionError).toBe("offline");
  await act(async () => { await result.current.start(4); });
  expect(start).not.toHaveBeenCalled();
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(read).toHaveBeenCalledTimes(2);
  expect(result.current.task?.status).toBe("running");
  expect(result.current.connectionError).toBe("");
});

test("does not retain the local submission lock after switching owners during a pending POST", async () => {
  const read = vi.spyOn(api, "coverDirectionTask").mockResolvedValue(null);
  vi.spyOn(api, "coverDirections").mockResolvedValue([]);
  let finish!: (value: CoverDirectionTask) => void;
  const post = vi.spyOn(api, "startCoverDirectionTask").mockReturnValue(new Promise(resolve => {finish = resolve;}));
  const complete = vi.fn();
  const {result, rerender} = renderHook(({owner}) => useCoverDirectionTask(owner, complete), {initialProps: {owner: "a"}});
  await waitFor(() => expect(result.current.busy).toBe(false));
  let request!: Promise<void>;
  act(() => { request = result.current.start(4); });
  expect(result.current.busy).toBe(true);
  rerender({owner: "b"});
  await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => { finish(task("completed")); await request; });
  expect(result.current.task).toBeNull();
  expect(complete).not.toHaveBeenCalled();
  read.mockResolvedValue(task("completed"));
  rerender({owner: "a"});
  await waitFor(() => expect(result.current.busy).toBe(false));
  expect(result.current.task?.status).toBe("completed");
  expect(post).toHaveBeenCalledTimes(1);
});

test("recovers an accepted task after losing the POST response", async () => {
  const read = vi.spyOn(api, "coverDirectionTask").mockResolvedValue(null);
  const post = vi.spyOn(api, "startCoverDirectionTask").mockImplementation(async () => {
    read.mockResolvedValue(task("running"));
    throw new Error("connection closed");
  });
  const complete = vi.fn();
  const {result} = renderHook(() => useCoverDirectionTask("book", complete));
  await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => { await expect(result.current.start(4)).rejects.toThrow("connection closed"); });
  await waitFor(() => expect(result.current.task?.status).toBe("running"));
  expect(result.current.busy).toBe(true);
  expect(post).toHaveBeenCalledTimes(1);
});

test("timestamps successful status reads without treating polling failures as fresh activity", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-11T10:05:00Z"));
  const read = vi.spyOn(api, "coverDirectionTask").mockResolvedValue(task("running"));
  const start = vi.spyOn(api, "startCoverDirectionTask");
  const complete = vi.fn();
  const {result} = renderHook(() => useCoverDirectionTask("book", complete));
  await act(async () => {});
  const confirmedAt = Date.now();
  expect(result.current.lastSyncedAt).toBe(confirmedAt);
  expect(result.current.task?.updated_at).toBe("2026-10-11T10:01:00Z");
  read.mockRejectedValueOnce(new Error("network unavailable"));
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(result.current.lastSyncedAt).toBe(confirmedAt);
  expect(result.current.connectionError).toBe("network unavailable");
  expect(result.current.task?.status).toBe("running");
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(result.current.lastSyncedAt).toBe(confirmedAt + 3000);
  expect(result.current.connectionError).toBe("");
  expect(start).not.toHaveBeenCalled();
});

test("does not show another owner's last sync while its status is still being fetched", async () => {
  const complete = vi.fn();
  const read = vi.spyOn(api, "coverDirectionTask").mockResolvedValue(task("running"));
  const {result, rerender} = renderHook(({owner}) => useCoverDirectionTask(owner, complete), {initialProps:{owner:"a"}});
  await waitFor(() => expect(result.current.task?.status).toBe("running"));
  expect(result.current.lastSyncedAt).toEqual(expect.any(Number));
  read.mockReturnValue(new Promise(() => {}));
  rerender({owner:"b"});
  expect(result.current.task).toBeNull();
  expect(result.current.lastSyncedAt).toBeNull();
  expect(result.current.checking).toBe(true);
});

test("keeps a confirmed completion visible if loading the saved directions fails", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-11T10:05:00Z"));
  vi.spyOn(api, "coverDirectionTask").mockResolvedValueOnce(task("running")).mockResolvedValue(task("completed"));
  vi.spyOn(api, "coverDirections").mockRejectedValue(new Error("direction list unavailable"));
  const complete = vi.fn();
  const {result} = renderHook(() => useCoverDirectionTask("book", complete));
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(result.current.task?.status).toBe("completed");
  expect(result.current.lastSyncedAt).toBe(Date.now());
  expect(result.current.checking).toBe(true);
  expect(result.current.busy).toBe(true);
  expect(result.current.connectionError).toBe("direction list unavailable");
  expect(complete).not.toHaveBeenCalled();
});
