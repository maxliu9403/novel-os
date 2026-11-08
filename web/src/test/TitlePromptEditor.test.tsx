import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import TitlePromptEditor from "../components/TitlePromptEditor";
import { systemPromptsApi, type SystemPromptRecord } from "../api/systemPrompts";
import { ConfirmCtx } from "../components/confirmContext";

const first = { version: 1, markdown: "# 书名方法\n以人物选择制造冲突。", sha256: "one", created_at: null };
function fixture(): SystemPromptRecord {
  return { id: "title-design", revision: 0, current: first, versions: [first], default_markdown: first.markdown, max_bytes: 12000 };
}
beforeEach(() => { vi.spyOn(systemPromptsApi, "get").mockResolvedValue(fixture()); });
afterEach(() => { vi.restoreAllMocks(); });

test("unchanged prompt has no save button; editing saves and acknowledges success", async () => {
  const save = vi.spyOn(systemPromptsApi, "save").mockImplementation(async (markdown) => {
    const next = { ...first, version: 2, markdown };
    return { ...fixture(), revision: 1, current: next, versions: [first, next] };
  });
  render(<TitlePromptEditor onClose={() => {}} />);
  await screen.findByRole("heading", { name: "书名方法" });
  expect(screen.queryByRole("button", { name: "保存并生效" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "编写 Markdown" }));
  fireEvent.change(screen.getByRole("textbox", { name: "书名设计 Markdown" }), { target: { value: "# 新方法\n凸显危险承诺" } });
  fireEvent.click(screen.getByRole("button", { name: "保存并生效" }));
  const dialog = await screen.findByRole("dialog", { name: "已保存" });
  expect(save).toHaveBeenCalledWith("# 新方法\n凸显危险承诺", 0, expect.any(String));
  fireEvent.click(within(dialog).getByRole("button", { name: "继续查看" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "保存并生效" })).not.toBeInTheDocument());
});

test("history is a draft until explicitly saved, and cancelled close preserves edits", async () => {
  const second = { ...first, version: 2, markdown: "# 第二版\n具体的关系反差" };
  vi.mocked(systemPromptsApi.get).mockResolvedValue({ ...fixture(), revision: 1, current: second, versions: [first, second] });
  const save = vi.spyOn(systemPromptsApi, "save");
  const close = vi.fn();
  const confirm = vi.fn().mockResolvedValue(false);
  render(<ConfirmCtx.Provider value={confirm}><TitlePromptEditor onClose={close} /></ConfirmCtx.Provider>);
  await screen.findByRole("heading", { name: "第二版" });
  fireEvent.click(screen.getByRole("tab", { name: "版本记录" }));
  fireEvent.click(screen.getByRole("button", { name: /v1/ }));
  fireEvent.click(screen.getByRole("button", { name: "载入此版本" }));
  expect(await screen.findByRole("textbox", { name: "书名设计 Markdown" })).toHaveValue(first.markdown);
  expect(save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "关闭" }));
  await waitFor(() => expect(confirm).toHaveBeenCalled());
  expect(close).not.toHaveBeenCalled();
});

test("save failure preserves draft and reuses request identity for a retry", async () => {
  const save = vi.spyOn(systemPromptsApi, "save").mockRejectedValue(new Error("网络暂不可用"));
  render(<TitlePromptEditor onClose={() => {}} />);
  await screen.findByRole("heading", { name: "书名方法" });
  fireEvent.click(screen.getByRole("tab", { name: "编写 Markdown" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "retain this draft" } });
  fireEvent.click(screen.getByRole("button", { name: "保存并生效" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("textbox")).toHaveValue("retain this draft");
  fireEvent.click(screen.getByRole("button", { name: "保存并生效" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
  expect(save.mock.calls[0]).toEqual(save.mock.calls[1]);
});
