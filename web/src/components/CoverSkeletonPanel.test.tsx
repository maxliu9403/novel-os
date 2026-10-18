import { act, cleanup, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import CoverSkeletonPanel from "./CoverSkeletonPanel";
import { useCoverAnalysis } from "./useCoverAnalysis";
import { api, type CoverSourcePreview, type CoverStoryAnalysis, type JobStatus } from "../api/client";

afterEach(() => {
  cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); localStorage.clear(); sessionStorage.clear();
});
const preview: CoverSourcePreview = {
  selector: {source_type: "project_input", project_id: "book"}, source_content_sha256: "a".repeat(64),
  source_bundle_sha256: "b".repeat(64), primary_text: "Mara withdraws the key.",
  origin: {relative_path: "outputs/input/prompt.md"}, documents: [{document_id: "primary", byte_length: 25, sha256: "a".repeat(64)}],
  analyses: [],
};
const analysis: CoverStoryAnalysis = {
  analysis_id: "analysis-a", snapshot_id: "snapshot-a", coverage_status: "complete", phase: "complete",
  execution_status: "complete", source_selector: preview.selector, source_bundle_sha256: preview.source_bundle_sha256,
  story: {core_conflict: "Mara refuses access", evidence_refs: ["evidence:span-a"]},
  characters: [{character_id: "mara", name: "Mara"}],
  evidence: [{evidence_id: "span-a", exact_quote: "Mara withdraws the key.", heading_path: ["The choice"], line_start: 12}],
  coverage: {expected_segment_ids: ["one", "two", "three", "four"], processed_segment_ids: ["one", "two", "three", "four"], failed_segment_ids: []},
};
function savedSource(...values: CoverStoryAnalysis[]): CoverSourcePreview {
  return {...preview, analyses: values.map(value => ({
    analysis_id: value.analysis_id, snapshot_id: value.snapshot_id, coverage_status: value.coverage_status,
    phase: value.phase, job_id: value.job_id, job_status: value.job_status, job_error: value.job_error,
    cancel_requested: value.cancel_requested,
    execution_status: value.execution_status, source_selector: value.source_selector,
    source_bundle_sha256: value.source_bundle_sha256, updated_at: value.updated_at,
  }))};
}
function partial(done = 1, extra: Partial<CoverStoryAnalysis> = {}): CoverStoryAnalysis {
  return {...analysis, coverage_status: "partial", phase: "segment", execution_status: "running",
    job_id: "cover-job", job_status: "running", updated_at: "2026-10-10T10:00:00Z",
    coverage: {...analysis.coverage, processed_segment_ids: ["one", "two", "three", "four"].slice(0, done)}, ...extra};
}
function runningJob(extra: Partial<JobStatus> = {}): JobStatus {
  return {job_id: "cover-job", kind: "cover.analyze", status: "running", error: null,
    meta: {analysis_id: "analysis-a", project_id: "book", snapshot_id: "snapshot-a"}, ...extra};
}
function readOnlyGuards() {
  const analyze = vi.spyOn(api, "analyzeCover").mockRejectedValue(new Error("Unexpected analysis submission"));
  const resume = vi.spyOn(api, "resumeCoverAnalysis").mockRejectedValue(new Error("Unexpected analysis resume"));
  const cancel = vi.spyOn(api, "cancelCoverAnalysis").mockRejectedValue(new Error("Unexpected analysis cancellation"));
  const jobs = vi.spyOn(api, "getJob").mockResolvedValue(runningJob());
  const deletionPreview = vi.spyOn(api, "projectDeletionPreview").mockResolvedValue({
    project_id: "book", title: "Book", chapter_count: 0, exists: true, counts: {},
    project_files: 1, project_bytes: 25, media_files: 0, media_bytes: 0, running_job_ids: [], can_delete: true,
  });
  return {analyze, resume, cancel, jobs, deletionPreview};
}
function expectNoSubmissions(guards: ReturnType<typeof readOnlyGuards>) {
  expect(guards.analyze).not.toHaveBeenCalled();
  expect(guards.resume).not.toHaveBeenCalled();
  expect(guards.cancel).not.toHaveBeenCalled();
}
async function settle() {
  await act(async () => { for (let n = 0; n < 8; n++) await Promise.resolve(); });
}
async function poll() {
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
}
function progressStage(index: number) {
  return within(screen.getByRole("list", {name: "分析阶段进度"})).getAllByRole("listitem")[index];
}

test("all extracted segments still show global synthesis and both unfinished verification stages", async () => {
  const guards = readOnlyGuards();
  const current = partial(4, {phase: "global", aggregation_progress: {groupA: {facts: []}, groupB: {facts: []}},
    attempts: [{phase: "aggregate", status: "complete"}, {phase: "global", status: "running"}]});
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(current));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(current);
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  await screen.findByText("后台分析中 · 第 2/4 阶段：骨架全局整合");
  expect(progressStage(0)).toHaveTextContent("已提取 4/4 段 · 已完成");
  expect(progressStage(1)).toHaveTextContent("进行中 · 已保存 2 组资料整合结果");
  expect(progressStage(1)).toHaveAttribute("aria-current", "step");
  expect(progressStage(0)).not.toHaveAttribute("aria-current");
  expect(progressStage(2)).toHaveTextContent("已核查 0/4 段 · 未开始");
  expect(progressStage(3)).toHaveTextContent("未开始");
  expect(screen.queryByText("已完成全段分析与全局核查")).not.toBeInTheDocument();
  expectNoSubmissions(guards);
});

