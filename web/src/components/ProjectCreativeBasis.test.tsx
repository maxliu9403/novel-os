import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import ProjectCreativeBasis from "./ProjectCreativeBasis";
import { projectCreativeBasisApi, type ProjectCreativeBasis as Basis } from "../api/projectCreativeBasis";

afterEach(() => vi.restoreAllMocks());
const report: Basis = {
  recorded: true, run_id: "run1", audience_channel: "female", binding: "execution_lock",
  source_sha256: "source-hash", handoff_sha256: "handoff-hash", lock_sha256: "lock-hash", selection_sha256: "selection-hash",
  documents: [{ id: "tag.betrayal", kind: "tag", name: "背叛", version: 4, source_sha256: "tag-hash" }],
  historical_designs: [{ id: "p1", title: "早期合作方案", summary: "旧方案让主角留下", status: "revised", author_note: "现在让主角离开", choice: "保持合作", cost: "失去工作", payoff: "赢回尊严", tag_contributions: [{ tag_id: "tag.betrayal", tag_name: "背叛", contribution: "背叛迫使主角决定去留" }] }],
  authority_note: "最终采用骨架优先。以下为历史已选设计，后续自由文本修订可能已调整它们。",
  evidence_note: "已规划／正文体现待确认。",
};

async function open() {
  await userEvent.click(screen.getByText("创作依据"));
}

it("loads only on expansion and distinguishes historical choices from verified prose", async () => {
  const get = vi.spyOn(projectCreativeBasisApi, "get").mockResolvedValue(report);
  render(<ProjectCreativeBasis projectId="book" />);
  expect(get).not.toHaveBeenCalled();
  await open();
  expect(await screen.findByText("读者频道 · 女频")).toBeVisible();
  expect(screen.getByText(report.authority_note)).toBeVisible();
  expect(screen.getByText(report.evidence_note)).toBeVisible();
  expect(screen.getByText("背叛迫使主角决定去留")).toBeVisible();
  expect(screen.getByText("现在让主角离开")).toBeVisible();
  expect(screen.getByRole("link", { name: "下载创作依据 MD" }).getAttribute("href")).toMatch(/\/api\/projects\/book\/creative-basis\/markdown$/);
  expect(get).toHaveBeenCalledTimes(1);
});

it("shows unrecorded old books without fabricating a channel or a download", async () => {
  vi.spyOn(projectCreativeBasisApi, "get").mockResolvedValue({ ...report, recorded: false, historical_designs: [], documents: [] });
  render(<ProjectCreativeBasis projectId="old-book" />);
  await open();
  expect(await screen.findByText("本作品未记录冻结创作依据。")).toBeVisible();
  expect(screen.queryByText("读者频道 · 女频")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "下载创作依据 MD" })).not.toBeInTheDocument();
});

it("surfaces integrity errors and does not expose a stale download", async () => {
  const get = vi.spyOn(projectCreativeBasisApi, "get").mockRejectedValue(new Error("创作依据完整性校验失败"));
  render(<ProjectCreativeBasis projectId="book" />);
  await open();
  expect(await screen.findByRole("alert")).toHaveTextContent("完整性校验失败");
  expect(screen.queryByRole("link", { name: "下载创作依据 MD" })).not.toBeInTheDocument();
  get.mockResolvedValue(report);
  fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
  await waitFor(() => expect(screen.getByRole("link", { name: "下载创作依据 MD" })).toBeVisible());
});
