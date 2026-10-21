import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { CoverDirectionTask } from "../api/client";
import CoverDirectionTaskStatus from "./CoverDirectionTaskStatus";

const completed: CoverDirectionTask = {
  task_id: "task-old", status: "completed", analysis_id: "analysis", count: 4,
  freshness_policy: "require_current_head", direction_id: "direction-old", error: null,
  created_at: "2026-10-11T10:00:00Z", updated_at: "2026-10-11T10:02:00Z", finished_at: "2026-10-11T10:02:00Z",
};
afterEach(() => { cleanup(); vi.useRealTimers(); });

test("does not present a previous completion or its elapsed time while submitting a new task", () => {
  render(<CoverDirectionTaskStatus task={completed} checking={false} connectionError=""
    lastSyncedAt={Date.parse(completed.finished_at!)} submitting />);
  expect(screen.getByText("正在提交美术方向任务")).toBeVisible();
  expect(screen.queryByText("美术方向已生成")).not.toBeInTheDocument();
  expect(screen.queryByText(/总用时/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

test("keeps confirmed completion distinct from direction result synchronization failure", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-11T10:03:00Z"));
  const { rerender } = render(<CoverDirectionTaskStatus task={completed} checking connectionError=""
    lastSyncedAt={Date.now()} />);
  expect(screen.getByText("任务已完成，正在加载已保存方案。")).toBeVisible();
  expect(screen.getByText("总用时 2 分 00 秒")).toBeVisible();
  rerender(<CoverDirectionTaskStatus task={completed} checking connectionError="saved direction unavailable"
    lastSyncedAt={Date.now()} />);
  expect(screen.getByText("任务已完成，正在重试加载已保存方案，不会重新规划。")).toBeVisible();
  expect(screen.queryByText(/上次确认：运行中/)).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
  expect(screen.getByText("总用时 2 分 00 秒")).toBeVisible();
});

test("waits honestly for first synchronization and never fabricates an elapsed time from invalid timestamps", () => {
  const { rerender } = render(<CoverDirectionTaskStatus task={null} checking connectionError="" lastSyncedAt={null} />);
  expect(screen.getByText("正在同步美术方向任务")).toBeVisible();
  expect(screen.getByText("等待首次状态同步")).toBeVisible();
  expect(screen.queryByText(/已用时|总用时/)).not.toBeInTheDocument();
  rerender(<CoverDirectionTaskStatus task={{...completed, created_at: "invalid"}} checking={false}
    connectionError="" lastSyncedAt={null} />);
  expect(screen.getByText("美术方向已生成")).toBeVisible();
  expect(screen.queryByText(/NaN|已用时|总用时/)).not.toBeInTheDocument();
  rerender(<CoverDirectionTaskStatus task={null} checking={false} connectionError="" lastSyncedAt={null} />);
  expect(screen.queryByRole("region", { name: "美术方向任务状态" })).not.toBeInTheDocument();
});

test("shows durable review phase, actual round and replies without inventing a token rate", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-11T10:03:00Z"));
  const task: CoverDirectionTask = {...completed, status: "running", finished_at: null,
    progress: {phase: "semantic_started", attempt: 2, completed_model_calls: 3,
      phase_started_at: "2026-10-11T10:02:00Z", last_event_at: "2026-10-11T10:02:00Z"}};
  const { rerender } = render(<CoverDirectionTaskStatus task={task} checking={false}
    connectionError="" lastSyncedAt={Date.now()} />);
  expect(screen.getByText("正在独立审核故事与构图")).toBeVisible();
  expect(screen.getByText("第 2 轮 · 已收到 3 次模型回复")).toBeVisible();
  expect(screen.getByText("本阶段已用时 1 分 00 秒")).toBeVisible();
  expect(screen.getByText(/Token 在模型调用结束后更新/)).toBeVisible();
  expect(screen.queryByText(/tokens\/s|%|预计剩余/)).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(16000); });
  expect(screen.getByText("状态同步较慢")).toBeVisible();
  expect(screen.getByText("上次确认阶段：正在独立审核故事与构图")).toBeVisible();
  expect(screen.queryByText(/本阶段已用时/)).not.toBeInTheDocument();
  rerender(<CoverDirectionTaskStatus task={{...task, status: "failed", finished_at: "2026-10-11T10:03:00Z", error: "fixture failure"}}
    checking={false} connectionError="" lastSyncedAt={Date.now()} />);
  expect(screen.getByText("美术方向规划失败")).toBeVisible();
  expect(screen.getByText("最后阶段：独立审核故事与构图")).toBeVisible();
  expect(screen.queryByText(/本阶段已用时/)).not.toBeInTheDocument();
});

test("a director completion is still saving until durable task completion and old phases stay hidden during submission", () => {
  const task: CoverDirectionTask = {...completed, status: "running", finished_at: null,
    progress: {phase: "completed", attempt: 1, completed_model_calls: 2,
      phase_started_at: "2026-10-11T10:02:00Z", last_event_at: "2026-10-11T10:02:00Z"}};
  const { rerender } = render(<CoverDirectionTaskStatus task={task} checking={false} connectionError="" lastSyncedAt={Date.now()} />);
  expect(screen.getByText("正在保存并核验美术方向")).toBeVisible();
  expect(screen.queryByText("美术方向已生成")).not.toBeInTheDocument();
  rerender(<CoverDirectionTaskStatus task={task} checking={false} connectionError="" lastSyncedAt={Date.now()} submitting />);
  expect(screen.queryByText(/模型回复|本阶段已用时|第 1 轮/)).not.toBeInTheDocument();
});