test("verification progresses separately and the final check starts only after every source segment passes", async () => {
  vi.useFakeTimers();
  const guards = readOnlyGuards();
  let current = partial(4, {phase: "verify", verification_progress: {segments: {one: {status: "pass"}, two: {status: "fail"}, unknown: {status: "pass"}}},
    attempts: [{phase: "verify", mode: "source_segment", status: "running"}]});
  vi.spyOn(api, "coverSource").mockImplementation(async () => savedSource(current));
  vi.spyOn(api, "coverAnalysis").mockImplementation(async () => current);
  const chosen = vi.fn();
  render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  await settle();
  expect(screen.getByText("后台分析中 · 第 3/4 阶段：原文逐段核查")).toBeInTheDocument();
  expect(progressStage(1)).toHaveTextContent("已完成");
  expect(progressStage(2)).toHaveTextContent("已核查 1/4 段 · 进行中");
  expect(progressStage(3)).toHaveTextContent("未开始");
  current = {...current, verification_progress: {segments: Object.fromEntries(["one", "two", "three", "four"].map(id => [id, {status: "pass"}]))},
    // Legacy attempt records do not include mode; durable segment checks identify this final stage.
    attempts: [{phase: "verify", status: "running"}]};
  await poll();
  expect(screen.getByText("后台分析中 · 第 4/4 阶段：全局因果核查")).toBeInTheDocument();
  expect(progressStage(2)).toHaveTextContent("已核查 4/4 段 · 已完成");
  expect(progressStage(3)).toHaveTextContent("进行中");
  expect(chosen).not.toHaveBeenCalledWith("analysis-a", expect.anything());
  expectNoSubmissions(guards);
});

test("a synthesis failure preserves completed extraction and aggregation and resumes the same analysis", async () => {
  const guards = readOnlyGuards();
  const failed = partial(4, {phase: "failed", execution_status: "failed", job_status: "error",
    last_error: {message: "cover_brief_v2.language is required"},
    aggregation_progress: {groupA: {facts: []}, groupB: {facts: []}},
    attempts: [{phase: "aggregate", status: "complete"}, {phase: "global", status: "failed"}]});
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(failed));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(failed);
  guards.resume.mockRejectedValue(new Error("稍后继续"));
  const user = userEvent.setup();
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  const resume = await screen.findByRole("button", {name: "继续未完成分析"});
  expect(screen.getByRole("status")).toHaveTextContent("第 2/4 阶段：骨架全局整合 · 分析失败");
  expect(progressStage(0)).toHaveTextContent("已提取 4/4 段 · 已完成");
  expect(progressStage(1)).toHaveTextContent("未通过，进度已保存 · 已保存 2 组资料整合结果");
  expect(progressStage(2)).toHaveTextContent("已核查 0/4 段 · 未开始");
  expect(screen.getByRole("alert")).toHaveTextContent("cover_brief_v2.language is required");
  expectNoSubmissions(guards);
  await user.click(resume);
  expect(guards.resume).toHaveBeenCalledExactlyOnceWith("book", "analysis-a");
});

test.each(["running", "failed", "paused"] as const)("a %s synthesis correction takes precedence over earlier verification history", async execution => {
  const guards = readOnlyGuards();
  const current = partial(4, {
    phase: execution === "running" ? "global" : execution === "failed" ? "failed" : "cancelled",
    execution_status: execution, job_status: execution === "running" ? "running" : "error",
    verification_progress: {segments: {one: {status: "pass"}, two: {status: "pass"}}},
    attempts: [{phase: "verify", mode: "source_segment", status: "failed"},
      {phase: "global", status: execution === "running" ? "running" : "failed"}],
  });
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(current));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(current);
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  await screen.findByRole("list", {name: "分析阶段进度"});
  expect(screen.getByRole("status")).toHaveTextContent("第 2/4 阶段：骨架全局整合");
  expect(progressStage(0)).toHaveTextContent("已提取 4/4 段 · 已完成");
  expect(progressStage(1)).toHaveTextContent(execution === "running" ? "进行中" : execution === "failed" ? "未通过，进度已保存" : "已暂停");
  expect(progressStage(1)).not.toHaveTextContent("已完成");
  expect(progressStage(2)).toHaveTextContent("已核查 2/4 段");
  expectNoSubmissions(guards);
});

test("an explicit source verification attempt is not mistaken for a global check by old saved counts", async () => {
  const guards = readOnlyGuards();
  const current = partial(4, {phase: "failed", execution_status: "failed", job_status: "error",
    verification_progress: {segments: Object.fromEntries(["one", "two", "three", "four"].map(id => [id, {status: "pass"}]))},
    attempts: [{phase: "verify", mode: "global_cross_segment", status: "failed"},
      {phase: "global", status: "complete"}, {phase: "verify", mode: "source_segment", status: "failed"}],
  });
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(current));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(current);
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  await screen.findByRole("button", {name: "继续未完成分析"});
  expect(screen.getByRole("status")).toHaveTextContent("第 3/4 阶段：原文逐段核查");
  expect(progressStage(2)).toHaveTextContent("未通过，进度已保存");
  expect(progressStage(3)).toHaveTextContent("未开始");
  expectNoSubmissions(guards);
});

