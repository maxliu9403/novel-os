import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import ReaderReview, { SourcePassage } from "../routes/ReaderReview";
import ReaderReviewPanel from "../components/ReaderReviewPanel";
import { DEFAULT_REVIEW_OPTIONS, DIMENSIONS, readerReviewsApi, type ReaderReview as Review, type ReviewEvidence } from "../api/readerReviews";

const evidence: ReviewEvidence = { id: "e1", chapter: 1, unit_id: "u0001", start: 3, end: 6, quote: "她走了", body_sha256: "body" };
const source = { number: 1, version: "final", body: "🐺说：她走了。旧版全文。", body_sha256: "body", snapshot_sha256: "source", offset_unit: "unicode_codepoints" };
function review(patch: Partial<Review> = {}): Review {
  return { id: "r1", status: "completed", created_at: 1700000000, updated_at: 1700000000, deadline: Date.now() / 1000 + 1000,
    worker_active: false, pause_requested: false, completed_units: 1, unit_count: 1, call_count: 2,
    model: { provider: "test", model: "fixture" }, options: DEFAULT_REVIEW_OPTIONS, source_sha256: "source", source_current: false,
    complete_book: true, missing_chapters: [], available_chapters: 1, expected_chapters: 1, report_ready: true, error: null, unrecorded_calls: 2, last_call_started_at: null,
    usage: { input_tokens: 0, output_tokens: 0, cached_input_tokens: 0, reasoning_tokens: 0, total_tokens: 0, images: 0, request_count: 0,
      known_usage_requests: 0, unknown_usage_requests: 0, estimated_cost_cny: null, estimated_cost_usd: null, cost_complete: false },
    report: { strengths: [{ dimension: "character", scope: "local", observation: "选择产生了后果", reader_impact: "想知道她如何承担", suggested_action: "", preserve: "保留行动", uncertainty: "", evidence_ids: ["e1"] }], issues: [],
      dimensions: Object.keys(DIMENSIONS).map(dimension => ({ dimension, status: "insufficient", explanation: "需要更多证据", evidence_ids: [] })),
      evidence: { e1: evidence }, uncertainties: ["并非真实读者测量"], limitations: ["引用真实不保证判断正确"], lossy_summary_levels: 0 }, ...patch };
}
function mount() { return render(<MemoryRouter initialEntries={["/projects/book/reader-reviews/r1"]}><Routes><Route path="/projects/:id/reader-reviews/:reviewId" element={<ReaderReview />} /></Routes></MemoryRouter>); }
beforeEach(() => {
  vi.spyOn(readerReviewsApi, "get").mockResolvedValue(review());
  vi.spyOn(readerReviewsApi, "source").mockResolvedValue(source);
  vi.spyOn(readerReviewsApi, "list").mockResolvedValue({ reviews: [] });
  vi.spyOn(readerReviewsApi, "create").mockResolvedValue(review());
  vi.spyOn(readerReviewsApi, "resume").mockResolvedValue(review({ status: "reading", worker_active: true }));
  vi.spyOn(readerReviewsApi, "pause").mockResolvedValue(review({ status: "reading", worker_active: true, pause_requested: true }));
  HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(() => vi.restoreAllMocks());

test("report shows strengths, historical evidence and honest unknown usage; GET never resumes work", async () => {
  mount();
  expect(await screen.findByText("选择产生了后果")).toBeVisible();
  expect(screen.getByText(/正文已发生变化/)).toBeVisible();
  expect(screen.getByText(/Token 用量待记录/)).toBeVisible();
  expect(readerReviewsApi.resume).not.toHaveBeenCalled();
  expect(readerReviewsApi.create).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "第 1 章 · 查看原文 ↗" }));
  const panel = await screen.findByRole("complementary", { name: "评议时的原文快照" });
  await waitFor(() => expect(within(panel).getByLabelText("引用原文")).toHaveTextContent("她走了"));
  expect(within(panel).getByText(/旧版全文/)).toBeVisible();
  expect(readerReviewsApi.source).toHaveBeenCalledWith("book", "r1", 1);
});

test("Unicode codepoint offsets do not split emoji and wrong hashes never highlight", () => {
  const ui = render(<SourcePassage source={source} evidence={evidence} />);
  expect(screen.getByLabelText("引用原文")).toHaveTextContent("她走了");
  ui.rerender(<SourcePassage source={source} evidence={{ ...evidence, body_sha256: "wrong" }} />);
  expect(screen.getByRole("alert")).toHaveTextContent("不匹配");
  expect(screen.queryByLabelText("引用原文")).not.toBeInTheDocument();
});

