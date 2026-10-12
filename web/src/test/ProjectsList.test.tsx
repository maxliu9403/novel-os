import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, vi } from "vitest";
import ProjectsList from "../routes/ProjectsList";
import * as client from "../api/client";
import { TASK_POLL_INTERVAL_MS } from "../lib/projectTasks";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("combines keyword and separate writing/cover filters with an empty-state reset", async () => {
  vi.spyOn(client.api, "projects").mockResolvedValue([
    { id: "book-a", title: "Moonlit Garden", author: "Alice", genre: "Mystery", chapter_count: 3, status: "completed",
      cover: { status: "needs_attention", detail: "需修复", candidate_count: 4 } },
    { id: "book-b", title: "The Other Shore", author: "Bob", genre: "Drama", chapter_count: 2, status: "running",
      cover: { status: "not_started", detail: "尚未设计", candidate_count: 0 } },
  ]);
  vi.spyOn(client.api, "studioLlm").mockRejectedValue(new Error("not configured"));
  render(<MemoryRouter><ProjectsList /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Moonlit Garden" });
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索作品" }), { target: { value: "ALICE" } });
  expect(screen.getAllByRole("article")).toHaveLength(1);
  fireEvent.change(screen.getByRole("combobox", { name: "正文状态" }), { target: { value: "completed" } });
  fireEvent.change(screen.getByRole("combobox", { name: "封面状态" }), { target: { value: "needs_attention" } });
  expect(screen.getAllByRole("article")).toHaveLength(1);
  expect(screen.getByText("1 / 2 部作品")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: "封面状态" }), { target: { value: "selected" } });
  expect(screen.queryByRole("article")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "没有符合条件的作品" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
  expect(screen.getAllByRole("article")).toHaveLength(2);
  expect(screen.getByRole("searchbox")).toHaveValue("");
});

test("restores filters from the address", async () => {
  vi.spyOn(client.api, "projects").mockResolvedValue([
    { id: "book-a", title: "Garden", genre: "Mystery", chapter_count: 3, status: "completed",
      cover: { status: "needs_attention", detail: "需修复", candidate_count: 4 } },
    { id: "book-b", title: "Shore", genre: "Drama", chapter_count: 2, status: "completed" },
  ]);
  vi.spyOn(client.api, "studioLlm").mockRejectedValue(new Error("not configured"));
  render(<MemoryRouter initialEntries={["/?q=Garden&cover=needs_attention&manuscript=completed"]}><ProjectsList /></MemoryRouter>);
  await screen.findByRole("heading", { name: "Garden" });
  expect(screen.getAllByRole("article")).toHaveLength(1);
  expect(screen.getByRole("searchbox")).toHaveValue("Garden");
  expect(screen.getByRole("combobox", { name: "封面状态" })).toHaveValue("needs_attention");
});

test("polls active cover work after writing has completed", async () => {
  const project: client.ProjectSummary = { id: "cover-book", title: "封面正在处理", genre: "Drama", chapter_count: 2, status: "completed",
    cover: { status: "processing", detail: "后台处理中", candidate_count: 4 } };
  const list = vi.spyOn(client.api, "projects").mockResolvedValueOnce([project])
    .mockResolvedValue([{ ...project, cover: { status: "awaiting_review", detail: "等待确认", candidate_count: 4 } }]);
  vi.spyOn(client.api, "studioLlm").mockRejectedValue(new Error("not configured"));
  vi.useFakeTimers();
  await act(async () => { render(<MemoryRouter><ProjectsList /></MemoryRouter>); });
  expect(screen.getByText("处理中")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS); });
  expect(screen.getByText("待确认")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS * 3); });
  expect(list).toHaveBeenCalledTimes(2);
});