test.each(["failed", "paused"] as const)("%s source verification preserves both completed stages and verified segment count", async execution => {
  const guards = readOnlyGuards();
  const current = partial(4, {phase: execution === "failed" ? "failed" : "cancelled", execution_status: execution, job_status: "error",
    verification_progress: {segments: {one: {status: "pass"}, two: {status: "pass"}}},
    attempts: [{phase: "verify", status: "failed"}]});
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(current));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(current);
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  await screen.findByRole("button", {name: "继续未完成分析"});
  expect(screen.getByRole("status")).toHaveTextContent("第 3/4 阶段：原文逐段核查");
  expect(progressStage(0)).toHaveTextContent("已完成");
  expect(progressStage(1)).toHaveTextContent("已完成");
  expect(progressStage(2)).toHaveTextContent(`已核查 2/4 段 · ${execution === "failed" ? "未通过，进度已保存" : "已暂停"}`);
  expect(progressStage(3)).toHaveTextContent("未开始");
  expectNoSubmissions(guards);
});

test("resuming verification keeps its saved counts visible while polling the same analysis", async () => {
  const guards = readOnlyGuards();
  let current = partial(4, {phase: "failed", execution_status: "failed", job_status: "error",
    verification_progress: {segments: {one: {status: "pass"}, two: {status: "pass"}}},
    attempts: [{phase: "verify", mode: "source_segment", status: "failed"}]});
  vi.spyOn(api, "coverSource").mockImplementation(async () => savedSource(current));
  const read = vi.spyOn(api, "coverAnalysis").mockImplementation(async () => current);
  guards.resume.mockImplementation(async () => {
    current = {...current, phase: "verify", execution_status: "running", job_status: "running",
      attempts: [...current.attempts!, {phase: "verify", mode: "source_segment", status: "running"}]};
    return runningJob();
  });
  const chosen = vi.fn(); const user = userEvent.setup();
  render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  const resume = await screen.findByRole("button", {name: "继续未完成分析"});
  expect(progressStage(2)).toHaveTextContent("已核查 2/4 段");
  await user.click(resume);
  await screen.findByText("后台分析中 · 第 3/4 阶段：原文逐段核查");
  expect(progressStage(0)).toHaveTextContent("已提取 4/4 段 · 已完成");
  expect(progressStage(1)).toHaveTextContent("已完成");
  expect(progressStage(2)).toHaveTextContent("已核查 2/4 段 · 进行中");
  expect(guards.resume).toHaveBeenCalledExactlyOnceWith("book", "analysis-a");
  expect(read).toHaveBeenLastCalledWith("book", "analysis-a");
  expect(screen.getByRole("button", {name: "暂停分析"})).toBeEnabled();
  expect(chosen).not.toHaveBeenCalledWith("analysis-a", expect.anything());
  expect(guards.analyze).not.toHaveBeenCalled();
});

test("a failed final gate is not shown as complete even when its model check passed", async () => {
  const guards = readOnlyGuards();
  const current = partial(4, {phase: "failed", execution_status: "failed", job_status: "error",
    verification: {status: "pass", verified_segment_ids: ["one", "two", "three", "four"]},
    attempts: [{phase: "verify", mode: "global_cross_segment", status: "complete"}]});
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(current));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(current);
  const chosen = vi.fn();
  render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  await screen.findByRole("button", {name: "继续未完成分析"});
  expect(screen.getByRole("status")).toHaveTextContent("第 4/4 阶段：全局因果核查");
  expect(progressStage(2)).toHaveTextContent("已核查 4/4 段 · 已完成");
  expect(progressStage(3)).toHaveTextContent("未通过，进度已保存");
  expect(chosen).not.toHaveBeenCalledWith("analysis-a", expect.anything());
  expectNoSubmissions(guards);
});

test("pause acceptance keeps polling and blocks resume until in-flight results are durably saved", async () => {
  vi.useFakeTimers();
  const guards = readOnlyGuards();
  let current = partial(1);
  let job = runningJob();
  vi.spyOn(api, "coverSource").mockImplementation(async () => savedSource(current));
  const read = vi.spyOn(api, "coverAnalysis").mockImplementation(async () => current);
  guards.jobs.mockImplementation(async () => job);
  guards.cancel.mockResolvedValue({analysis_id: "analysis-a", job_id: "cover-job", cancel_requested: true});
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  await settle();
  await act(async () => { screen.getByRole("button", {name: "暂停分析"}).click(); });
  expect(guards.cancel).toHaveBeenCalledExactlyOnceWith("book", "analysis-a");
  expect(screen.getByRole("button", {name: "正在暂停"})).toBeDisabled();
  expect(screen.queryByRole("button", {name: "继续未完成分析"})).not.toBeInTheDocument();
  expect(screen.getByRole("button", {name: "分析处理中"})).toBeDisabled();
  // An already-started poll may return a pre-cancel record, so false/missing
  // cancel_requested cannot unlock controls or forget the accepted request.
  const beforePoll = read.mock.calls.length;
  current = partial(2);
  await poll();
  expect(read.mock.calls.length).toBeGreaterThan(beforePoll);
  expect(progressStage(0)).toHaveTextContent("已提取 2/4 段");
  expect(screen.getByRole("button", {name: "正在暂停"})).toBeDisabled();
  await act(async () => { screen.getByRole("button", {name: "正在暂停"}).click(); });
  expect(guards.cancel).toHaveBeenCalledTimes(1);
  current = partial(3, {phase: "cancelled", execution_status: "paused", job_status: "error", cancel_requested: true});
  job = runningJob({status: "error", error: "任务已暂停。", cancel_requested: true});
  await poll();
  expect(progressStage(0)).toHaveTextContent("已提取 3/4 段");
  expect(screen.getByRole("button", {name: "继续未完成分析"})).toBeEnabled();
  expect(screen.queryByRole("button", {name: "正在暂停"})).not.toBeInTheDocument();
  expect(guards.resume).not.toHaveBeenCalled();
  expect(guards.analyze).not.toHaveBeenCalled();
});

