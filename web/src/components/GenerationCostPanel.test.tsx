import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type CostTotals, type GenerationCosts } from "../api/client";
import GenerationCostPanel from "./GenerationCostPanel";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Unexpected network request")));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function totals(patch: Partial<CostTotals> = {}): CostTotals {
  return {input_tokens: 1200, output_tokens: 300, cached_input_tokens: 400, reasoning_tokens: 100,
    total_tokens: 1500, images: 0, request_count: 1, known_usage_requests: 1, unknown_usage_requests: 0,
    estimated_cost_usd: 1, estimated_cost_cny: 7.2, cost_complete: true, ...patch};
}
function costs(patch: Partial<GenerationCosts> = {}): GenerationCosts {
  return {schema_version: 1, project_id: "book-a", currency: "CNY", estimate: true,
    pricing: {usd_to_cny: 7.2, exchange_rate_status: "configured"},
    totals: totals({input_tokens: 3600, output_tokens: 900, cached_input_tokens: 1200, reasoning_tokens: 300,
      total_tokens: 4500, images: 1, request_count: 3, known_usage_requests: 3, estimated_cost_usd: 6, estimated_cost_cny: 43.2}),
    stages: [
      {...totals(), stage: "brainstorming"},
      {...totals({estimated_cost_usd: 2, estimated_cost_cny: 14.4}), stage: "manuscript"},
      {...totals({images: 1, estimated_cost_usd: 3, estimated_cost_cny: 21.6}), stage: "cover"},
    ],
    models: [{...totals(), stage: "brainstorming", provider: "codex", model: "text-fixture"}],
    notes: ["历史记录可能不完整。"], ...patch};
}
function mount(owner = "book-a", refreshKey = 0) {
  return render(<MemoryRouter><GenerationCostPanel owner={owner} refreshKey={refreshKey} /></MemoryRouter>);
}
function stage(label: string) {
  const element = screen.getByText(label, {selector: ".generation-costs__stage-label"}).closest("div");
  if (!element) throw new Error(`Missing stage ${label}`);
  return within(element);
}
function metric(label: string) {
  const element = screen.getByText(label, {selector: "dt"}).parentElement;
  if (!element) throw new Error(`Missing metric ${label}`);
  return within(element).getByRole("definition");
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return {promise, resolve};
}

test("shows RMB by production stage and actual tokens without adding cached or reasoning tokens twice", async () => {
  const load = vi.spyOn(api, "generationCosts").mockResolvedValue(costs());
  const user = userEvent.setup();
  mount();
  await screen.findByText("¥43.20");
  expect(load).toHaveBeenCalledExactlyOnceWith("book-a");
  expect(stage("头脑风暴").getByText("¥7.20")).toBeVisible();
  expect(stage("正文创作").getByText("¥14.40")).toBeVisible();
  expect(stage("封面设计").getByText("¥21.60")).toBeVisible();
  expect(screen.getByText("4,500 tokens")).toBeVisible();
  expect(screen.queryByText("6,000 tokens")).not.toBeInTheDocument();
  expect(screen.getByText(/Token 按模型调用结束后返回的用量更新/)).toBeVisible();
  await user.click(screen.getByText("用量明细与计费说明"));
  expect(metric("输入 Token")).toHaveTextContent("3,600");
  expect(metric("其中缓存命中")).toHaveTextContent("1,200");
  expect(metric("输出 Token")).toHaveTextContent("900");
  expect(metric("其中推理 Token")).toHaveTextContent("300");
  expect(screen.getByRole("table", {name: "模型与阶段明细"})).toHaveTextContent("text-fixture");
  expect(screen.getByRole("link", {name: /单价与汇率/})).toHaveAttribute("href", "/settings?tab=pricing");
  expect(screen.getByText(/1 USD = 7.2 CNY/)).toBeVisible();
  expect(screen.getByText("历史记录可能不完整。")).toBeVisible();
  expect(screen.queryByRole("table", {name: "封面制作明细"})).not.toBeInTheDocument();
});

