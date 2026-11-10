import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { api, ApiError, type ProjectTaskDetail } from "../api/client";
import TaskStatusPanel from "../components/TaskStatusPanel";
import { TASK_IDLE_POLL_INTERVAL_MS, TASK_POLL_INTERVAL_MS } from "../lib/projectTasks";

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

test('source restoration is an explicit action and never starts on inspection', async () => {
  vi.spyOn(api, 'projectTask').mockResolvedValue({ ...task, current_phase: 'outline', planning: {
    status: 'failed', completed_batches: 4, total_batches: 4, chapter_count: 16,
    active_step: 'semantic_review', updated_at: null, steps: [], milestones: [],
    recovery: { retryable: true, action: 'restore_source_omissions', reason: '来源补全后完整核查，会产生模型用量。' },
  } });
  const resume = vi.spyOn(api, 'resumeProjectTask').mockResolvedValue({ run_id: task.run_id, job_id: 'source', status: 'running' });
  render(<TaskStatusPanel projectId='p' runId={task.run_id} />);
  const button = await screen.findByRole('button', { name: '按新版补全并恢复' });
  expect(resume).not.toHaveBeenCalled();
  fireEvent.click(button);
  expect(resume).toHaveBeenCalledTimes(1);
  expect(resume).toHaveBeenCalledWith('p', task.run_id);
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

test("submits once, keeps polling a running manifest after job done, and slows polling at task failure", async () => {
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
  expect(screen.getByText("检查与保存候选")).toBeInTheDocument();
  fireEvent(document, new Event("visibilitychange"));
  expect(await screen.findByText("已完成")).toBeInTheDocument();
  expect(screen.getByText("生成交付包")).toBeInTheDocument();
  expect(read).toHaveBeenCalledTimes(3);
});

test("a visible failed task detects an external resume without focus or a reload", async () => {
  vi.useFakeTimers();
  const error = "ValueError: themes must contain only strings";
  const read = vi.spyOn(api, "projectTask")
    .mockResolvedValueOnce({ ...task, error, failed_stages: [] })
    .mockResolvedValueOnce({ ...task, status: "running", current_phase: "chapter.write", current_chapter: 5,
      can_resume: false, error: null, failed_stages: [] })
    .mockResolvedValue({ ...task, status: "completed", can_resume: false, error: null, failed_stages: [] });
  const resume = vi.spyOn(api, "resumeProjectTask");
  const changed = vi.fn();
  await act(async () => { render(<TaskStatusPanel projectId="p" runId={task.run_id} onTaskChanged={changed} />); });
  expect(screen.getByText(error)).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_IDLE_POLL_INTERVAL_MS - 1); });
  expect(read).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(screen.queryByText(error)).not.toBeInTheDocument();
  expect(screen.getByText("运行中")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "恢复重试" })).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS); });
  expect(screen.getByText("已完成")).toBeInTheDocument();
  expect(read).toHaveBeenCalledTimes(3);
  expect(changed).toHaveBeenCalledTimes(2);
  expect(resume).not.toHaveBeenCalled();
});

test("manual status refresh clears an old failure without resuming the task", async () => {
  vi.spyOn(api, "projectTask").mockResolvedValueOnce(task)
    .mockResolvedValue({ ...task, status: "running", can_resume: false, error: null, failed_stages: [] });
  const resume = vi.spyOn(api, "resumeProjectTask");
  render(<TaskStatusPanel projectId="p" runId={task.run_id} />);
  expect(await screen.findByText(task.error!)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
  expect(await screen.findByText("运行中")).toBeInTheDocument();
  expect(screen.queryByText(task.error!)).not.toBeInTheDocument();
  expect(resume).not.toHaveBeenCalled();
});

test("stopped task polling survives a read failure and stops when unmounted", async () => {
  vi.useFakeTimers();
  const read = vi.spyOn(api, "projectTask").mockResolvedValueOnce(task)
    .mockRejectedValueOnce(new Error("connection unavailable"))
    .mockResolvedValue({ ...task, status: "paused" });
  let unmount = () => {};
  await act(async () => { ({ unmount } = render(<TaskStatusPanel projectId="p" runId={task.run_id} />)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_IDLE_POLL_INTERVAL_MS); });
  expect(screen.getByRole("alert")).toHaveTextContent("connection unavailable");
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_IDLE_POLL_INTERVAL_MS); });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByText("已暂停")).toBeInTheDocument();
  unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_IDLE_POLL_INTERVAL_MS * 2); });
  expect(read).toHaveBeenCalledTimes(3);
});

