import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import type { PipelineProgress, ProjectTaskDetail, ProjectTaskPlanning } from "../api/client";
import TaskProgress from "../components/TaskProgress";
import { projectTaskElapsed } from "../lib/projectTasks";

const now = "2026-10-22T10:02:00Z";
const progress: PipelineProgress = {
  schema_version: 1, run_id: "run-a", execution_id: "execution-a", status: "running",
  phase: "outline", chapter: null, started_at: "2026-10-22T10:00:00Z", updated_at: now,
  phase_started_at: "2026-10-22T10:00:00Z", heartbeat_at: now, last_upstream_activity_at: now,
  last_model_activity_at: null, last_output_at: null, request_timeout_seconds: 900, stage_timeout_seconds: 3600,
  request: { request_id: "request-a", provider: "codex", model: "test-model", started_at: "2026-10-22T10:01:00Z", status: "running", output_chars: 0, model_activity_count: 0, error_type: null, timeout_seconds: 900 },
  milestones: [],
};
const planning: ProjectTaskPlanning = {
  status: "running", active_step: "chapters", updated_at: now, completed_batches: 2, total_batches: 5, chapter_count: 20,
  message: "正在规划第 9–12 章。", next_action: "本批检查通过后继续下一批。",
  steps: [
    { id: "foundation", label: "故事基础", status: "completed", summary: "人物动机与核心冲突已保存。" },
    { id: "chapters", label: "章节批次", status: "running", summary: "已保存前两批。" },
    { id: "review", label: "全书校验", status: "pending" },
  ],
  milestones: [{ id: "saved-1", time: now, label: "第 5–8 章大纲已保存", summary: "本批人物选择和因果衔接已通过检查。" }],
};
const task: ProjectTaskDetail = {
  run_id: "run-a", status: "running", started_at: progress.started_at, updated_at: now,
  current_phase: "outline", current_chapter: null, error: null, failed_stages: [], can_resume: false, progress,
};

afterEach(() => { cleanup(); vi.useRealTimers(); });

test("fresh heartbeat and upstream events do not assert model content or reasoning", () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date(now));
  render(<TaskProgress task={task} />);
  expect(screen.getByRole("status")).toHaveTextContent("等待模型内容");
  expect(screen.getByText(/无法确认模型是否正在生成/)).toBeInTheDocument();
  fireEvent.click(screen.getByText("查看活动与时间记录"));
  expect(screen.getByText("后台在线信号").nextElementSibling).toHaveTextContent("2026");
  expect(screen.getByText("最近模型内容").nextElementSibling).toHaveTextContent("尚未收到");
  expect(screen.getByText(/后台在线信号和上游事件不代表模型持续生成/)).toBeInTheDocument();
});

test("only content from the current request changes the activity label", () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date(now));
  const { rerender } = render(<TaskProgress task={{ ...task, progress: { ...progress, last_model_activity_at: "2026-10-22T10:00:30Z" } }} />);
  expect(screen.getByRole("status")).toHaveTextContent("等待模型内容");
  rerender(<TaskProgress task={{ ...task, progress: { ...progress, last_model_activity_at: "2026-10-22T10:01:30Z", request: { ...progress.request!, output_chars: 28, model_activity_count: 1 } } }} />);
  expect(screen.getByRole("status")).toHaveTextContent("已收到模型内容 · 等待完整回复");
  expect(screen.getByText(/最近收到内容：30 秒前/)).toBeInTheDocument();
});

test("a completed model reply is validation, not a saved planning result", () => {
  render(<TaskProgress task={{ ...task, progress: { ...progress, request: { ...progress.request!, status: "completed" } }, planning: { ...planning, completed_batches: 0, milestones: [], steps: [{ id: "foundation", label: "故事基础", status: "running" }] } }} />);
  expect(screen.getByRole("status")).toHaveTextContent("回复已返回 · 校验与保存");
  expect(screen.getByText(/尚无已保存的阶段成果/)).toBeInTheDocument();
  expect(screen.queryByText("已保存")).not.toBeInTheDocument();
});

test("saved steps, batch counts and milestones are restored from persistent task data", () => {
  const view = render(<TaskProgress task={{ ...task, planning }} />);
  expect(screen.getByText("正在规划第 9–12 章。")).toBeInTheDocument();
  expect(screen.getByText("本批检查通过后继续下一批。")).toBeInTheDocument();
  expect(screen.getByRole("progressbar", { name: "已保存的章节大纲批次" })).toHaveAttribute("value", "2");
  expect(screen.getByRole("progressbar")).toHaveAttribute("max", "5");
  view.unmount();
  render(<TaskProgress task={{ ...task, status: "paused", can_resume: true, planning }} />);
  expect(screen.getByText("第 5–8 章大纲已保存")).toBeInTheDocument();
  expect(screen.getByText("人物动机与核心冲突已保存。")).toBeInTheDocument();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

test("legacy or mismatched diagnostics show unknown activity instead of an active model", () => {
  render(<TaskProgress task={{ ...task, progress: { ...progress, run_id: "wrong-run" } }} />);
  expect(screen.getByRole("status")).toHaveTextContent("模型活动未知");
  expect(screen.getByText(/此执行未提供模型活动记录/)).toBeInTheDocument();
  expect(screen.queryByText("查看活动与时间记录")).not.toBeInTheDocument();
});

test.each(["failed", "paused", "completed"])("an ended %s execution cannot look like live model work during resume", (status) => {
  render(<TaskProgress task={{ ...task, progress: { ...progress, status, request: { ...progress.request!, status: "completed" } } }} />);
  expect(screen.getByRole("status")).toHaveTextContent("模型活动未知");
  expect(screen.queryByText("回复已返回 · 校验与保存")).not.toBeInTheDocument();
});

test.each([["paused", "已暂停"], ["failed", "未完成"]])("a %s task cannot leave a saved running step visually active", (status, label) => {
  render(<TaskProgress task={{ ...task, status, planning }} />);
  expect(screen.getByText(label)).toBeInTheDocument();
  expect(screen.queryByText("进行中")).not.toBeInTheDocument();
  expect(screen.getByText("第 5–8 章大纲已保存")).toBeInTheDocument();
});

test("failed outline retains its recovery instructions without showing them in later phases", () => {
  const failed = { ...task, status: "failed", planning: { ...planning, status: "failed", next_action: "请检查已保存的故事设计，不要重复恢复相同请求。" } };
  const view = render(<TaskProgress task={failed} />);
  expect(screen.getByText("下一步：请检查已保存的故事设计，不要重复恢复相同请求。")).toBeInTheDocument();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  view.rerender(<TaskProgress task={{ ...failed, current_phase: "draft" }} />);
  expect(screen.queryByText(/下一步：请检查已保存的故事设计/)).not.toBeInTheDocument();
});

test("elapsed times reject unknown or future timestamps", () => {
  expect(projectTaskElapsed("invalid", Date.parse(now))).toBeNull();
  expect(projectTaskElapsed("2026-10-22T10:03:00Z", Date.parse(now))).toBeNull();
  expect(projectTaskElapsed("2026-10-22T10:00:00Z", Date.parse(now))).toBe("2 分 0 秒");
});

test("long milestone content has wrapping and shrinkable containers for narrow screens", () => {
  const longLabel = "chapter_".repeat(60);
  render(<TaskProgress task={{ ...task, planning: { ...planning, milestones: [{ id: "long", label: longLabel, summary: longLabel, time: now }] } }} />);
  for (const text of screen.getAllByText(longLabel)) expect(text).toHaveClass("break-words");
  expect(screen.getByRole("region", { name: "大纲规划成果" })).toHaveClass("min-w-0");
});