test("a remounted cancelling task restores its stopping state and cannot resume early", async () => {
  const guards = readOnlyGuards();
  const current = partial(2, {cancel_requested: true});
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(current));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(current);
  guards.jobs.mockResolvedValue(runningJob({cancel_requested: true}));
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  expect(await screen.findByRole("button", {name: "正在暂停"})).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("正在暂停");
  expect(screen.queryByRole("button", {name: "继续未完成分析"})).not.toBeInTheDocument();
  expectNoSubmissions(guards);
});

test("opening restores a complete source-bound analysis without paid submissions", async () => {
  const guards = readOnlyGuards();
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(analysis));
  const read = vi.spyOn(api, "coverAnalysis").mockResolvedValue(analysis);
  const chosen = vi.fn();
  render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  await waitFor(() => expect(chosen).toHaveBeenLastCalledWith("analysis-a", "require_current_head"));
  expect(read).toHaveBeenCalledWith("book", "analysis-a");
  expect(screen.getByText(/查看这项理解的原文依据/)).toBeInTheDocument();
  expect(screen.getByText(/The choice · 行 12/)).toBeInTheDocument();
  expect(screen.getByText("已完成全段分析与全局核查")).toBeInTheDocument();
  expectNoSubmissions(guards);
});

test("source recovery disables analysis submission until inspection has finished", async () => {
  const guards = readOnlyGuards();
  let resolveSource!: (value: CoverSourcePreview) => void;
  vi.spyOn(api, "coverSource").mockReturnValue(new Promise(resolve => { resolveSource = resolve; }));
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  expect(screen.getByRole("button", {name: /分析/})).toBeDisabled();
  await act(async () => resolveSource(preview));
  await waitFor(() => expect(screen.getByRole("button", {name: "分析完整骨架"})).toBeEnabled());
  expectNoSubmissions(guards);
});

test("explicitly starting an analysis still submits the inspected source hash once", async () => {
  const guards = readOnlyGuards();
  vi.spyOn(api, "coverSource").mockResolvedValue(preview);
  guards.analyze.mockResolvedValue(runningJob());
  guards.jobs.mockResolvedValue(runningJob({status: "done"}));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(analysis);
  const chosen = vi.fn(); const user = userEvent.setup();
  render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  await waitFor(() => expect(screen.getByRole("button", {name: "分析完整骨架"})).toBeEnabled());
  expectNoSubmissions(guards);
  await user.click(screen.getByRole("button", {name: "分析完整骨架"}));
  expect(guards.analyze).toHaveBeenCalledExactlyOnceWith("book", {...preview.selector, expected_content_sha256: preview.source_content_sha256});
  await waitFor(() => expect(chosen).toHaveBeenLastCalledWith("analysis-a", "require_current_head"));
});

test("rereading restores the newest matching saved analysis instead of clearing progress", async () => {
  const guards = readOnlyGuards();
  const next = {...analysis, analysis_id: "analysis-new", snapshot_id: "snapshot-new"};
  const source = vi.spyOn(api, "coverSource").mockResolvedValueOnce(savedSource(analysis)).mockResolvedValue(savedSource(next, analysis));
  vi.spyOn(api, "coverAnalysis").mockImplementation(async (_owner, id) => id === "analysis-new" ? next : analysis);
  const chosen = vi.fn(); const user = userEvent.setup();
  render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  await waitFor(() => expect(chosen).toHaveBeenLastCalledWith("analysis-a", "require_current_head"));
  await user.click(screen.getByRole("button", {name: "重新读取骨架"}));
  await waitFor(() => expect(chosen).toHaveBeenLastCalledWith("analysis-new", "require_current_head"));
  expect(source).toHaveBeenLastCalledWith("book");
  expect(screen.getByText("已完成全段分析与全局核查")).toBeInTheDocument();
  expectNoSubmissions(guards);
});

