import { useState } from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import * as documentApi from "../api/documentImport";
import StoryBriefInput from "./StoryBriefInput";

const result = {
  filename: "story.md",
  text: "新人物与新结局",
  characters: 7,
  warnings: [],
  pages: null,
};
function Editor({ initial = "原故事", disabled = false }) {
  const [text, setText] = useState(initial);
  const [pending, setPending] = useState(false);
  return (
    <>
      <StoryBriefInput
        value={text}
        onChange={setText}
        onPendingChange={setPending}
        disabled={disabled}
      />
      <button disabled={pending}>下一步</button>
    </>
  );
}
const upload = () =>
  userEvent.upload(
    screen.getByLabelText("选择故事构想文件"),
    new File(["source"], "story.md", { type: "text/markdown" }),
  );
afterEach(() => vi.restoreAllMocks());

test("previews before changing text and appends to the current author draft", async () => {
  vi.spyOn(documentApi, "importDocument").mockResolvedValue(result);
  render(<Editor />);
  await upload();
  expect(await screen.findByText("确认导入内容")).toBeVisible();
  expect(screen.getByLabelText("故事构想或已有框架")).toHaveValue("原故事");
  expect(screen.getByRole("button", { name: "下一步" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("故事构想或已有框架"), {
    target: { value: "等待时补充的想法" },
  });
  fireEvent.change(screen.getByLabelText("导入文字预览"), {
    target: { value: "修改后的导入结局" },
  });
  await userEvent.click(screen.getByRole("button", { name: "确认导入" }));
  expect(screen.getByLabelText("故事构想或已有框架")).toHaveValue(
    "等待时补充的想法\n\n修改后的导入结局",
  );
  expect(screen.getByRole("status")).toHaveTextContent("已导入");
  expect(screen.getByRole("button", { name: "下一步" })).toBeEnabled();
});

test("replacement is explicit and cancelling a later preview preserves confirmed text", async () => {
  vi.spyOn(documentApi, "importDocument").mockResolvedValue(result);
  render(<Editor />);
  await upload();
  await userEvent.click(await screen.findByLabelText("替换现有内容"));
  await userEvent.click(screen.getByRole("button", { name: "确认导入" }));
  expect(screen.getByLabelText("故事构想或已有框架")).toHaveValue(result.text);
  await upload();
  await userEvent.click(
    await screen.findByRole("button", { name: "取消导入" }),
  );
  expect(screen.queryByLabelText("导入文字预览")).not.toBeInTheDocument();
  expect(screen.getByLabelText("故事构想或已有框架")).toHaveValue(result.text);
});

test("failed extraction preserves the draft and allows selecting the same file again", async () => {
  const call = vi
    .spyOn(documentApi, "importDocument")
    .mockRejectedValueOnce(new Error("扫描版 PDF 需要 OCR"))
    .mockResolvedValue(result);
  render(<Editor />);
  await upload();
  expect(await screen.findByRole("alert")).toHaveTextContent("需要 OCR");
  expect(screen.getByLabelText("故事构想或已有框架")).toHaveValue("原故事");
  await upload();
  expect(await screen.findByLabelText("导入文字预览")).toHaveValue(result.text);
  expect(call).toHaveBeenCalledTimes(2);
});

test("cancel and unmount abort extraction and never apply late results", async () => {
  let resolve!: (value: documentApi.ImportedDocument) => void;
  const call = vi.spyOn(documentApi, "importDocument").mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  const page = render(<Editor />);
  await upload();
  expect(screen.getByRole("status")).toHaveTextContent("正在提取文字");
  await userEvent.click(screen.getByRole("button", { name: "取消导入" }));
  expect(call.mock.calls[0][1].aborted).toBe(true);
  await act(async () => resolve(result));
  expect(screen.queryByLabelText("导入文字预览")).not.toBeInTheDocument();
  expect(screen.getByLabelText("故事构想或已有框架")).toHaveValue("原故事");
  await upload();
  page.unmount();
  expect(call.mock.calls[1][1].aborted).toBe(true);
});

test("read-only saved settings cannot import or edit", async () => {
  const call = vi.spyOn(documentApi, "importDocument");
  render(<Editor disabled />);
  expect(screen.getByRole("button", { name: "导入文件" })).toBeDisabled();
  expect(screen.getByLabelText("故事构想或已有框架")).toBeDisabled();
  await waitFor(() => expect(call).not.toHaveBeenCalled());
});