test("creates a manuscript with expanded hybrid themes and preserves every selection", async () => {
  vi.spyOn(client.api, "projects").mockResolvedValue([]);
  vi.spyOn(client.api, "studioLlm").mockRejectedValue(new Error("not configured"));
  const create = vi.spyOn(client.api, "createProject").mockResolvedValue({
    id: "new-door", title: "新的门", genre: "", chapter_count: 0, status: "draft",
  });
  const user = userEvent.setup();
  render(<MemoryRouter><ProjectsList /></MemoryRouter>);
  await user.click((await screen.findAllByRole("button", { name: /新建作品/ }))[0]);
  for (const label of ["复仇", "伦理", "女性成长", "男性成长", "CEO", "黑手党", "狼人", "玄幻", "恐怖", "家庭背叛", "霸总", "名人明星", "受到虐待", "皇后女王", "一见钟情", "办公室恋情", "职场闹剧", "同性恋", "单身父母亲", "富豪", "怀孕", "末日降临"]) {
    expect(screen.getByRole("button", { name: label, exact: true })).toBeInTheDocument();
  }
  await user.type(screen.getByPlaceholderText("例如：最后的信号"), "新的门");
  for (const label of ["家庭背叛", "女性成长", "怀孕", "单身父母亲"]) {
    await user.click(screen.getByRole("button", { name: label, exact: true }));
  }
  await user.click(screen.getByRole("button", { name: "创建作品" }));
  await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({
    title: "新的门",
    genres: ["Domestic Betrayal", "Female Growth", "Pregnancy", "Single Parent"],
  })));
});

test("renders project cards in the task-start order returned by the API", async () => {
  vi.spyOn(client.api, "projects").mockResolvedValue([
    {
      id: "the-last-signal", title: "The Last Signal", genre: "Sci-Fi", chapter_count: 3,
      status: "completed", task_id: "latest-run", task_started_at: "2026-10-08T10:00:00Z",
      updated_at: "2026-10-08T11:00:00Z",
    },
    {
      id: "older-book", title: "Older Book", genre: "Sci-Fi", chapter_count: 2,
      status: "paused", task_id: "older-run", task_started_at: "2026-10-07T10:00:00Z",
      updated_at: "2026-10-09T11:00:00Z",
    },
  ]);
  vi.spyOn(client.api, "studioLlm").mockRejectedValue(new Error("not configured"));
  render(<MemoryRouter><ProjectsList /></MemoryRouter>);
  expect(await screen.findByText("The Last Signal")).toBeInTheDocument();
  expect(screen.getByText(/已写 0\/3 章/)).toBeInTheDocument();
  expect(screen.getByText("按任务启动时间倒序排列")).toBeInTheDocument();
  expect(screen.getAllByRole("article").map((article) => article.querySelector("a")?.getAttribute("href")))
    .toEqual(["/projects/the-last-signal", "/projects/older-book"]);
  expect(screen.getByText("latest-run")).toBeInTheDocument();
  expect(screen.getByText("已完成")).toBeInTheDocument();
  expect(screen.getByText("已暂停")).toBeInTheDocument();
});

test("resumes the existing run and refreshes cards until the durable task becomes terminal", async () => {
  const project = {
    id: "retry-book", title: "可以恢复的作品", genre: "科幻", chapter_count: 3,
    status: "failed", task_id: "original-run", task_can_resume: true,
  };
  const list = vi.spyOn(client.api, "projects")
    .mockResolvedValueOnce([project])
    .mockResolvedValueOnce([{ ...project, status: "running", task_can_resume: false }])
    .mockResolvedValue([{ ...project, status: "completed", task_can_resume: false }]);
  vi.spyOn(client.api, "studioLlm").mockRejectedValue(new Error("not configured"));
  const resume = vi.spyOn(client.api, "resumeProjectTask").mockResolvedValue({ job_id: "job-resume", status: "running", run_id: "original-run" });
  render(<MemoryRouter><ProjectsList /></MemoryRouter>);
  const button = await screen.findByRole("button", { name: "恢复重试" });
  vi.useFakeTimers();
  await act(async () => fireEvent.click(button));
  expect(resume).toHaveBeenCalledWith("retry-book", "original-run");
  expect(screen.getByText("运行中")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS); });
  expect(screen.getByText("运行中")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS); });
  expect(screen.getByText("已完成")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "恢复重试" })).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS * 3); });
  expect(list).toHaveBeenCalledTimes(3);
});