test("a running analysis survives unmount and remount, keeps polling and completes automatically", async () => {
  vi.useFakeTimers();
  const guards = readOnlyGuards();
  let current = partial(1);
  vi.spyOn(api, "coverSource").mockImplementation(async () => savedSource(current));
  const read = vi.spyOn(api, "coverAnalysis").mockImplementation(async () => current);
  guards.jobs.mockImplementation(async () => runningJob({status: current.coverage_status === "complete" ? "done" : "running"}));
  const chosen = vi.fn();
  const first = render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  await settle();
  expect(screen.getByText(/已提取 1\/4 段/)).toBeInTheDocument();
  expect(screen.getByRole("button", {name: "暂停分析"})).toBeEnabled();
  first.unmount();
  current = partial(2);
  render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  await settle();
  expect(screen.getByText(/已提取 2\/4 段/)).toBeInTheDocument();
  const readsAfterMount = read.mock.calls.length;
  current = partial(3);
  await poll();
  expect(screen.getByText(/已提取 3\/4 段/)).toBeInTheDocument();
  expect(read.mock.calls.length).toBeGreaterThan(readsAfterMount);
  expect(guards.jobs).toHaveBeenCalledWith("cover-job");
  expect(chosen).not.toHaveBeenCalledWith("analysis-a", expect.anything());
  current = {...analysis, job_id: "cover-job", job_status: "done"};
  await poll();
  expect(chosen).toHaveBeenLastCalledWith("analysis-a", "require_current_head");
  expect(screen.getByText("已完成全段分析与全局核查")).toBeInTheDocument();
  expectNoSubmissions(guards);
});

test("a saved failed analysis restores its error, segment progress and same-ID resume", async () => {
  const guards = readOnlyGuards();
  const failed = partial(2, {execution_status: "failed", phase: "failed", job_status: "error", job_error: "核查模型暂不可用", last_error: {message: "核查模型暂不可用"}});
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(failed));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(failed);
  guards.jobs.mockResolvedValue(runningJob({status: "error", error: "核查模型暂不可用"}));
  guards.resume.mockRejectedValue(new Error("模型暂不可用"));
  const chosen = vi.fn(); const user = userEvent.setup();
  render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  const resume = await screen.findByRole("button", {name: "继续未完成分析"});
  expect(screen.getByText(/已提取 2\/4 段/)).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("核查模型暂不可用");
  expect(chosen).not.toHaveBeenCalledWith("analysis-a", expect.anything());
  expectNoSubmissions(guards);
  await user.click(resume);
  expect(guards.resume).toHaveBeenCalledExactlyOnceWith("book", "analysis-a");
  expect(await screen.findByRole("alert")).toHaveTextContent("模型暂不可用");
});

test("a transient polling failure retains the task and automatically recovers", async () => {
  vi.useFakeTimers();
  const guards = readOnlyGuards();
  let current = partial(1);
  let jobUnavailable = false;
  vi.spyOn(api, "coverSource").mockImplementation(async () => savedSource(current));
  vi.spyOn(api, "coverAnalysis").mockImplementation(async () => current);
  guards.jobs.mockImplementation(async () => {
    if (jobUnavailable) throw new Error("网络暂时中断");
    return runningJob();
  });
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  await settle();
  expect(screen.getByText(/已提取 1\/4 段/)).toBeInTheDocument();
  jobUnavailable = true;
  await poll();
  expect(screen.getByText(/已提取 1\/4 段/)).toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "继续未完成分析"})).not.toBeInTheDocument();
  const attempts = guards.jobs.mock.calls.length;
  jobUnavailable = false; current = partial(3);
  await poll();
  expect(guards.jobs.mock.calls.length).toBeGreaterThan(attempts);
  expect(screen.getByText(/已提取 3\/4 段/)).toBeInTheDocument();
  expect(screen.queryByText("网络暂时中断")).not.toBeInTheDocument();
  expectNoSubmissions(guards);
});

test("legacy project runtime discovery filters read-only active jobs by kind and analysis ID", async () => {
  vi.useFakeTimers();
  const guards = readOnlyGuards();
  let current = partial(2, {execution_status: undefined, job_id: undefined, job_status: undefined});
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(current));
  const read = vi.spyOn(api, "coverAnalysis").mockImplementation(async () => current);
  guards.deletionPreview.mockResolvedValue({project_id: "book", title: "Book", chapter_count: 0, exists: true, counts: {}, project_files: 1, project_bytes: 25, media_files: 0, media_bytes: 0,
    running_job_ids: ["novel-job", "other-analysis", "cover-job"], can_delete: false});
  guards.jobs.mockImplementation(async id => id === "novel-job" ? runningJob({job_id: id, kind: "novel.run"})
    : id === "other-analysis" ? runningJob({job_id: id, meta: {analysis_id: "analysis-unrelated"}}) : runningJob());
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  await settle();
  expect(guards.deletionPreview).toHaveBeenCalledWith("book");
  expect(screen.getByText(/已提取 2\/4 段/)).toBeInTheDocument();
  expect(screen.getByRole("button", {name: "暂停分析"})).toBeEnabled();
  current = partial(3, {execution_status: undefined, job_id: undefined, job_status: undefined});
  await poll();
  expect(screen.getByText(/已提取 3\/4 段/)).toBeInTheDocument();
  expect(read).not.toHaveBeenCalledWith("book", "analysis-unrelated");
  expectNoSubmissions(guards);
});

