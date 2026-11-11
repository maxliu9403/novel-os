import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import SourceBooks from "./SourceBooks";
import { sourceBooksApi, type SourceDesignState, type SourceDetail, type SourceSelection } from "../api/sourceBooks";
import { sourceRoutesApi } from "../api/sourceRoutes";
import { sourceBook } from "../test/sourceRoutesFixture";

// The route editor is an independent feature. Keep a real editable DOM node
// here so list refreshes and filters must preserve the selected editor instance.
vi.mock("../components/SourceRoutes", () => ({
  default: ({ sourceId, mode, onStatusChange }: { sourceId: string; mode: SourceSelection["mode"]; onStatusChange?: () => void }) => (
    <section aria-label="当前路线编辑器">
      <output aria-label="正在查看的路线">{sourceId}:{mode}</output>
      <textarea aria-label="未提交的路线补充" />
      <button type="button" onClick={onStatusChange}>模拟路线状态变化</button>
    </section>
  ),
}));

function reference(id: string, status?: SourceDesignState["status"], mode: SourceSelection["mode"] = "original_rewrite"): SourceDetail {
  return {
    ...sourceBook(), id, title: `资料-${id}`,
    study: { status: "complete", completed: 1, total: 1 },
    ...(status ? { design: {
      status, active_count: status === "running" ? 1 : 0, total_runs: 1,
      latest_run_id: `run-${id}`, mode, completed: 1, total: 3,
    } } : {}),
  };
}
let books: SourceDetail[];
beforeEach(() => {
  localStorage.clear();
  books = [reference("running", "running"), reference("paused", "paused", "reference_creation"),
    reference("ready", "ready"), reference("failed", "needs_attention"), reference("unknown", "unknown"), reference("legacy")];
  vi.spyOn(sourceBooksApi, "list").mockImplementation(async () => ({ items: books }));
  vi.spyOn(sourceBooksApi, "get").mockImplementation(async (id) => books.find(book => book.id === id)!);
  vi.spyOn(sourceBooksApi, "usage").mockResolvedValue({
    counts: { discussions: 0, projects: 0, in_progress: 0, completed: 0 }, sessions: [], projects: [],
  });
  vi.spyOn(sourceBooksApi, "analyze").mockRejectedValue(new Error("viewing must not start reading"));
  vi.spyOn(sourceBooksApi, "cancel").mockRejectedValue(new Error("viewing must not pause reading"));
  for (const method of ["start", "pause", "resume", "handoff"] as const) {
    vi.spyOn(sourceRoutesApi, method).mockRejectedValue(new Error("viewing must not mutate a design"));
  }
});
afterEach(() => vi.restoreAllMocks());

function Location() {
  const location = useLocation();
  return <output aria-label="当前地址">{location.search}</output>;
}
function open(search = "?source=running") {
  return render(<MemoryRouter initialEntries={[`/source-books${search}`]}><SourceBooks /><Location /></MemoryRouter>);
}
function expectNoTaskChanges() {
  expect(sourceBooksApi.analyze).not.toHaveBeenCalled();
  expect(sourceBooksApi.cancel).not.toHaveBeenCalled();
  expect(sourceRoutesApi.start).not.toHaveBeenCalled();
  expect(sourceRoutesApi.pause).not.toHaveBeenCalled();
  expect(sourceRoutesApi.resume).not.toHaveBeenCalled();
  expect(sourceRoutesApi.handoff).not.toHaveBeenCalled();
}

it("shows whole-story design separately from completed reading and filters each management state", async () => {
  const user = userEvent.setup();
  open();
  const nav = await screen.findByRole("navigation", { name: "参考小说列表" });
  const running = within(nav).getByRole("button", { name: /资料-running/ });
  expect(within(running).getByLabelText("整书设计：设计中")).toBeVisible();
  expect(within(running).getByText(/分析完成/)).toBeVisible();
  expect(within(running).getByText(/已保存 1\/3 步/)).toBeVisible();
  expect(screen.getByText(/1 项路线设计进行中/)).toBeVisible();
  const filters = within(screen.getByRole("group", { name: "设计状态筛选" }));
  expect(filters.getByRole("button", { name: "全部 6" })).toHaveAttribute("aria-pressed", "true");
  await user.click(filters.getByRole("button", { name: "设计中 1" }));
  expect(within(nav).getByRole("button", { name: /资料-running/ })).toBeVisible();
  expect(within(nav).queryByRole("button", { name: /资料-paused/ })).not.toBeInTheDocument();
  await user.click(filters.getByRole("button", { name: "已暂停 1" }));
  const paused = within(nav).getByRole("button", { name: /资料-paused/ });
  expect(within(paused).getByLabelText("整书设计：设计已暂停")).toBeVisible();
  await user.click(paused);
  expect(await screen.findByLabelText("正在查看的路线")).toHaveTextContent("paused:reference_creation");
  expect(screen.getByRole("radio", { name: "借鉴创作" })).toHaveAttribute("aria-checked", "true");
  await user.click(filters.getByRole("button", { name: "待处理 2" }));
  expect(within(nav).getByRole("button", { name: /资料-failed/ })).toBeVisible();
  expect(within(nav).getByRole("button", { name: /资料-unknown/ })).toBeVisible();
  await user.click(filters.getByRole("button", { name: "路线就绪 1" }));
  expect(within(nav).getByRole("button", { name: /资料-ready/ })).toBeVisible();
  await user.click(filters.getByRole("button", { name: "全部 6" }));
  expect(within(nav).getByRole("button", { name: /资料-legacy/ })).toBeVisible();
  expectNoTaskChanges();
});