test("previews and permanently deletes every resource owned by a project", async () => {
  const project = {
    id: "last-signal",
    title: "最后的信号",
    genre: "科幻",
    chapter_count: 3,
    status: "in_progress",
  };
  vi.spyOn(client.api, "projects").mockResolvedValue([project]);
  vi.spyOn(client.api, "studioLlm").mockRejectedValue(new Error("not configured"));
  const preview = vi.spyOn(client.api, "projectDeletionPreview").mockResolvedValue({
    project_id: project.id,
    title: project.title,
    chapter_count: 3,
    exists: true,
    counts: {
      projects: 1,
      chapters: 3,
      artifacts: 8,
      artifact_revisions: 4,
      snapshots: 2,
      comments: 1,
      evaluation_reports: 2,
      quality_findings: 3,
      promotion_receipts: 1,
      media: 2,
    },
    project_files: 18,
    project_bytes: 8192,
    media_files: 2,
    media_bytes: 4096,
    running_job_ids: [],
    can_delete: true,
  });
  const destroy = vi.spyOn(client.api, "deleteProject").mockResolvedValue({
    project_id: project.id,
    status: "deleted",
    before: {} as client.ProjectDeletionInventory,
    after: {} as client.ProjectDeletionInventory,
    cleared_consequence_previews: 0,
  });
  const user = userEvent.setup();

  render(<MemoryRouter><ProjectsList /></MemoryRouter>);
  expect(await screen.findByText(project.title)).toBeInTheDocument();

  await user.click(screen.getByLabelText(`管理作品《${project.title}》`));
  await user.click(screen.getByRole("button", { name: "永久删除作品" }));
  expect(await screen.findByText("此操作无法撤销")).toBeInTheDocument();
  expect(preview).toHaveBeenCalledWith(project.id);

  const confirmation = await screen.findByLabelText(`请输入书名“${project.title}”确认删除`);
  await user.type(confirmation, project.title);
  await user.click(screen.getByRole("button", { name: "永久删除" }));

  await waitFor(() => expect(destroy).toHaveBeenCalledWith(project.id, project.title));
  await waitFor(() => expect(screen.queryByText(project.title)).not.toBeInTheDocument());
});

test("blocks deletion while a project task is running", async () => {
  const project = {
    id: "busy-book",
    title: "仍在生成的小说",
    genre: "悬疑",
    chapter_count: 2,
    status: "in_progress",
  };
  vi.spyOn(client.api, "projects").mockResolvedValue([project]);
  vi.spyOn(client.api, "studioLlm").mockRejectedValue(new Error("not configured"));
  vi.spyOn(client.api, "projectDeletionPreview").mockResolvedValue({
    project_id: project.id,
    title: project.title,
    chapter_count: 2,
    exists: true,
    counts: {},
    project_files: 4,
    project_bytes: 1024,
    media_files: 0,
    media_bytes: 0,
    running_job_ids: ["job-1"],
    can_delete: false,
  });
  const user = userEvent.setup();

  render(<MemoryRouter><ProjectsList /></MemoryRouter>);
  await screen.findByText(project.title);
  await user.click(screen.getByLabelText(`管理作品《${project.title}》`));
  await user.click(screen.getByRole("button", { name: "永久删除作品" }));

  expect(await screen.findByText(/仍有 1 个任务正在运行/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "永久删除" })).toBeDisabled();
});

test("keeps the project visible when permanent deletion fails", async () => {
  const project = {
    id: "renamed-book",
    title: "改名前的小说",
    genre: "剧情",
    chapter_count: 1,
    status: "in_progress",
  };
  vi.spyOn(client.api, "projects").mockResolvedValue([project]);
  vi.spyOn(client.api, "studioLlm").mockRejectedValue(new Error("not configured"));
  vi.spyOn(client.api, "projectDeletionPreview").mockResolvedValue({
    project_id: project.id,
    title: project.title,
    chapter_count: 1,
    exists: true,
    counts: {},
    project_files: 2,
    project_bytes: 512,
    media_files: 0,
    media_bytes: 0,
    running_job_ids: [],
    can_delete: true,
  });
  vi.spyOn(client.api, "deleteProject").mockRejectedValue(
    new Error("小说标题已变化，请重新预览后再删除。"),
  );
  const user = userEvent.setup();

  render(<MemoryRouter><ProjectsList /></MemoryRouter>);
  await screen.findByText(project.title);
  await user.click(screen.getByLabelText(`管理作品《${project.title}》`));
  await user.click(screen.getByRole("button", { name: "永久删除作品" }));
  await user.type(
    await screen.findByLabelText(`请输入书名“${project.title}”确认删除`),
    project.title,
  );
  await user.click(screen.getByRole("button", { name: "永久删除" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("小说标题已变化");
  expect(screen.getByText(project.title)).toBeInTheDocument();
});