test("hidden task panels skip polling and catch up on becoming visible", async () => {
  vi.useFakeTimers();
  const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  const read = vi.spyOn(api, "projectTask").mockResolvedValueOnce(task)
    .mockResolvedValue({ ...task, status: "running", error: null, failed_stages: [], can_resume: false });
  await act(async () => { render(<TaskStatusPanel projectId="p" runId={task.run_id} />); });
  visibility.mockReturnValue("hidden");
  fireEvent(document, new Event("visibilitychange"));
  await act(async () => { await vi.advanceTimersByTimeAsync(TASK_IDLE_POLL_INTERVAL_MS * 3); });
  expect(read).toHaveBeenCalledTimes(1);
  visibility.mockReturnValue("visible");
  await act(async () => { fireEvent(document, new Event("visibilitychange")); });
  expect(screen.getByText("运行中")).toBeInTheDocument();
  expect(screen.queryByText(task.error!)).not.toBeInTheDocument();
  expect(read).toHaveBeenCalledTimes(2);
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

const runningTask: ProjectTaskDetail = {
  ...task, status: "running", error: null, failed_stages: [], can_resume: false,
  can_pause: true, active_job_id: "job-a", cancel_requested: false,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => { resolve = accept; });
  return { promise, resolve };
}

test("pause targets the exact job once and waits for a fresh terminal GET, even after a stale poll", async () => {
  const stalePoll = deferred<ProjectTaskDetail>();
  const pauseAccepted = deferred<ProjectTaskDetail>();
  const read = vi.spyOn(api, "projectTask")
    .mockResolvedValueOnce(runningTask)
    .mockReturnValueOnce(stalePoll.promise)
    // A server snapshot can precede the cancellation flag. Keep the local fence.
    .mockResolvedValueOnce(runningTask)
    .mockResolvedValue({ ...runningTask, status: "paused", can_resume: true, can_pause: false, active_job_id: null });
  const pause = vi.spyOn(api, "pauseProjectTask").mockReturnValue(pauseAccepted.promise);
  render(<TaskStatusPanel projectId="p" runId={task.run_id} />);
  const button = await screen.findByRole("button", { name: "暂停任务" });
  fireEvent.focus(window);
  await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  fireEvent.click(button);
  fireEvent.click(button);
  expect(pause).toHaveBeenCalledTimes(1);
  expect(pause).toHaveBeenCalledWith("p", task.run_id, "job-a");
  expect(screen.getByRole("button", { name: "正在暂停…" })).toBeDisabled();
  await act(async () => stalePoll.resolve(runningTask));
  expect(screen.queryByText("已暂停")).not.toBeInTheDocument();
  await act(async () => pauseAccepted.resolve({ ...runningTask, cancel_requested: true }));
  await waitFor(() => expect(read).toHaveBeenCalledTimes(3));
  expect(screen.getByRole("button", { name: "正在暂停…" })).toBeDisabled();
  expect(screen.getByText("运行中")).toBeInTheDocument();
  fireEvent.focus(window);
  expect(await screen.findByText("已暂停")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "恢复重试" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "正在暂停…" })).not.toBeInTheDocument();
});

test("a pause conflict refreshes the current job and never silently pauses its replacement", async () => {
  vi.spyOn(api, "projectTask")
    .mockResolvedValueOnce(runningTask)
    .mockResolvedValue({ ...runningTask, active_job_id: "replacement-job" });
  const pause = vi.spyOn(api, "pauseProjectTask").mockRejectedValue(new ApiError("当前执行已变更", 409));
  render(<TaskStatusPanel projectId="p" runId={task.run_id} />);
  fireEvent.click(await screen.findByRole("button", { name: "暂停任务" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("当前执行已变更");
  expect(screen.getByRole("button", { name: "暂停任务" })).toBeEnabled();
  expect(screen.queryByText("已暂停")).not.toBeInTheDocument();
  expect(pause).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "暂停任务" }));
  await waitFor(() => expect(pause).toHaveBeenLastCalledWith("p", task.run_id, "replacement-job"));
});

