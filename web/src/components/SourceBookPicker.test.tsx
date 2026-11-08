import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import SourceBookPicker from "./SourceBookPicker";
import { ConfirmProvider } from "./Confirm";
import { sourceBooksApi, type SourceDetail } from "../api/sourceBooks";
vi.mock("../api/sourceBooks", () => ({
  sourceBooksApi: {
    list: vi.fn(),
    get: vi.fn(),
    upload: vi.fn(),
    analyze: vi.fn(),
    cancel: vi.fn(),
    coverUrl: () => "/fixture.png",
  },
}));
const book: SourceDetail = {
  id: "source-1",
  title: "Reference Book",
  book_id: "1",
  chapter_count: 3,
  supplement_count: 0,
  cover_url: "/cover",
  warnings: [],
  synopsis: "Official promise",
  study: { status: "pending", completed: 0, total: 0 },
  result: null,
};
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(sourceBooksApi.list).mockResolvedValue({ items: [book] });
  vi.mocked(sourceBooksApi.get).mockResolvedValue(book);
});
it("requires completed reading and does not call a model on selection", async () => {
  const user = userEvent.setup(),
    change = vi.fn(),
    ready = vi.fn();
  render(<SourceBookPicker onChange={change} onReady={ready} />);
  await user.click(screen.getByRole("radio", { name: "改编重写" }));
  await screen.findByRole("option", { name: /Reference Book/ });
  await user.selectOptions(screen.getByLabelText("选择参考小说"), "source-1");
  await screen.findByText("Official promise");
  expect(ready).toHaveBeenLastCalledWith(false);
  expect(change).toHaveBeenLastCalledWith(null);
  expect(sourceBooksApi.analyze).not.toHaveBeenCalled();
  vi.mocked(sourceBooksApi.get).mockResolvedValue({
    ...book,
    study: { status: "complete", completed: 1, total: 1 },
    result: {
      analysis_sha256: "sha",
      analysis: {
        story_summary: "Read supplied chapters only",
        uncertainties: ["Ending unknown"],
      },
      coverage: { files: 4, segments: 1, all_supplied_text_sent: true },
    },
  });
  vi.mocked(sourceBooksApi.analyze).mockResolvedValue({
    status: "complete",
    completed: 1,
    total: 1,
  });
  await user.click(screen.getByRole("button", { name: "读取全部资料" }));
  await waitFor(() => expect(ready).toHaveBeenLastCalledWith(true));
  expect(change).toHaveBeenLastCalledWith({
    source_id: "source-1",
    analysis_sha256: "sha",
    mode: "original_rewrite",
    use_cover: true,
  });
  await user.click(screen.getByRole("checkbox"));
  expect(change).toHaveBeenLastCalledWith(
    expect.objectContaining({ use_cover: false }),
  );
});
it("restores interrupted reading and can explicitly restart after a configuration change", async () => {
  const user = userEvent.setup();
  vi.mocked(sourceBooksApi.get).mockResolvedValue({
    ...book,
    study: { status: "failed", completed: 1, total: 3, error: "模型配置变化" },
  });
  render(
    <ConfirmProvider>
      <SourceBookPicker
        value={{
          source_id: "source-1",
          analysis_sha256: "old",
          mode: "reference_creation",
          use_cover: true,
        }}
        onChange={vi.fn()}
        onReady={vi.fn()}
      />
    </ConfirmProvider>,
  );
  await screen.findByText("模型配置变化");
  await user.click(screen.getByRole("button", { name: "重新读取" }));
  const dialog = await screen.findByRole("dialog", {
    name: "重新读取参考资料？",
  });
  await waitFor(() =>
    expect(within(dialog).getByText(/已有分析会保留在版本记录中/)).toBeVisible(),
  );
  expect(sourceBooksApi.analyze).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("button", { name: "取消" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(sourceBooksApi.analyze).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "重新读取" }));
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", {
    name: "重新读取",
  }));
  await waitFor(() => expect(sourceBooksApi.analyze).toHaveBeenCalledExactlyOnceWith("source-1", true));
});
it("restores an unfinished selected source after closing and reopening without starting a second task", async () => {
  localStorage.clear();
  const user = userEvent.setup();
  vi.mocked(sourceBooksApi.get).mockResolvedValue({
    ...book,
    study: { status: "running", completed: 0, total: 1 },
  });
  const first = render(
    <SourceBookPicker onChange={vi.fn()} onReady={vi.fn()} />,
  );
  await user.click(screen.getByRole("radio", { name: "原故事推演" }));
  await user.selectOptions(
    await screen.findByLabelText("选择参考小说"),
    "source-1",
  );
  await screen.findByText("Official promise");
  first.unmount();
  render(<SourceBookPicker onChange={vi.fn()} onReady={vi.fn()} />);
  await waitFor(() =>
    expect(screen.getByRole("radio", { name: "原故事推演" })).toHaveAttribute(
      "aria-checked",
      "true",
    ),
  );
  expect(await screen.findByLabelText("选择参考小说")).toHaveValue("source-1");
  expect(sourceBooksApi.analyze).not.toHaveBeenCalled();
});

it("can reuse a saved analysis while a new analysis is running", async () => {
  const ready = vi.fn(),
    change = vi.fn();
  vi.mocked(sourceBooksApi.get).mockResolvedValue({
    ...book,
    study: { status: "running", completed: 0, total: 1 },
    versions: [
      {
        analysis_sha256: "saved-sha",
        created_at: "2026-11-06T10:00:00Z",
        has_plan: false,
      },
    ],
    result: {
      analysis_sha256: "saved-sha",
      analysis: { story_summary: "Saved reading", uncertainties: [] },
      coverage: { files: 4, segments: 1, all_supplied_text_sent: true },
    },
  });
  render(
    <SourceBookPicker
      value={{
        source_id: book.id,
        analysis_sha256: "saved-sha",
        mode: "source_development",
        use_cover: true,
      }}
      onReady={ready}
      onChange={change}
    />,
  );
  await screen.findByText("Saved reading");
  await waitFor(() => expect(ready).toHaveBeenLastCalledWith(true));
  expect(change).toHaveBeenLastCalledWith(
    expect.objectContaining({ analysis_sha256: "saved-sha" }),
  );
  expect(sourceBooksApi.analyze).not.toHaveBeenCalled();
});