test("pause keeps active worker visible and never offers parallel resume", async () => {
  vi.mocked(readerReviewsApi.get).mockResolvedValue(review({ status: "reading", worker_active: true, report: null, report_ready: false }));
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "暂停评议" }));
  await waitFor(() => expect(readerReviewsApi.pause).toHaveBeenCalledWith("book", "r1"));
  expect(screen.queryByRole("button", { name: "恢复未完成部分" })).not.toBeInTheDocument();
});

test("explicit resume is required for interrupted work", async () => {
  vi.mocked(readerReviewsApi.get).mockResolvedValue(review({ status: "interrupted", report: null, report_ready: false }));
  mount();
  expect(await screen.findByRole("button", { name: "恢复未完成部分" })).toBeEnabled();
  expect(readerReviewsApi.resume).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "恢复未完成部分" }));
  await waitFor(() => expect(readerReviewsApi.resume).toHaveBeenCalledTimes(1));
});

test("creation requires preview and reuses idempotency key after ambiguous HTTP failure", async () => {
  vi.spyOn(readerReviewsApi, "preview").mockResolvedValue({ proposal_sha256: "proposal", model: { provider: "test", model: "fixture" },
    options: DEFAULT_REVIEW_OPTIONS, available_chapters: 1, expected_chapters: 1, missing_chapters: [], complete_book: true,
    units: 1, minimum_calls: 2, source_reference_tokens: 42, maximum_reserved_cny: null, notes: ["参考预算"] });
  vi.mocked(readerReviewsApi.create).mockRejectedValueOnce(new Error("响应中断"));
  render(<MemoryRouter><ReaderReviewPanel projectId="book" /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "新建阅读评议" }));
  expect(readerReviewsApi.create).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "预览范围与预算" }));
  fireEvent.click(await screen.findByRole("button", { name: "开始评议" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("响应中断");
  fireEvent.click(screen.getByRole("button", { name: "开始评议" }));
  await waitFor(() => expect(readerReviewsApi.create).toHaveBeenCalledTimes(2));
  expect(vi.mocked(readerReviewsApi.create).mock.calls[0][2]).toBe(vi.mocked(readerReviewsApi.create).mock.calls[1][2]);
});

test("editing scope invalidates the preview", async () => {
  vi.spyOn(readerReviewsApi, "preview").mockResolvedValue({ proposal_sha256: "proposal", model: { provider: "test", model: "fixture" },
    options: DEFAULT_REVIEW_OPTIONS, available_chapters: 1, expected_chapters: 1, missing_chapters: [], complete_book: true,
    units: 1, minimum_calls: 2, source_reference_tokens: 42, maximum_reserved_cny: null, notes: [] });
  render(<MemoryRouter><ReaderReviewPanel projectId="book" /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "新建阅读评议" }));
  fireEvent.click(screen.getByRole("button", { name: "预览范围与预算" }));
  await screen.findByRole("button", { name: "开始评议" });
  fireEvent.change(screen.getByLabelText("起始章"), { target: { value: "2" } });
  expect(screen.queryByRole("button", { name: "开始评议" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "预览范围与预算" })).toBeVisible();
});

test("chapter list links only chapters with completed saved review coverage", async () => {
  const { ProjectChaptersTab } = await import("../components/ProjectStoryWorkspace");
  vi.mocked(readerReviewsApi.list).mockResolvedValue({ reviews: [review({ reviewed_chapters: [1] })] });
  render(<MemoryRouter><ProjectChaptersTab projectId="book" chapters={[1, 2].map(number => ({ number, title: `Chapter ${number}`, status: "complete", word_count: 100, pov: "她" }))} story={null} loading={false} error={null} onRetry={() => {}} isRunning={false} onPlanChapter={() => {}} /></MemoryRouter>);
  const link = await screen.findByRole("link", { name: "阅读评议 ↗" });
  expect(link).toHaveAttribute("href", "/projects/book/reader-reviews/r1");
  expect(screen.getAllByRole("link", { name: "阅读评议 ↗" })).toHaveLength(1);
  expect(readerReviewsApi.list).toHaveBeenCalledTimes(1);
});