test("refresh restores a requested pause without allowing another request", async () => {
  vi.spyOn(api, "projectTask").mockResolvedValue({ ...runningTask, can_pause: false, cancel_requested: true });
  const pause = vi.spyOn(api, "pauseProjectTask");
  render(<TaskStatusPanel projectId="p" runId={task.run_id} />);
  expect(await screen.findByRole("button", { name: "正在暂停…" })).toBeDisabled();
  expect(screen.getByText("运行中")).toBeInTheDocument();
  expect(screen.queryByText("已暂停")).not.toBeInTheDocument();
  expect(pause).not.toHaveBeenCalled();
});

test("an acknowledged cancellation becomes a paused task, not a recovery failure", async () => {
  const stopped = {
    ...runningTask, status: "paused", can_resume: true, can_pause: false,
    active_job_id: null, cancel_requested: false,
    resume_job_status: "error", resume_job_error: "任务已暂停。",
    error: "cancelled: 已停止当前模型请求，保留已保存成果。",
    failed_stages: [{ phase: "outline", chapter: null, status: "blocked", error: "cancelled: 用户请求暂停", started_at: task.started_at, finished_at: task.updated_at }],
  };
  vi.spyOn(api, "projectTask")
    .mockResolvedValueOnce({ ...runningTask, cancel_requested: true, can_pause: false })
    .mockResolvedValue(stopped);
  render(<TaskStatusPanel projectId="p" runId={task.run_id} />);
  expect(await screen.findByRole("button", { name: "正在暂停…" })).toBeDisabled();
  fireEvent.focus(window);
  expect(await screen.findByText("暂停原因")).toBeInTheDocument();
  expect(screen.getByText("已暂停")).toBeInTheDocument();
  expect(screen.getByText("阶段停止记录（1）")).toBeInTheDocument();
  expect(screen.getByText("阶段停止原因")).toBeInTheDocument();
  expect(screen.queryByText("本次恢复失败原因")).not.toBeInTheDocument();
  expect(screen.queryByText(/失败阶段记录/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "正在暂停…" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "恢复重试" })).toBeEnabled();
});

test("a stage budget pause explains the timeout without claiming recovery failed", async () => {
  const reason = "stage_timeout: 当前阶段已达到时间预算，保留已验证检查点；不会自动重试。";
  vi.spyOn(api, "projectTask").mockResolvedValue({
    ...task, status: "paused", resume_job_status: "error", resume_job_error: `RuntimeError: ${reason}`,
    error: reason, failed_stages: [{ ...task.failed_stages[0], error: reason, status: "blocked" }],
  });
  render(<TaskStatusPanel projectId="p" runId={task.run_id} />);
  expect(await screen.findByText("暂停原因")).toBeInTheDocument();
  expect(screen.getAllByText(reason)).toHaveLength(2);
  expect(screen.getByText("阶段停止记录（1）")).toBeInTheDocument();
  expect(screen.queryByText("本次恢复失败原因")).not.toBeInTheDocument();
  expect(screen.queryByText("失败原因")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "恢复重试" })).toBeEnabled();
});

test("older executions do not get a fake pause control or model activity claim", async () => {
  vi.spyOn(api, "projectTask").mockResolvedValue({ ...runningTask, can_pause: undefined, active_job_id: undefined });
  render(<TaskStatusPanel projectId="p" runId={task.run_id} />);
  expect(await screen.findByText(/此执行未提供模型活动记录/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "暂停任务" })).not.toBeInTheDocument();
  expect(screen.getByText(/当前执行不支持页面暂停/)).toBeInTheDocument();
});