it("refreshes running designs to ready without discarding the open route mode or unsent feedback", async () => {
  const user = userEvent.setup();
  books = [reference("running", "running")];
  open();
  await screen.findByRole("group", { name: "设计状态筛选" });
  await waitFor(() => expect(screen.getByLabelText("当前地址")).toHaveTextContent("mode=original_rewrite"));
  await user.type(screen.getByLabelText("未提交的路线补充"), "保留她自己的选择，不改结局。");
  await user.click(screen.getByRole("button", { name: "设计中 1" }));
  // A different mode becomes the latest saved run while this editor is open.
  books = [reference("running", "ready", "source_development")];
  await user.click(screen.getByRole("button", { name: "刷新列表" }));
  await screen.findByText(/没有符合此状态的资料/);
  expect(screen.getByLabelText("正在查看的路线")).toHaveTextContent("running:original_rewrite");
  expect(screen.getByLabelText("未提交的路线补充")).toHaveValue("保留她自己的选择，不改结局。");
  expect(screen.getByRole("radio", { name: "改编重写" })).toHaveAttribute("aria-checked", "true");
  expect(screen.queryByText(/项路线设计进行中/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "全部 1" }));
  const item = within(screen.getByRole("navigation", { name: "参考小说列表" })).getByRole("button", { name: /资料-running/ });
  expect(within(item).getByLabelText("整书设计：路线就绪")).toBeVisible();
  await user.click(item);
  expect(screen.getByLabelText("正在查看的路线")).toHaveTextContent("running:original_rewrite");
  expect(screen.getByLabelText("未提交的路线补充")).toHaveValue("保留她自己的选择，不改结局。");
  expectNoTaskChanges();
});

it("retains the last design state on a failed refresh and recovers without restarting a task", async () => {
  const user = userEvent.setup();
  books = [reference("running", "running")];
  open();
  await screen.findByRole("group", { name: "设计状态筛选" });
  vi.mocked(sourceBooksApi.list).mockRejectedValueOnce(new Error("offline"));
  await user.click(screen.getByRole("button", { name: "刷新列表" }));
  await screen.findByText(/连接暂时中断，保留上次状态/);
  const nav = within(screen.getByRole("navigation", { name: "参考小说列表" }));
  expect(within(nav.getByRole("button", { name: /资料-running/ })).getByLabelText("整书设计：设计中")).toBeVisible();
  books = [reference("running", "needs_attention")];
  await user.click(screen.getByRole("button", { name: "刷新列表" }));
  await waitFor(() => expect(screen.queryByText(/连接暂时中断，保留上次状态/)).not.toBeInTheDocument());
  expect(within(nav.getByRole("button", { name: /资料-running/ })).getByLabelText("整书设计：设计待处理")).toBeVisible();
  expectNoTaskChanges();
});

it("respects an explicitly linked mode instead of the latest design mode", async () => {
  open("?source=running&mode=reference_creation");
  expect(await screen.findByLabelText("正在查看的路线")).toHaveTextContent("running:reference_creation");
  expect(screen.getByRole("radio", { name: "借鉴创作" })).toHaveAttribute("aria-checked", "true");
  expectNoTaskChanges();
});

it("refreshes the library immediately when its route editor reports a state change", async () => {
  const user = userEvent.setup();
  books = [reference("running", "running")];
  open();
  await screen.findByLabelText("正在查看的路线");
  const readsBefore = vi.mocked(sourceBooksApi.list).mock.calls.length;
  books = [reference("running", "paused")];
  await user.click(screen.getByRole("button", { name: "模拟路线状态变化" }));
  const nav = within(screen.getByRole("navigation", { name: "参考小说列表" }));
  await waitFor(() => expect(within(nav.getByRole("button", { name: /资料-running/ })).getByLabelText("整书设计：设计已暂停")).toBeVisible());
  expect(sourceBooksApi.list).toHaveBeenCalledTimes(readsBefore + 1);
  expectNoTaskChanges();
});