test("legacy project without a live worker restores saved progress as interrupted", async () => {
  const guards = readOnlyGuards();
  const interrupted = partial(2, {execution_status: undefined, job_id: undefined, job_status: undefined});
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(interrupted));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(interrupted);
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  expect(await screen.findByRole("button", {name: "继续未完成分析"})).toBeEnabled();
  expect(screen.getByText(/已提取 2\/4 段/)).toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "暂停分析"})).not.toBeInTheDocument();
  expectNoSubmissions(guards);
});

test("legacy workshop without runtime metadata polls saved progress without submitting or guessing its discussion job", async () => {
  vi.useFakeTimers();
  const guards = readOnlyGuards();
  const selector = {source_type: "workshop_current" as const, session_id: "session-one"};
  let current = partial(1, {execution_status: undefined, job_id: undefined, job_status: undefined, source_selector: selector});
  vi.spyOn(api, "coverSource").mockImplementation(async () => ({...savedSource(current), selector}));
  const read = vi.spyOn(api, "coverAnalysis").mockImplementation(async () => current);
  const chosen = vi.fn();
  render(<CoverSkeletonPanel owner="workshop:session-one" onAnalysis={chosen} />);
  await settle();
  expect(screen.getByText(/已提取 1\/4 段/)).toBeInTheDocument();
  const newAnalysis = screen.getByRole("button", {name: /^(分析完整骨架|分析处理中)$/});
  expect(newAnalysis).toBeDisabled();
  await act(async () => { newAnalysis.click(); });
  expectNoSubmissions(guards);
  const initialReads = read.mock.calls.length;
  current = {...analysis, execution_status: undefined, source_selector: selector};
  await poll();
  expect(read.mock.calls.length).toBeGreaterThan(initialReads);
  expect(chosen).toHaveBeenLastCalledWith("analysis-a", "require_current_head");
  expect(guards.deletionPreview).not.toHaveBeenCalled();
  expect(guards.jobs).not.toHaveBeenCalled();
  expectNoSubmissions(guards);
});

test("automatic restoration does not select a newer analysis belonging to a different source", async () => {
  const guards = readOnlyGuards();
  const unrelated = {...analysis, analysis_id: "analysis-other", source_bundle_sha256: "c".repeat(64)};
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(unrelated, analysis));
  vi.spyOn(api, "coverAnalysis").mockImplementation(async (_owner, id) => id === "analysis-other" ? unrelated : analysis);
  const chosen = vi.fn();
  render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  await waitFor(() => expect(chosen).toHaveBeenLastCalledWith("analysis-a", "require_current_head"));
  expect(chosen).not.toHaveBeenCalledWith("analysis-other", expect.anything());
  expectNoSubmissions(guards);
});

test("an explicitly chosen historical analysis remains selectable after automatic latest recovery", async () => {
  const guards = readOnlyGuards();
  const historical = {...analysis, analysis_id: "analysis-old", snapshot_id: "snapshot-old",
    story: {core_conflict: "A previously saved understanding"}};
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(analysis, historical));
  vi.spyOn(api, "coverAnalysis").mockImplementation(async (_owner, id) => id === "analysis-old" ? historical : analysis);
  const chosen = vi.fn(); const user = userEvent.setup();
  render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  await waitFor(() => expect(chosen).toHaveBeenLastCalledWith("analysis-a", "require_current_head"));
  await user.selectOptions(screen.getByLabelText("已保存的骨架分析"), "analysis-old");
  await waitFor(() => expect(chosen).toHaveBeenLastCalledWith("analysis-old", "require_current_head"));
  expect(screen.getByText("A previously saved understanding")).toBeInTheDocument();
  expectNoSubmissions(guards);
});

test("a late source response from the previous owner cannot replace the current owner's analysis", async () => {
  const guards = readOnlyGuards();
  let releaseOld!: (value: CoverSourcePreview) => void;
  const other = {...analysis, analysis_id: "analysis-second", source_selector: {source_type: "project_input" as const, project_id: "second-book"}};
  vi.spyOn(api, "coverSource").mockImplementation(owner => owner === "book"
    ? new Promise(resolve => { releaseOld = resolve; })
    : Promise.resolve({...savedSource(other), selector: other.source_selector}));
  vi.spyOn(api, "coverAnalysis").mockImplementation(async owner => owner === "book" ? analysis : other);
  const chosen = vi.fn();
  const view = render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  view.rerender(<CoverSkeletonPanel owner="second-book" onAnalysis={chosen} />);
  await waitFor(() => expect(chosen).toHaveBeenLastCalledWith("analysis-second", "require_current_head"));
  await act(async () => releaseOld(savedSource(analysis)));
  expect(chosen).toHaveBeenLastCalledWith("analysis-second", "require_current_head");
  expect(chosen).not.toHaveBeenCalledWith("analysis-a", expect.anything());
  expectNoSubmissions(guards);
});

