import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WorkshopDeliveryCheckpoints, WorkshopRuntimeStatus } from "./WorkshopProgress";
import type { WorkshopRuntimeProgress } from "../api/workshop";

const now = Date.parse("2026-10-12T07:00:00Z");
const ago = (seconds: number) => new Date(now - seconds * 1000).toISOString();
function progress(patch: Partial<WorkshopRuntimeProgress> = {}): WorkshopRuntimeProgress {
  return {
    revision: 2, operation: "brainstorm", job_id: "job-1", attempt: 2, request_id: "request-2",
    started_at: ago(600), request_started_at: ago(300), deadline_at: ago(-300), phase: "waiting",
    last_worker_check_at: ago(1), last_cli_event_at: ago(300), last_content_at: null, content_chars: 0,
    last_upstream_event_at: ago(2), upstream_event: "codex.api_request", upstream_endpoint_category: "models",
    configured_max_tokens: 8192, effective_max_tokens: null, output_limit_supported: false,
    activity_time_basis: "batch_observed", last_reasoning_observed_at: null, last_text_observed_at: null,
    last_transport_observed_at: null, reasoning_event_count: 0, text_event_count: 0, transport_event_count: 0,
    ...patch,
  };
}

function view(value: WorkshopRuntimeProgress | null) {
  return render(<WorkshopRuntimeStatus progress={value} label="等待模型回复" now={now} startedAt={now - 600_000} />);
}

test("transport frames and model-list queries do not count as model progress or suppress silence", () => {
  view(progress({ last_transport_observed_at: ago(1), transport_event_count: 48 }));
  expect(screen.getByText("最近观测到推理摘要").parentElement).toHaveTextContent("尚未观测到");
  expect(screen.getByText("最近观测到正文增量").parentElement).toHaveTextContent("尚未观测到");
  expect(screen.getByText(/连接有消息，尚未观测到新的推理摘要或正文/)).toBeInTheDocument();
  expect(screen.getByText(/已有 5 分钟未收到新内容/)).toBeInTheDocument();
  expect(screen.getByText(/最近上游事件/)).toHaveTextContent("模型列表查询");
  expect(screen.queryByText(/模型正在思考|已收到 48 字符/)).not.toBeInTheDocument();
});

test.each([
  ["reasoning", { last_reasoning_observed_at: ago(4), reasoning_event_count: 7 }, "最近观测到推理摘要"],
  ["text", { last_text_observed_at: ago(4), text_event_count: 12 }, "最近观测到正文增量"],
] as const)("observes %s deltas before exec publishes a complete message", (_kind, activity, label) => {
  view(progress(activity));
  expect(screen.getByText(label).parentElement).toHaveTextContent("4 秒前");
  expect(screen.getByText(/已观测到模型内容进展/)).toBeInTheDocument();
  expect(screen.getByText("最近收到回复文本").parentElement).toHaveTextContent("尚未收到");
  expect(screen.queryByText(/分钟未收到新内容/)).not.toBeInTheDocument();
  expect(screen.queryByText(/已收到 \d+ 字符|模型正在思考/)).not.toBeInTheDocument();
});

test("old activity does not imply continuous work when newer transport messages arrive", () => {
  view(progress({ last_reasoning_observed_at: ago(180), last_transport_observed_at: ago(1), reasoning_event_count: 1, transport_event_count: 30 }));
  expect(screen.getByText(/已有 3 分钟未收到新内容/)).toBeInTheDocument();
  expect(screen.getByText("最近观测到推理摘要").parentElement).toHaveTextContent("180 秒前");
});

test("a new request starts a fresh silence clock without reusing the old round start", () => {
  view(progress({ request_started_at: ago(10), last_turn_content_at: ago(240), cumulative_content_chars: 2000 }));
  expect(screen.queryByText(/分钟未收到新内容/)).not.toBeInTheDocument();
  expect(screen.getByText("最近收到回复文本").parentElement).toHaveTextContent("240 秒前 · 本轮已收到 2000 字符");
});

test("saved content from an earlier attempt cannot mask current-request silence", () => {
  view(progress({ request_started_at: ago(240), last_turn_content_at: ago(10), cumulative_content_chars: 2000 }));
  expect(screen.getByText(/已有 4 分钟未收到新内容/)).toBeInTheDocument();
});

test("details identify batched observations and preserve per-request counts without inventing tokens", async () => {
  view(progress({ last_reasoning_observed_at: ago(3), last_text_observed_at: ago(2), last_transport_observed_at: ago(1), reasoning_event_count: 4, text_event_count: 5, transport_event_count: 11 }));
  const user = userEvent.setup();
  const details = screen.getByText("运行详情").parentElement!;
  expect(details).not.toHaveAttribute("open");
  await user.click(screen.getByText("运行详情"));
  expect(within(details).getByText(/本次请求观测计数/)).toHaveTextContent("公开摘要 4 次 · 正文增量 5 次 · 连接消息 11 次");
  expect(within(details).getByText(/原生指标批次的接收时间/)).toHaveTextContent("计数不等于 Token 或字符数");
  expect(within(details).getByText(/上游事件和后台检查/)).toHaveTextContent("不代表模型持续思考");
});

test("delivery distinguishes the bounded overall task from each model request", () => {
  view(progress({ task_budget_seconds: 2700, request_timeout_seconds: 900, deadline_at: ago(-2100) }));
  expect(screen.getByText(/^本轮总时限/)).toHaveTextContent("45 分钟");
  expect(screen.getByText(/单次模型请求上限/)).toHaveTextContent("15 分钟；同时受本轮总时限约束");
  expect(screen.queryByText(/本轮最长等待/)).not.toBeInTheDocument();
});

