import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api } from "../api/client";
import { workshopApi, type WorkshopSession, type WorkshopSessionSummary } from "../api/workshop";
import WorkshopLibrary from "./WorkshopLibrary";
import Sidebar from "../components/Sidebar";

const input = { title: "未完成的信", author: "", genres: [], premise: "母亲收到一封旧信。", language: "中文", audience: "", market: "", constraints: "" };
function summary(status: WorkshopSession["status"], index = 1): WorkshopSessionSummary {
  return { id: `session-${index}`, title: `讨论 ${index}`, status, revision: 4, updated_at: "2026-10-11T15:00:00Z", project_id: status === "launched" ? "published-book" : null,
    has_prompt: true, message_count: 8, premise_preview: input.premise };
}
function session(patch: Partial<WorkshopSession> = {}): WorkshopSession {
  return { id: "session-1", revision: 4, input, messages: [{ role: "assistant", content: "已保存的故事方向" }], prompt_md: "# 当前骨架", questions: [], ready_for_confirmation: false,
    status: "draft", last_error: null, job_id: null, skill_version: "v1", prepared: null, project_id: null, logs: [], ...patch };
}
function Location() { return <output data-testid="url">{useLocation().search}</output>; }
function view(url = "/workshop") { return render(<MemoryRouter initialEntries={[url]}><WorkshopLibrary /><Location /></MemoryRouter>); }

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(api, "studioModels").mockRejectedValue(new Error("Not configured"));
  vi.spyOn(workshopApi, "turn").mockRejectedValue(new Error("Unexpected model call"));
  vi.spyOn(workshopApi, "restart").mockRejectedValue(new Error("Unexpected model call"));
  vi.spyOn(workshopApi, "resume").mockRejectedValue(new Error("Unexpected model call"));
  vi.spyOn(workshopApi, "get").mockResolvedValue(session());
  vi.spyOn(workshopApi, "list").mockResolvedValue({ sessions: [summary("draft"), summary("launched", 2)], total: 2 });
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

test("history includes launched discussions and opens the chosen saved session without running a model", async () => {
  const user = userEvent.setup(); view();
  expect(await screen.findByText("讨论 1")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "恢复讨论 讨论 2（session-2）" })).toHaveTextContent("已启动创作");
  expect(screen.getByRole("link", { name: "进入已关联作品 ↗" })).toHaveAttribute("href", "/projects/published-book");
  await user.click(screen.getByRole("button", { name: "恢复讨论 讨论 1（session-1）" }));
  expect(await screen.findByText("已保存的故事方向")).toBeInTheDocument();
  expect(screen.getByTestId("url")).toHaveTextContent("session=session-1");
  expect(workshopApi.turn).not.toHaveBeenCalled();
  expect(workshopApi.resume).not.toHaveBeenCalled();
  expect(workshopApi.restart).not.toHaveBeenCalled();
});

test("a direct URL restores the selected paused session instead of whichever session was last cached", async () => {
  localStorage.setItem("novel-os.workshop.active-session", "different-session");
  vi.mocked(workshopApi.get).mockResolvedValue(session({ status: "paused", job_id: "old-job" }));
  view("/workshop?session=session-1");
  expect(await screen.findByText("已保存的故事方向")).toBeInTheDocument();
  expect(workshopApi.get).toHaveBeenCalledWith("session-1");
  expect(workshopApi.get).not.toHaveBeenCalledWith("different-session");
  expect(workshopApi.resume).not.toHaveBeenCalled();
});

test("status filtering is sent to the server and pagination respects the filter", async () => {
  vi.mocked(workshopApi.list).mockImplementation(async (_query, _limit, offset, status) => ({
    sessions: Array.from({ length: offset ? 1 : 20 }, (_, index) => summary(status === "paused" ? "paused" : "draft", offset + index + 1)), total: 21,
  }));
  const user = userEvent.setup(); view();
  await screen.findByText("讨论 1");
  await user.selectOptions(screen.getByLabelText("按讨论状态筛选"), "paused");
  await waitFor(() => expect(workshopApi.list).toHaveBeenCalledWith("", 20, 0, "paused"));
  await user.click(screen.getByRole("button", { name: "加载更多讨论" }));
  expect(await screen.findByText("讨论 21")).toBeInTheDocument();
  expect(workshopApi.list).toHaveBeenCalledWith("", 20, 20, "paused");
});

test("a fresh-start decision survives reopening and declining it does not run a model", async () => {
  vi.mocked(workshopApi.get).mockResolvedValue(session({ restart_required: true, input_needs_revision: true }));
  const user = userEvent.setup(); view("/workshop?session=session-1");
  await user.click(await screen.findByRole("button", { name: "暂不推导" }));
  expect(screen.getByLabelText("故事构想或已有框架")).toHaveValue(input.premise);
  expect(workshopApi.restart).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "查看从头推导选项" }));
  expect(screen.getByRole("button", { name: "确认，从头推导" })).toBeInTheDocument();
});

test("refresh errors preserve the last known sessions and allow retry", async () => {
  const user = userEvent.setup(); view(); await screen.findByText("讨论 1");
  vi.mocked(workshopApi.list).mockRejectedValueOnce(new Error("网络断开"));
  await user.click(screen.getByRole("button", { name: "刷新列表" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("网络断开");
  expect(screen.getByText("讨论 1")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "刷新列表" }));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

test("polling refreshes status without launching or resuming work", async () => {
  vi.mocked(workshopApi.list).mockResolvedValueOnce({ sessions: [summary("running")], total: 1 });
  view(); await screen.findByRole("button", { name: "恢复讨论 讨论 1（session-1）" });
  expect(screen.getByRole("button", { name: "恢复讨论 讨论 1（session-1）" })).toHaveTextContent("推导中");
  vi.mocked(workshopApi.list).mockResolvedValue({ sessions: [summary("ready")], total: 1 });
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  await waitFor(() => expect(screen.getByRole("button", { name: "恢复讨论 讨论 1（session-1）" })).toHaveTextContent("骨架待确认"));
  expect(workshopApi.turn).not.toHaveBeenCalled(); expect(workshopApi.resume).not.toHaveBeenCalled();
});

test("sidebar exposes a stable workshop history destination", () => {
  render(<MemoryRouter initialEntries={["/workshop"]}><Sidebar /></MemoryRouter>);
  expect(screen.getByRole("link", { name: "头脑风暴" })).toHaveAttribute("href", "/workshop");
  expect(screen.getByRole("link", { name: "头脑风暴" })).toHaveAttribute("aria-current", "page");
});
