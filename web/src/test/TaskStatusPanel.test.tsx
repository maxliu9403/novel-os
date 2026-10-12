import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { api, type ProjectTaskDetail } from "../api/client";
import TaskStatusPanel from "../components/TaskStatusPanel";
import { TASK_POLL_INTERVAL_MS } from "../lib/projectTasks";

const task: ProjectTaskDetail = {
  run_id: "b1bd6c12ef6b",
  status: "failed",
  started_at: "2026-10-08T09:00:00Z",
  updated_at: "2026-10-08T11:29:24.691734Z",
  current_phase: "publication.copy",
  current_chapter: null,
  error: "LLMError: Style Curator conflict extractor model call failed (LLMError)",
  can_resume: true,
  failed_stages: [{
    phase: "publication.copy", chapter: null,
    error: "原始出版阶段错误",
    started_at: "2026-10-08T11:20:00Z", finished_at: "2026-10-08T11:29:24.691734Z",
  }],
};

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("shows the durable failure, stage history, and copyable native resume command", async () => {
  vi.spyOn(api, "projectTask").mockResolvedValue(task);
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  render(<TaskStatusPanel projectId="p" runId={task.run_id} />);

  expect(await screen.findByText(task.error!)).toBeInTheDocument();
  expect(screen.getAllByText("出版文案生成")).toHaveLength(2);
  expect(screen.getByText(/开始：.*结束：/)).toBeInTheDocument();
  expect(screen.getByText(/校验并复用已完成且有效的阶段/)).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "复制原始错误" })[0]);
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(task.error));
  fireEvent.click(screen.getByText("在终端恢复此任务"));
  fireEvent.click(screen.getByRole("button", { name: "复制命令" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith("./deploy.sh novel-resume b1bd6c12ef6b"));
});

test("submits once, keeps polling a running manifest after job done, and stops at task failure", async () => {
  const read = vi.spyOn(api, "projectTask")
    .mockResolvedValueOnce(task)
    .mockResolvedValueOnce({ ...task, status: "running", can_resume: false, resume_job_status: "running" })
    .mockResolvedValueOnce({ ...task, status: "running", can_resume: false, resume_job_status: "done" })
    .mockResolvedValue({ ...task, error: "恢复后的阶段仍然失败", resume_job_status: "done" });
  let accept: (value: { run_id: string; job_id: string; status: "running" }) => void = () => {};
  const resume = vi.spyOn(api, "resumeProjectTask").mockImplementation(() => new Promise((resolve) => { accept = resolve; }));
  const job = vi.spyOn(api, "getJob");
  const changed = vi.fn();
  render(<TaskStatusPanel projectId="p" runId={task.run_id} onTaskChanged={changed} />);
  const button = await screen.findByRole("button", { name: "恢复重试" });

  vi.useFakeTimers();
  fireEvent.click(button);
  expect(screen.getByRole("button", { name: "恢复中…" })).toBeDisabled();
  fireEvent.click(button);
  expect(resume).toHaveBeenCalledTimes(1);
  expect(resume).toHaveBeenCalledWith("p", task.run_id);
  await act(async () => accept({ run_id: task.run_id, job_id: "resume-1", status: "running" }));
  expect(screen.getByText("运行中")).toBeInTheDocument();
  expect(screen.getByText("上次失败原因")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS); });
  expect(read).toHaveBeenCalledTimes(3);
  expect(screen.getByText("运行中")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS); });
  expect(screen.getByText("恢复后的阶段仍然失败")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "恢复重试" })).toBeEnabled();
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS * 3); });
  expect(read).toHaveBeenCalledTimes(4);
  expect(job).not.toHaveBeenCalled();
  expect(changed).toHaveBeenCalledTimes(2);
});

test("shows submit errors and permits retry without losing the saved failure", async () => {
  vi.spyOn(api, "projectTask").mockResolvedValue(task);
  vi.spyOn(api, "resumeProjectTask").mockRejectedValue(new Error("已有任务运行，请稍后重试"));
  render(<TaskStatusPanel projectId="p" runId={task.run_id} />);
  fireEvent.click(await screen.findByRole("button", { name: "恢复重试" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("已有任务运行");
  expect(screen.getByText(task.error!)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "恢复重试" })).toBeEnabled();
});

test("prioritizes resume setup errors while keeping failed stage evidence", async () => {
  vi.spyOn(api, "projectTask").mockResolvedValue({ ...task, resume_job_error: "任务锁被其他进程持有" });
  render(<TaskStatusPanel projectId="p" runId={task.run_id} />);
  expect(await screen.findByText("任务锁被其他进程持有")).toBeInTheDocument();
  expect(screen.getByText("本次恢复失败原因")).toBeInTheDocument();
  expect(screen.getByText("原始出版阶段错误")).toBeInTheDocument();
  expect(screen.getByText(task.error!)).toBeInTheDocument();
});

test("refreshes a stopped task on focus and visibility after an external resume", async () => {
  const read = vi.spyOn(api, "projectTask")
    .mockResolvedValueOnce(task)
    .mockResolvedValueOnce({ ...task, status: "running", current_phase: "chapter.style", can_resume: false })
    .mockResolvedValue({ ...task, status: "completed", current_phase: "delivery.package", can_resume: false, error: null, failed_stages: [] });
  render(<TaskStatusPanel projectId="p" runId={task.run_id} />);
  expect(await screen.findByText("失败原因")).toBeInTheDocument();
  fireEvent.focus(window);
  expect(await screen.findByText("运行中")).toBeInTheDocument();
  expect(screen.getByText("风格校验")).toBeInTheDocument();
  fireEvent(document, new Event("visibilitychange"));
  expect(await screen.findByText("已完成")).toBeInTheDocument();
  expect(screen.getByText("生成交付包")).toBeInTheDocument();
  expect(read).toHaveBeenCalledTimes(3);
});

test("does not offer resume for completed or incompatible tasks", async () => {
  vi.spyOn(api, "projectTask").mockResolvedValue({ ...task, status: "completed", error: null, failed_stages: [], can_resume: false });
  render(<TaskStatusPanel projectId="p" runId={task.run_id} />);
  expect(await screen.findByText("已完成")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "恢复重试" })).not.toBeInTheDocument();
});

test("retries transient polling failures and cancels polling on unmount", async () => {
  vi.useFakeTimers();
  const read = vi.spyOn(api, "projectTask")
    .mockResolvedValueOnce({ ...task, status: "running", can_resume: false })
    .mockRejectedValueOnce(new Error("connection unavailable"))
    .mockResolvedValue({ ...task, status: "running", can_resume: false });
  let unmount: () => void = () => {};
  await act(async () => { ({ unmount } = render(<TaskStatusPanel projectId="p" runId={task.run_id} />)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS); });
  expect(screen.getByRole("alert")).toHaveTextContent("connection unavailable");
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS); });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS * 3); });
  expect(read).toHaveBeenCalledTimes(3);
});