test("cover activities distinguish recorded costs from unknown usage and frozen pricing from current settings", async () => {
  vi.spyOn(api, "generationCosts").mockResolvedValue(costs({
    pricing: {usd_to_cny: 7.2, exchange_rate_status: "configured", request_snapshots_present: true},
    totals: totals({mixed_exchange_rates: true}),
    cover_activities: [
      {...totals(), activity: "plan"},
      {...totals({total_tokens: 0, known_usage_requests: 0, unknown_usage_requests: 1, estimated_cost_cny: null, estimated_cost_usd: null, cost_complete: false}), activity: "refine"},
    ],
  }));
  const user = userEvent.setup(); mount();
  const table = await screen.findByRole("table", {name: "封面制作明细"});
  expect(within(table).getByRole("row", {name: /设计方向/})).toHaveTextContent("1,500 tokens");
  const refine = within(table).getByRole("row", {name: /原图精修/});
  expect(refine).toHaveTextContent("Token 未记录"); expect(refine).toHaveTextContent("待核算");
  expect(refine).not.toHaveTextContent("0 tokens"); expect(refine).not.toHaveTextContent("¥0.00");
  await user.click(screen.getByText("用量明细与计费说明"));
  expect(screen.getByText(/按当次保存的单价与汇率估算/)).toBeVisible();
  expect(screen.getByText(/总人民币费用为各次调用估算金额之和/)).toBeVisible();
});

test("partial totals remain labelled as a subtotal while missing stage costs are never zero", async () => {
  const unknown = totals({input_tokens: 0, output_tokens: 0, cached_input_tokens: 0, reasoning_tokens: 0,
    total_tokens: 0, known_usage_requests: 0, unknown_usage_requests: 1,
    estimated_cost_usd: null, estimated_cost_cny: null, cost_complete: false});
  vi.spyOn(api, "generationCosts").mockResolvedValue(costs({
    totals: totals({request_count: 2, unknown_usage_requests: 1, cost_complete: false}),
    stages: [{...totals(), stage: "brainstorming"}, {...unknown, stage: "manuscript"},
      {...unknown, request_count: 0, unknown_usage_requests: 0, stage: "cover"}], models: [],
  }));
  const user = userEvent.setup(); mount();
  await screen.findByText("已计费用 · 估算小计");
  expect(stage("正文创作").getByText("待核算")).toBeVisible();
  expect(stage("正文创作").getByText("Token 未记录")).toBeVisible();
  expect(stage("封面设计").getByText("—")).toBeVisible();
  expect(stage("封面设计").getByText("尚无记录")).toBeVisible();
  expect(screen.queryByText("¥0.00")).not.toBeInTheDocument();
  await user.click(screen.getByText("用量明细与计费说明"));
  expect(screen.getByText(/1 次调用缺少完整用量/)).toBeVisible();
});

test("entirely unknown usage keeps every token detail unknown instead of asserting real zero", async () => {
  const unknown = totals({input_tokens: 0, output_tokens: 0, cached_input_tokens: 0, reasoning_tokens: 0,
    total_tokens: 0, known_usage_requests: 0, unknown_usage_requests: 1,
    estimated_cost_usd: null, estimated_cost_cny: null, cost_complete: false});
  vi.spyOn(api, "generationCosts").mockResolvedValue(costs({totals: unknown,
    pricing: {usd_to_cny: null, exchange_rate_status: "unset"},
    stages: [{...unknown, stage: "cover"}], models: []}));
  const user = userEvent.setup(); mount();
  await screen.findByText("已计费用 · 估算小计");
  expect(screen.queryByText("0 tokens")).not.toBeInTheDocument();
  expect(screen.queryByText("¥0.00")).not.toBeInTheDocument();
  await user.click(screen.getByText("用量明细与计费说明"));
  for (const label of ["输入 Token", "其中缓存命中", "输出 Token", "其中推理 Token"]) {
    expect(metric(label)).toHaveTextContent("未记录");
  }
  expect(screen.getByText(/尚未设置美元兑人民币换算值。/)).toBeVisible();
});

test("missing cache and reasoning counters stay unknown even with complete input and output", async () => {
  vi.spyOn(api, "generationCosts").mockResolvedValue(costs({totals: totals({
    cached_input_tokens: 0, reasoning_tokens: 0,
    usage_coverage: {input_tokens: 1, output_tokens: 1, cached_input_tokens: 0, reasoning_tokens: 0, total_tokens: 1},
  })}));
  const user = userEvent.setup(); mount();
  await screen.findByText("创作用量与费用", {selector: "h2"});
  await user.click(screen.getByText("用量明细与计费说明"));
  expect(metric("输入 Token")).toHaveTextContent("1,200");
  expect(metric("输出 Token")).toHaveTextContent("300");
  expect(metric("其中缓存命中")).toHaveTextContent("未记录");
  expect(metric("其中推理 Token")).toHaveTextContent("未记录");
});