test.each(["coverSource", "coverAnalysis"] as const)("a transient initial %s read automatically recovers and cannot enable duplicate analysis", async failingRead => {
  vi.useFakeTimers();
  const guards = readOnlyGuards();
  let online = false;
  const source = vi.spyOn(api, "coverSource").mockImplementation(async () => {
    if (failingRead === "coverSource" && !online) throw new Error("骨架接口暂时断开");
    return savedSource(analysis);
  });
  const read = vi.spyOn(api, "coverAnalysis").mockImplementation(async () => {
    if (failingRead === "coverAnalysis" && !online) throw new Error("分析接口暂时断开");
    return analysis;
  });
  const chosen = vi.fn();
  render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  await settle();
  const button = screen.getByRole("button", {name: /^(分析完整骨架|分析处理中)$/});
  expect(button).toBeDisabled();
  await act(async () => { button.click(); });
  expectNoSubmissions(guards);
  const beforeRecovery = failingRead === "coverSource" ? source.mock.calls.length : read.mock.calls.length;
  online = true;
  await poll();
  expect(failingRead === "coverSource" ? source.mock.calls.length : read.mock.calls.length).toBeGreaterThan(beforeRecovery);
  expect(chosen).toHaveBeenLastCalledWith("analysis-a", "require_current_head");
  expect(screen.getByText("已完成全段分析与全局核查")).toBeInTheDocument();
  expect(screen.getByRole("button", {name: "分析完整骨架"})).toBeEnabled();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expectNoSubmissions(guards);
});

test.each(["running", "interrupted"] as const)("legacy project retries failed job discovery and recovers %s state without submissions", async outcome => {
  vi.useFakeTimers();
  const guards = readOnlyGuards();
  let discoveryOnline = false;
  const current = partial(2, {execution_status: undefined, job_id: undefined, job_status: undefined});
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(current));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(current);
  guards.deletionPreview.mockImplementation(async () => {
    if (!discoveryOnline) throw new Error("任务查询暂时不可用");
    return {project_id: "book", title: "Book", chapter_count: 0, exists: true, counts: {},
      project_files: 1, project_bytes: 25, media_files: 0, media_bytes: 0,
      running_job_ids: outcome === "running" ? ["cover-job"] : [], can_delete: outcome !== "running"};
  });
  const chosen = vi.fn();
  render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  await settle();
  expect(screen.getByText(/已提取 2\/4 段/)).toBeInTheDocument();
  expect(screen.getByRole("button", {name: /^(分析完整骨架|分析处理中)$/})).toBeDisabled();
  expect(screen.queryByRole("button", {name: "继续未完成分析"})).not.toBeInTheDocument();
  const initialDiscoveryCalls = guards.deletionPreview.mock.calls.length;
  discoveryOnline = true;
  await poll();
  expect(guards.deletionPreview.mock.calls.length).toBeGreaterThan(initialDiscoveryCalls);
  if (outcome === "running") {
    expect(screen.getByText(/后台分析中/)).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "暂停分析"})).toBeEnabled();
    expect(guards.jobs).toHaveBeenCalledWith("cover-job");
  } else {
    expect(screen.getByText(/任务已中断/)).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "继续未完成分析"})).toBeEnabled();
    expect(screen.queryByRole("button", {name: "暂停分析"})).not.toBeInTheDocument();
  }
  expect(chosen).not.toHaveBeenCalledWith("analysis-a", expect.anything());
  expectNoSubmissions(guards);
});


test("source details are compact and disclose the frozen input without model submissions", async () => {
  const guards = readOnlyGuards();
  vi.spyOn(api, "coverSource").mockResolvedValue(preview);
  const user = userEvent.setup();
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  const summary = await screen.findByText(/预览原文与来源身份/);
  expect(screen.getByText(/不自动扫描章节正文/)).toBeVisible();
  expect(summary.closest("details")).not.toHaveAttribute("open");
  expect(screen.getByText("outputs/input/prompt.md")).not.toBeVisible();
  // Native summary owns keyboard activation in browsers; jsdom models its click default.
  expect(summary.tagName).toBe("SUMMARY");
  await user.click(summary);
  expect(summary.closest("details")).toHaveAttribute("open");
  expect(screen.getByText("outputs/input/prompt.md")).toBeVisible();
  expect(screen.getByText("Mara withdraws the key.")).toBeVisible();
  expectNoSubmissions(guards);
});

test.each([
  {last_error: {code: "analysis_semantic_repair_exhausted", message: "The single source-bound correction failed"}},
  {semantic_revision: {status: "exhausted"}, last_error: {message: "Prior verification failed"}},
])("legacy exhausted analyses do not offer a futile resume: %o", async failure => {
  const guards = readOnlyGuards();
  const current = partial(4, {phase: "failed", execution_status: "failed", job_id: null, job_status: null, ...failure});
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(current));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(current);
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  expect(await screen.findByRole("status")).toHaveTextContent("本次自动修正已用尽");
  expect(screen.queryByRole("button", {name: "继续未完成分析"})).not.toBeInTheDocument();
  expect(screen.getByText("查看此前核查问题").closest("details")).not.toHaveAttribute("open");
  expectNoSubmissions(guards);
});

test("the analysis hook also blocks direct same-ID resume for an exhausted parent", async () => {
  const guards = readOnlyGuards();
  const current = partial(4, {phase: "failed", execution_status: "failed",
    recovery: {kind: "source_correction_required", can_resume: false, message: "需要先更正分析"}});
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(current));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(current);
  const chosen = vi.fn();
  const {result} = renderHook(() => useCoverAnalysis("book", chosen));
  await waitFor(() => expect(result.current.busy).toBe(false));
  await act(async () => result.current.resume());
  expect(result.current.execution).toBe("failed");
  expectNoSubmissions(guards);
});

