import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";
import type { ProjectSummary } from "../api/client";
import ProjectCard from "../components/ProjectCard";
import StatusPill from "../components/StatusPill";

const project: ProjectSummary = {
  id: "last-signal",
  title: "最后的信号",
  genre: "科幻",
  chapter_count: 3,
  drafted_count: 3,
  status: "completed",
  task_id: "run-2026-10-08T05-30-00-abcdef0123456789",
  task_started_at: "2026-10-08T05:30:00Z",
  updated_at: "2026-10-09T15:45:00Z",
};

function renderCard(overrides: Partial<ProjectSummary> = {}) {
  return render(<MemoryRouter><ProjectCard p={{ ...project, ...overrides }} /></MemoryRouter>);
}

function CurrentLocation() {
  return <output aria-label="当前路径">{useLocation().pathname}</output>;
}

test("opens the project using one native keyboard-accessible card link", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/projects"]}>
    <ProjectCard p={project} /><CurrentLocation />
  </MemoryRouter>);
  const link = screen.getByRole("link", { name: "打开作品" });
  expect(screen.getAllByRole("link").filter((item) => item.getAttribute("href") === "/projects/last-signal")).toHaveLength(1);
  expect(link.querySelector("button, details, a")).toBeNull();
  await user.tab();
  expect(link).toHaveFocus();
  await user.keyboard("{Enter}");
  expect(screen.getByLabelText("当前路径")).toHaveTextContent(/^\/projects\/last-signal$/);
});

test("keeps the cover entry separate from the whole-card project link", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/projects"]}>
    <ProjectCard p={project} /><CurrentLocation />
  </MemoryRouter>);
  const cover = screen.getByRole("link", { name: "设计封面" });
  expect(cover.parentElement?.closest("a")).toBeNull();
  await user.tab();
  await user.tab();
  expect(cover).toHaveFocus();
  await user.keyboard("{Enter}");
  expect(screen.getByLabelText("当前路径")).toHaveTextContent(/^\/projects\/last-signal\/covers$/);
});

test("task disclosure and its selectable contents do not open the project", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/projects"]}>
    <ProjectCard p={project} /><CurrentLocation />
  </MemoryRouter>);
  await user.click(screen.getByText("任务信息"));
  const taskId = screen.getByText(project.task_id!);
  expect(taskId.closest("details")).toHaveAttribute("open");
  expect(taskId.closest("a")).toBeNull();
  await user.click(taskId);
  expect(screen.getByLabelText("当前路径")).toHaveTextContent(/^\/projects$/);
});

test("management and resume actions stay in the library without opening the project", async () => {
  const user = userEvent.setup();
  const onDelete = vi.fn();
  const onResume = vi.fn().mockResolvedValue(undefined);
  const retryable = { ...project, status: "failed", task_can_resume: true };
  render(<MemoryRouter initialEntries={["/projects"]}>
    <ProjectCard p={retryable} onDelete={onDelete} onResume={onResume} /><CurrentLocation />
  </MemoryRouter>);
  await user.click(screen.getByLabelText(`管理作品《${project.title}》`));
  const remove = screen.getByRole("button", { name: "永久删除作品" });
  expect(remove.closest("a")).toBeNull();
  await user.click(remove);
  expect(onDelete).toHaveBeenCalledWith(retryable);
  expect(remove.closest("details")).not.toHaveAttribute("open");
  expect(screen.getByLabelText("当前路径")).toHaveTextContent(/^\/projects$/);
  await user.click(screen.getByRole("button", { name: "恢复重试" }));
  expect(onResume).toHaveBeenCalledTimes(1);
  expect(onResume).toHaveBeenCalledWith(retryable);
  expect(screen.getByLabelText("当前路径")).toHaveTextContent(/^\/projects$/);
});

test.each([
  ["completed", "已完成"],
  ["running", "运行中"],
  ["paused", "已暂停"],
  ["interrupted", "已中断"],
  ["failed", "失败"],
  ["cancelled", "已取消"],
  ["pending", "待启动"],
  ["draft", "未启动"],
  ["in_progress", "未完成"],
])("renders project status %s as %s", (status, label) => {
  renderCard({ status });
  expect(screen.getByText(label)).toHaveAttribute("data-status", status);
});