test("a slower earlier refresh cannot replace newer costs for the same book", async () => {
  const earlier = deferred<GenerationCosts>();
  const later = deferred<GenerationCosts>();
  const load = vi.spyOn(api, "generationCosts").mockResolvedValueOnce(costs())
    .mockReturnValueOnce(earlier.promise).mockReturnValueOnce(later.promise);
  mount();
  await screen.findByText("¥43.20");
  act(() => window.dispatchEvent(new Event("focus")));
  act(() => window.dispatchEvent(new Event("focus")));
  expect(load).toHaveBeenCalledTimes(3);
  await act(async () => later.resolve(costs({totals: totals({estimated_cost_cny: 9.5})})));
  await screen.findByText("¥9.50");
  await act(async () => earlier.resolve(costs({totals: totals({estimated_cost_cny: 1})})));
  expect(screen.getByText("¥9.50")).toBeVisible();
  expect(screen.queryByText("¥1.00")).not.toBeInTheDocument();
});

test("changing owner hides the previous book immediately and ignores its late response", async () => {
  const oldRefresh = deferred<GenerationCosts>();
  const nextOwner = deferred<GenerationCosts>();
  const load = vi.spyOn(api, "generationCosts").mockResolvedValueOnce(costs())
    .mockReturnValueOnce(oldRefresh.promise).mockReturnValueOnce(nextOwner.promise);
  const view = mount();
  await screen.findByText("¥43.20");
  view.rerender(<MemoryRouter><GenerationCostPanel owner="book-a" refreshKey={1} /></MemoryRouter>);
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  view.rerender(<MemoryRouter><GenerationCostPanel owner="workshop:session-b" /></MemoryRouter>);
  expect(screen.queryByText("¥43.20")).not.toBeInTheDocument();
  expect(screen.getByText("正在读取创作用量…")).toBeVisible();
  await act(async () => nextOwner.resolve(costs({project_id: undefined, session_id: "session-b", totals: totals({estimated_cost_cny: 9.5})})));
  await screen.findByText("¥9.50");
  await act(async () => oldRefresh.resolve(costs({totals: totals({estimated_cost_cny: 999})})));
  expect(screen.queryByText("¥999.00")).not.toBeInTheDocument();
  expect(screen.getByText("¥9.50")).toBeVisible();
  expect(load).toHaveBeenLastCalledWith("workshop:session-b");
});

test("a failed initial request can be retried without any real network request", async () => {
  const load = vi.spyOn(api, "generationCosts").mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(costs());
  const user = userEvent.setup(); mount();
  await user.click(await screen.findByRole("button", {name: "重试用量统计"}));
  await screen.findByText("¥43.20");
  expect(load).toHaveBeenCalledTimes(2);
  expect(screen.queryByText("用量暂时无法读取")).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

test("a failed refresh retains labelled last results and retry replaces them", async () => {
  const load = vi.spyOn(api, "generationCosts").mockResolvedValueOnce(costs())
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(costs({totals: totals({estimated_cost_cny: 50})}));
  const user = userEvent.setup(); const view = mount();
  await screen.findByText("¥43.20");
  view.rerender(<MemoryRouter><GenerationCostPanel owner="book-a" refreshKey={1} /></MemoryRouter>);
  expect(await screen.findByRole("status")).toHaveTextContent("保留上次读取结果");
  expect(screen.getByText("¥43.20")).toBeVisible();
  await user.click(screen.getByRole("button", {name: "重试"}));
  await screen.findByText("¥50.00");
  expect(load).toHaveBeenCalledTimes(3);
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});


test("explicit zero usage and zero pricing remain real zero instead of unknown", async () => {
  const free = totals({input_tokens: 0, output_tokens: 0, cached_input_tokens: 0, reasoning_tokens: 0,
    total_tokens: 0, estimated_cost_usd: 0, estimated_cost_cny: 0});
  vi.spyOn(api, "generationCosts").mockResolvedValue(costs({totals: free,
    stages: [{...free, stage: "cover"}], models: []}));
  const user = userEvent.setup(); mount();
  await screen.findByText("已记录总费用 · 估算");
  expect(stage("封面设计").getByText("¥0.00")).toBeVisible();
  expect(stage("封面设计").getByText("0 tokens")).toBeVisible();
  expect(screen.queryByText("待核算")).not.toBeInTheDocument();
  await user.click(screen.getByText("用量明细与计费说明"));
  expect(metric("输入 Token")).toHaveTextContent(/^0 · 已记录$/);
  expect(metric("输出 Token")).toHaveTextContent(/^0 · 已记录$/);
});
