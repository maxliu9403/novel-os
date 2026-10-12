import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { CoverGenerationTask } from "../api/client";
import CoverGenerationTaskStatus from "./CoverGenerationTaskStatus";

const task: CoverGenerationTask = {task_id: "generation-a", job_id: "job-a", status: "running", direction_id: "direction-a", cover_set_id: "set-a",
  count: 4, result_status: null, error: null, created_at: "2026-10-11T10:00:00Z", started_at: "2026-10-11T10:00:01Z",
  updated_at: "2026-10-11T10:01:00Z", finished_at: null, progress: {phase: "generating", current_candidate_id: "candidate-2", current_candidate_index: 2,
    total: 4, completed: 1, failed: 0, ready: 1, phase_started_at: "2026-10-11T10:01:00Z", last_event_at: "2026-10-11T10:01:00Z"}};
const props = {checking: false, connectionError: "", lastSyncedAt: Date.parse("2026-10-11T10:03:00Z"), submitting: false};
afterEach(() => {cleanup(); vi.useRealTimers();});

test.each([
  ["preparing", "正在准备生成封面"], ["generating", "正在生成封面图片"], ["composing", "正在排版与合成"],
  ["reviewing", "正在审核封面质量"], ["comparing", "正在检查整组差异"], ["saving", "正在保存封面结果"],
] as const)("displays the reported %s phase without fabricated completion percentage", (phase, title) => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-11T10:03:00Z"));
  render(<CoverGenerationTaskStatus {...props} task={{...task, progress: {...task.progress!, phase}}} />);
  expect(screen.getByRole("heading", {name: title})).toBeVisible();
  expect(screen.getByText("当前处理：候选图 2 / 4")).toBeVisible();
  expect(screen.getByText("已处理 1/4 张 · 可查看 1 张 · 生成失败 0 张")).toBeVisible();
  expect(screen.getByText("已用时 3 分 00 秒")).toBeVisible();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  expect(screen.queryByText(/预计剩余|%|tokens\/s/)).not.toBeInTheDocument();
  expect(screen.getByText("已用时 3 分 00 秒").closest('[aria-live="off"]')).not.toBeNull();
});

test("does not confuse processed, failed or viewable images with a passed quality review", () => {
  render(<CoverGenerationTaskStatus {...props} task={{...task, status: "completed", result_status: "partial", finished_at: "2026-10-11T10:04:00Z",
    progress: {...task.progress!, phase: "saving", completed: 4, ready: 3, failed: 1, current_candidate_id: null, current_candidate_index: null}}} />);
  expect(screen.getByText("本批封面已处理，部分图片生成失败")).toBeVisible();
  expect(screen.getByText("已处理 4/4 张 · 可查看 3 张 · 生成失败 1 张")).toBeVisible();
  expect(screen.getByText(/生成完成不代表质量审核通过/)).toBeVisible();
});

test.each(["failed", "interrupted"] as const)("preserves %s with a frozen clock and does not offer an automatic retry", async status => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-11T10:10:00Z"));
  render(<CoverGenerationTaskStatus {...props} task={{...task, status, error: "服务已中断", finished_at: "2026-10-11T10:04:00Z"}} />);
  expect(screen.getByText(status === "failed" ? "封面生成失败" : "封面生成已中断")).toBeVisible();
  expect(screen.getByText(/服务已中断.*不会自动重试/)).toBeVisible();
  await act(async () => {await vi.advanceTimersByTimeAsync(10000);});
  expect(screen.getByText("总用时 4 分 00 秒")).toBeVisible();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

test("marks a stalled status read as uncertain without calling its timestamp a model heartbeat", async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-11T10:03:00Z"));
  const {rerender} = render(<CoverGenerationTaskStatus {...props} task={task} />);
  await act(async () => {await vi.advanceTimersByTimeAsync(16000);});
  expect(screen.getByText("封面任务状态暂未确认")).toBeVisible();
  expect(screen.getByText(/上次确认：运行中/)).toBeVisible();
  expect(screen.getByText("本次任务已等待 3 分 16 秒")).toBeVisible();
  expect(screen.queryByText(/本阶段已用时/)).not.toBeInTheDocument();
  expect(screen.getByText("同步时间不代表模型已有新输出")).toBeVisible();
  rerender(<CoverGenerationTaskStatus {...props} checking connectionError="图片列表不可用" task={{...task, status: "completed", finished_at: "2026-10-11T10:04:00Z"}} />);
  expect(screen.getByText(/上次确认：已完成.*不会重新出图/)).toBeVisible();
  expect(screen.getByText("总用时 4 分 00 秒")).toBeVisible();
});

test("handles initial discovery, queueing and submitting without showing a previous batch's clock", () => {
  const {rerender} = render(<CoverGenerationTaskStatus {...props} checking lastSyncedAt={null} task={null} />);
  expect(screen.getByText("正在同步封面生成任务")).toBeVisible();
  expect(screen.queryByText(/已用时|总用时/)).not.toBeInTheDocument();
  rerender(<CoverGenerationTaskStatus {...props} lastSyncedAt={Date.now()} task={{...task, status: "queued", progress: null}} />);
  expect(screen.getByText("封面生成已排队")).toBeVisible();
  rerender(<CoverGenerationTaskStatus {...props} submitting task={{...task, status: "completed", finished_at: "2026-10-11T10:04:00Z"}} />);
  expect(screen.getByText("正在提交封面生成任务")).toBeVisible();
  expect(screen.queryByText(/总用时|已处理 1/)).not.toBeInTheDocument();
});

test("keeps the actual generation failure visible when loading its saved images also fails", () => {
  render(<CoverGenerationTaskStatus {...props} checking connectionError="图片列表暂不可用"
    task={{...task, status: "failed", error: "所有图片调用失败", finished_at: "2026-10-11T10:04:00Z"}} />);
  expect(screen.getByText("任务结果：所有图片调用失败")).toBeVisible();
  expect(screen.getByText("图片列表暂不可用")).toBeVisible();
  expect(screen.getByText(/上次确认：失败/)).toBeVisible();
});