test("switching runs discards previous data and late pause responses", async () => {
  const nextRead = deferred<ProjectTaskDetail>();
  const pendingPause = deferred<ProjectTaskDetail>();
  const read = vi.spyOn(api, "projectTask").mockResolvedValueOnce(runningTask).mockReturnValue(nextRead.promise);
  vi.spyOn(api, "pauseProjectTask").mockReturnValue(pendingPause.promise);
  const changed = vi.fn();
  const { rerender } = render(<TaskStatusPanel projectId="p" runId={task.run_id} onTaskChanged={changed} />);
  fireEvent.click(await screen.findByRole("button", { name: "暂停任务" }));
  rerender(<TaskStatusPanel projectId="p" runId="run-b" onTaskChanged={changed} />);
  expect(screen.queryByRole("button", { name: "正在暂停…" })).not.toBeInTheDocument();
  expect(screen.getByText("正在加载任务状态…")).toBeInTheDocument();
  expect(screen.queryByText(task.run_id)).not.toBeInTheDocument();
  await act(async () => pendingPause.resolve({ ...runningTask, status: "paused" }));
  await act(async () => nextRead.resolve({ ...runningTask, run_id: "run-b", active_job_id: "job-b" }));
  expect(screen.getByRole("button", { name: "暂停任务" })).toBeEnabled();
  expect(read).toHaveBeenCalledTimes(2);
  expect(changed).not.toHaveBeenCalled();
});

test("switching runs also discards late resume mutations", async () => {
  const resumed = deferred<{ job_id: string; run_id: string; status: "running" }>();
  vi.spyOn(api, "projectTask").mockResolvedValueOnce(task).mockResolvedValue({ ...task, run_id: "run-b", error: "第二个任务的错误" });
  vi.spyOn(api, "resumeProjectTask").mockReturnValue(resumed.promise);
  const changed = vi.fn();
  const { rerender } = render(<TaskStatusPanel projectId="p" runId={task.run_id} onTaskChanged={changed} />);
  fireEvent.click(await screen.findByRole("button", { name: "恢复重试" }));
  rerender(<TaskStatusPanel projectId="p" runId="run-b" onTaskChanged={changed} />);
  expect(await screen.findByText("第二个任务的错误")).toBeInTheDocument();
  await act(async () => resumed.resolve({ run_id: task.run_id, job_id: "old-job", status: "running" }));
  expect(screen.queryByText("运行中")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "恢复重试" })).toBeEnabled();
  expect(changed).not.toHaveBeenCalled();
});

test("eligible legacy runs use the ordinary resume action", async () => {
  vi.spyOn(api, "projectTask").mockResolvedValue({ ...task, can_upgrade_planning: true });
  const resume = vi.spyOn(api, "resumeProjectTask").mockRejectedValue(new Error("离线测试"));
  render(<TaskStatusPanel projectId="p" runId={task.run_id} />);
  fireEvent.click(await screen.findByRole("button", { name: "恢复重试" }));
  await waitFor(() => expect(resume).toHaveBeenCalledWith("p", task.run_id));
  expect(await screen.findByRole("button", { name: "恢复重试" })).toBeEnabled();
});

test("asks for a story choice only from verified original passages and sends no default", async () => {
  const plan = {status:"failed",active_step:"semantic_review",updated_at:null,completed_batches:3,
    total_batches:3,chapter_count:12,steps:[],milestones:[],author_decisions:{request_id:"canon-1",
      issues:[{issue_index:4,affected_chapters:[2,8],reason:"同一事件有两个结局",author_quotes:["她留下了。","她离开了。"] as [string,string]}]}};
  vi.spyOn(api, "projectTask").mockResolvedValue({...task, can_resume:false, planning:plan});
  const post = vi.spyOn(api, "resumeProjectTask").mockResolvedValue({job_id:"j",run_id:task.run_id,status:"running"});
  render(<TaskStatusPanel projectId="p" runId={task.run_id} />);
  const button = await screen.findByRole("button",{name:"按所选原文继续"});
  expect(button).toBeDisabled();
  expect(screen.queryByRole("button",{name:"恢复重试"})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("radio",{name:"她离开了。"}));
  fireEvent.click(button);
  await waitFor(() => expect(post).toHaveBeenCalledWith("p",task.run_id,{author_decisions:{
    request_id:"canon-1",choices:[{issue_index:4,selected_quote:1}]}}));
});