test.each([null, undefined])("an explicit backend recovery %s controls legacy exhausted-state fallback", async recovery => {
  const guards = readOnlyGuards();
  const current = partial(4, {phase: "failed", execution_status: "failed", job_id: null, job_status: null,
    last_error: {code: "analysis_semantic_repair_exhausted", message: "Saved old-policy review failed"},
    semantic_revision: {status: "exhausted"}, recovery});
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(current));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(current);
  const user = userEvent.setup();
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  await screen.findByRole("status");
  expectNoSubmissions(guards);
  if (recovery === null) {
    const resume = screen.getByRole("button", {name: "继续未完成分析"});
    guards.resume.mockRejectedValue(new Error("Explicit upgrade request reached the API"));
    await user.click(resume);
    expect(guards.resume).toHaveBeenCalledExactlyOnceWith("book", current.analysis_id);
  } else {
    expect(screen.queryByRole("button", {name: "继续未完成分析"})).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("本次自动修正已用尽");
    expectNoSubmissions(guards);
  }
});

test("viewing an available amendment only reads it; verification requires a separate explicit click", async () => {
  const guards = readOnlyGuards();
  const parent = partial(4, {phase: "failed", execution_status: "failed", job_id: null, job_status: null,
    last_error: {code: "analysis_semantic_repair_exhausted", message: "Earlier source qualification failure"},
    recovery: {kind: "amendment_available", can_resume: false, analysis_id: "analysis-child", message: "已有局部修订，可查看修订结果。"}});
  const child = partial(4, {analysis_id: "analysis-child", phase: "verify", execution_status: "interrupted", job_id: null, job_status: null,
    recovery: {kind: "amendment_pending", can_resume: true, parent_analysis_id: parent.analysis_id,
      message: "局部修订已保存；核查尚未启动，继续核查会调用模型。"}});
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(parent, child));
  const read = vi.spyOn(api, "coverAnalysis").mockImplementation(async (_owner, id) => id === child.analysis_id ? child : parent);
  const chosen = vi.fn(); const user = userEvent.setup();
  render(<CoverSkeletonPanel owner="book" onAnalysis={chosen} />);
  await user.click(await screen.findByRole("button", {name: "查看修订结果"}));
  expect(await screen.findByText("局部修订已保存，待核查")).toBeInTheDocument();
  expect(read).toHaveBeenLastCalledWith("book", child.analysis_id);
  expect(screen.queryByRole("button", {name: "继续未完成分析"})).not.toBeInTheDocument();
  expect(chosen).not.toHaveBeenCalledWith(child.analysis_id, expect.anything());
  expectNoSubmissions(guards);
  guards.resume.mockRejectedValue(new Error("本次请求尚未发送，服务暂不可用"));
  await user.click(screen.getByRole("button", {name: "继续核查修订结果"}));
  expect(guards.resume).toHaveBeenCalledExactlyOnceWith("book", child.analysis_id);
  expect(guards.analyze).not.toHaveBeenCalled();
  expect(await screen.findByRole("alert")).toHaveTextContent("服务暂不可用");
});

test("a saved amendment remains pending across remounts without starting verification", async () => {
  const guards = readOnlyGuards();
  const child = partial(4, {analysis_id: "analysis-child", phase: "verify", execution_status: "interrupted", job_id: null, job_status: null,
    recovery: {kind: "amendment_pending", can_resume: true, message: "局部修订已保存；等待手动核查。"}});
  vi.spyOn(api, "coverSource").mockResolvedValue(savedSource(child));
  vi.spyOn(api, "coverAnalysis").mockResolvedValue(child);
  const first = render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  expect(await screen.findByRole("button", {name: "继续核查修订结果"})).toBeEnabled();
  first.unmount();
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  expect(await screen.findByRole("button", {name: "继续核查修订结果"})).toBeEnabled();
  expect(screen.getByRole("status")).toHaveTextContent("等待手动核查");
  expectNoSubmissions(guards);
});

test("semantic amendment failure stops retries while transport recovery remains available", async () => {
  const guards = readOnlyGuards();
  let current = partial(4, {phase: "failed", execution_status: "failed", job_id: null, job_status: null,
    last_error: {code: "analysis_amendment_verification_failed", message: "A material source claim still conflicts"},
    recovery: {kind: "amendment_failed", can_resume: false, message: "修订结果未通过，请处理核查问题。"}});
  vi.spyOn(api, "coverSource").mockImplementation(async () => savedSource(current));
  vi.spyOn(api, "coverAnalysis").mockImplementation(async () => current);
  const user = userEvent.setup();
  render(<CoverSkeletonPanel owner="book" onAnalysis={vi.fn()} />);
  expect(await screen.findByRole("status")).toHaveTextContent("修订结果未通过");
  expect(screen.queryByRole("button", {name: /继续.*分析|继续核查/})).not.toBeInTheDocument();
  current = {...current, last_error: {code: "analysis_failed", message: "Provider connection interrupted"},
    recovery: {kind: "amendment_pending", can_resume: true, message: "核查连接中断，已保存的结果保留，可继续核查。"}};
  await user.click(screen.getByRole("button", {name: "重新读取骨架"}));
  expect(await screen.findByRole("button", {name: "继续核查修订结果"})).toBeEnabled();
  expectNoSubmissions(guards);
});