test("shows the full task ID and task start time instead of the last update", () => {
  const { container } = renderCard();
  const task = screen.getByText(project.task_id!);
  expect(task).toHaveAttribute("title", project.task_id);
  expect(task).toHaveClass("break-all");
  expect(screen.getByRole("link", { name: "打开作品" })).toHaveAttribute("href", "/projects/last-signal");

  const time = container.querySelector("time");
  expect(time).toHaveAttribute("datetime", project.task_started_at);
  expect(time).toHaveTextContent(new Date(project.task_started_at!).toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }));
  expect(time?.parentElement).toHaveTextContent(/^启动 /);
});

test.each([undefined, null, "", "not-a-date"])(
  "shows unknown start time for %s without falling back to updated_at",
  (task_started_at) => {
    const { container } = renderCard({ task_started_at });
    expect(screen.getByText("启动时间未知")).toBeInTheDocument();
    expect(container.querySelector("time")).not.toBeInTheDocument();
    expect(screen.queryByText(/Invalid Date/)).not.toBeInTheDocument();
  },
);

test("shows an empty task ID for projects that have never started", () => {
  renderCard({ task_id: null, task_started_at: null, status: "draft" });
  expect(screen.getByText("任务 ID：").parentElement).toHaveTextContent("任务 ID：—");
  expect(screen.getByText("未启动")).toBeInTheDocument();
});

test("keeps the shared in_progress label unchanged outside project cards", () => {
  render(<StatusPill status="in_progress" />);
  expect(screen.getByText("进行中")).toBeInTheDocument();
});

test("offers resume outside the project link and prevents duplicate submits", async () => {
  let finish: () => void = () => {};
  const onResume = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
  render(<MemoryRouter><ProjectCard p={{ ...project, status: "failed", task_can_resume: true }} onResume={onResume} /></MemoryRouter>);
  const button = screen.getByRole("button", { name: "恢复重试" });
  expect(button.closest("a")).toBeNull();
  fireEvent.click(button);
  expect(screen.getByRole("button", { name: "恢复中…" })).toBeDisabled();
  fireEvent.click(button);
  expect(onResume).toHaveBeenCalledTimes(1);
  await act(async () => finish());
});

test("keeps the failed card visible and retryable when resume fails", async () => {
  const onResume = vi.fn().mockRejectedValue(new Error("任务当前不可恢复"));
  render(<MemoryRouter><ProjectCard p={{ ...project, status: "failed", task_can_resume: true }} onResume={onResume} /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "恢复重试" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("任务当前不可恢复"));
  expect(screen.getByRole("button", { name: "恢复重试" })).toBeEnabled();
  expect(screen.getByText(project.title)).toBeInTheDocument();
});

test("hides recovery when the backend says the task cannot resume", () => {
  render(<MemoryRouter><ProjectCard p={{ ...project, status: "failed", task_can_resume: false }} onResume={vi.fn()} /></MemoryRouter>);
  expect(screen.queryByRole("button", { name: "恢复重试" })).not.toBeInTheDocument();
});

test.each([
  ["not_started", "待设计", "设计封面"], ["processing", "处理中", "查看进度"],
  ["awaiting_review", "待确认", "确认封面"], ["needs_attention", "需处理", "处理封面"],
  ["selected", "已选定", "查看封面"],
] as const)("shows cover %s independently of completed writing", (status, label, action) => {
  renderCard({ cover: { status, detail: "实际处理结果。", candidate_count: 4 } });
  expect(screen.getByText("已完成")).toBeInTheDocument();
  expect(screen.getByText(label)).toHaveAttribute("data-status", status);
  expect(screen.getByRole("link", { name: action })).toHaveAttribute("href", "/projects/last-signal/covers");
  expect(screen.getByRole("link", { name: "打开作品" })).toHaveAttribute("href", "/projects/last-signal");
});

test("identifies candidate preview and keeps its review status after image failure", () => {
  renderCard({ cover: { status: "needs_attention", detail: "需要修复", image_kind: "candidate", image_url: "/api/image.jpg", candidate_count: 4 } });
  const image = screen.getByRole("img", { name: "最后的信号的候选封面" });
  expect(screen.getByText("候选预览")).toBeInTheDocument();
  fireEvent.error(image);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(screen.getByText("文稿书衣")).toBeInTheDocument();
  expect(screen.getByText("需处理")).toBeInTheDocument();
});

test("keeps task identity behind an optional disclosure", () => {
  const { container } = renderCard();
  const details = container.querySelector(".library-book__task");
  expect(details).not.toHaveAttribute("open");
  fireEvent.click(screen.getByText("任务信息"));
  expect(details).toHaveAttribute("open");
  expect(screen.getByText(project.task_id!)).toBeVisible();
});