test("legacy request records keep their original total deadline", () => {
  view(progress());
  expect(screen.getByText(/本轮最长等待/)).toHaveTextContent("15 分钟");
  expect(screen.queryByText(/单次模型请求上限/)).not.toBeInTheDocument();
});

test("checkpoint views show only durably saved artifacts and keep running content private", async () => {
  render(<WorkshopDeliveryCheckpoints progress={{ status: "running", completed_stages: ["story_design"], active_stage: "chapter_plan", source_revision: 3, resumable: false,
    stages: [{ stage: "story_design", status: "saved", content_md: "Saved story decisions" }, { stage: "chapter_plan", status: "running", content_md: "Partial unvalidated chapter data" }] }}
    running renderContent={(text) => <p>{text}</p>} />);
  expect(screen.getByText("已保存 1/3 个阶段")).toBeInTheDocument();
  expect(screen.getByText("章节规划").closest("li")).toHaveAttribute("aria-current", "step");
  expect(screen.getByText("章节规划").closest("li")).toHaveTextContent("进行中");
  const summary = screen.getByText("查看已保存的全局故事设计");
  expect(summary.parentElement).not.toHaveAttribute("open");
  await userEvent.setup().click(summary);
  expect(summary.parentElement).toHaveAttribute("open");
  expect(screen.getByLabelText("全局故事设计阶段成果")).toHaveTextContent("Saved story decisions");
  expect(screen.queryByText("Partial unvalidated chapter data")).not.toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

test("paused checkpoints identify the unfinished stage without pretending it remains running", () => {
  render(<WorkshopDeliveryCheckpoints progress={{ status: "paused", completed_stages: ["story_design"], active_stage: "chapter_plan", source_revision: 3, resumable: true }}
    running={false} renderContent={(text) => text} />);
  expect(screen.getByText("章节规划").closest("li")).toHaveTextContent("未完成");
  expect(screen.queryByText("进行中")).not.toBeInTheDocument();
});

test("assembly shows its current part and durable progress without treating parts as complete stages", async () => {
  render(<WorkshopDeliveryCheckpoints progress={{ status: "running", completed_stages: ["story_design", "chapter_plan"], active_stage: "assembly", resumable: false,
    assembly: { completed_parts: 1, total_parts: 3, active_part: "world", parts: [
      { part: "people", label: "人物与关系", status: "saved", content_md: "Verified character relationships" },
      { part: "world", label: "世界与线索", status: "running", content_md: "Unvalidated fragment" },
      { part: "delivery", label: "交付规范", status: "pending" },
    ] } }} running renderContent={(text) => <p>{text}</p>} />);
  const assembly = screen.getByRole("region", { name: "交付整合进度" });
  expect(screen.getByText("已保存 2/3 个阶段")).toBeInTheDocument();
  expect(within(assembly).getByText("已保存 1/3 个部分")).toBeInTheDocument();
  expect(within(assembly).getByText("正在补全：世界与线索")).toBeInTheDocument();
  expect(screen.queryByText("Unvalidated fragment")).not.toBeInTheDocument();
  const user = userEvent.setup();
  const summary = within(assembly).getByText("查看补全部分");
  expect(summary.parentElement).not.toHaveAttribute("open");
  await user.click(summary);
  expect(within(assembly).getByText("世界与线索").closest("li")).toHaveAttribute("aria-current", "step");
  await user.click(within(assembly).getByText("查看已保存内容"));
  expect(within(assembly).getByLabelText("人物与关系已保存内容")).toHaveTextContent("Verified character relationships");
  expect(within(assembly).getByText("交付规范").closest("li")).toHaveTextContent("待开始");
});

test.each(["paused", "error"] as const)("%s assembly shows the retained part and explicit recovery without claiming active work", (status) => {
  render(<WorkshopDeliveryCheckpoints progress={{ status, completed_stages: ["story_design", "chapter_plan"], active_stage: "assembly", resumable: true,
    assembly: { completed_parts: 1, total_parts: 2, active_part: "world", parts: [
      { part: "people", label: "人物与关系", status: "saved" },
      { part: "world", label: "世界与线索", status: "running" },
    ] } }} running={false} renderContent={(text) => text} />);
  const assembly = screen.getByRole("region", { name: "交付整合进度" });
  expect(within(assembly).getByText("待继续：世界与线索")).toBeInTheDocument();
  expect(within(assembly).getByText(/继续时会复用已保存部分，只补全尚未完成的内容/)).toBeInTheDocument();
  expect(within(assembly).queryByText(/正在补全|进行中/)).not.toBeInTheDocument();
});

test("saving every assembly part still waits for final integration and validation", () => {
  render(<WorkshopDeliveryCheckpoints progress={{ status: "running", completed_stages: ["story_design", "chapter_plan"], active_stage: "assembly", resumable: false,
    assembly: { completed_parts: 1, total_parts: 1, active_part: null, parts: [{ part: "people", label: "人物与关系", status: "saved" }] } }}
    running renderContent={(text) => text} />);
  expect(screen.getByText("已保存 2/3 个阶段")).toBeInTheDocument();
  expect(screen.getByText("各部分已保存，正在整合与校验。")).toBeInTheDocument();
  expect(screen.queryByText("已保存 3/3 个阶段")).not.toBeInTheDocument();
});
