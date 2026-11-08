import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { workshopApi, type WorkshopSessionSummary } from "../api/workshop";
import { WORKSHOP_SESSION_CACHE_KEY } from "../lib/workshopSessionCache";
import { ConfirmProvider } from "./Confirm";
import WorkshopSessions from "./WorkshopSessions";

const row: WorkshopSessionSummary = {
  id: "discussion-1", title: "A New Choice", status: "draft", revision: 4,
  updated_at: "2026-11-07T00:00:00+08:00", project_id: "existing-book",
  message_count: 2, has_prompt: true, premise_preview: "A saved story",
};

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(workshopApi, "list").mockResolvedValue({ sessions: [row], total: 1 });
  vi.spyOn(workshopApi, "delete").mockResolvedValue(undefined);
});
afterEach(() => vi.restoreAllMocks());

function view(studio = true) {
  render(<MemoryRouter><ConfirmProvider>
    <WorkshopSessions studio={studio} busy={false} onSelect={vi.fn()} />
  </ConfirmProvider></MemoryRouter>);
}

it("requires confirmation, preserves the book and clears the deleted last-session pointer", async () => {
  const user = userEvent.setup();
  localStorage.setItem(WORKSHOP_SESSION_CACHE_KEY, row.id);
  view();
  await user.click(await screen.findByRole("button", { name: /删除讨论 A New Choice/ }));
  const dialog = await screen.findByRole("dialog", { name: "删除这条头脑风暴？" });
  expect(within(dialog).getByText(/已生成的作品、参考资料及费用记录会保留/)).toBeInTheDocument();
  expect(workshopApi.delete).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("button", { name: "取消" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(workshopApi.delete).not.toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "进入已关联作品 ↗" })).toHaveAttribute("href", "/projects/existing-book");
  await user.click(screen.getByRole("button", { name: /删除讨论 A New Choice/ }));
  vi.mocked(workshopApi.list).mockResolvedValue({ sessions: [], total: 0 });
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "删除记录" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: /恢复讨论/ })).not.toBeInTheDocument());
  expect(workshopApi.delete).toHaveBeenCalledExactlyOnceWith(row.id, 4);
  expect(localStorage.getItem(WORKSHOP_SESSION_CACHE_KEY)).toBeNull();
  expect(await screen.findByText("还没有保存的头脑风暴。")).toBeVisible();
});

it("keeps the record and refreshes after a deletion conflict", async () => {
  const user = userEvent.setup();
  vi.mocked(workshopApi.delete).mockRejectedValue(new Error("讨论已有更新，请刷新列表后再删除。"));
  localStorage.setItem(WORKSHOP_SESSION_CACHE_KEY, "another-session");
  view();
  await user.click(await screen.findByRole("button", { name: /删除讨论/ }));
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "删除记录" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("讨论已有更新");
  expect(screen.getByRole("button", { name: /恢复讨论/ })).toBeInTheDocument();
  expect(localStorage.getItem(WORKSHOP_SESSION_CACHE_KEY)).toBe("another-session");
  await waitFor(() => expect(workshopApi.list).toHaveBeenCalledTimes(2));
});

it("disables deletion while a discussion is running", async () => {
  vi.mocked(workshopApi.list).mockResolvedValue({ sessions: [{ ...row, status: "running" }], total: 1 });
  view();
  expect(await screen.findByRole("button", { name: /删除讨论/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /删除讨论/ })).toHaveAttribute("title", "请先暂停推导，再删除记录");
  expect(workshopApi.delete).not.toHaveBeenCalled();
});

it("keeps the embedded discussion switcher read-only", async () => {
  view(false);
  await screen.findByRole("button", { name: /恢复讨论/ });
  expect(screen.queryByRole("button", { name: /删除讨论/ })).not.toBeInTheDocument();
});
