import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { useState } from "react";
import { afterEach, beforeEach, vi, test, expect } from "vitest";
import { creativeTypesApi, type CreativeTypeRef } from "../api/creativeTypes";
import CreativeTypes from "../routes/CreativeTypes";
import CreativeTypePicker from "../components/CreativeTypePicker";
import { typeRecord, typeLibrary } from "./creativeTypesFixture";

beforeEach(() => {
  vi.spyOn(creativeTypesApi, "list").mockResolvedValue(typeLibrary());
  vi.spyOn(creativeTypesApi, "get").mockResolvedValue(typeRecord());
  vi.spyOn(creativeTypesApi, "prepare").mockImplementation(async (body, options) => {
    const record = typeRecord();
    const header = (options?.base_markdown ?? record.draft).match(/^---\n[\s\S]*?\n---\n/)![0];
    return { needs_identity: false, markdown: header + body,
      document: { ...record.document!, schema: "novel-knowledge.v3", body } };
  });
});
afterEach(() => vi.restoreAllMocks());

test("editor shows only author prose, with free headings instead of a YAML contract", async () => {
  const record = typeRecord("tag.age_gap", "年龄差", ["Age Gap"]);
  record.document = { ...record.document!, schema: "novel-knowledge.v3", body: "前言\n## 我的自由标题\n我的要求。" };
  vi.mocked(creativeTypesApi.get).mockResolvedValue(record);
  render(<MemoryRouter><CreativeTypes /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "我的自由标题" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "编写 Markdown" }));
  expect(screen.getByLabelText("资料 Markdown")).toHaveValue(record.document.body);
  expect(screen.getByLabelText("资料 Markdown")).not.toHaveValue(expect.stringContaining("schema:"));
  expect(screen.getByText("正文完整用于创作，标题与分节不限。")).toBeInTheDocument();
});

test("replacing the editor with a complete file prepares its prose without saving", async () => {
  const record = typeRecord();
  const body = "## 自由标题\n完整保留的写作要求。";
  const file = record.draft.match(/^---\n[\s\S]*?\n---\n/)![0] + body;
  vi.mocked(creativeTypesApi.prepare).mockResolvedValue({
    needs_identity: false, markdown: file,
    document: { ...record.document!, schema: "novel-knowledge.v3", body },
  });
  const mutate = vi.spyOn(creativeTypesApi, "mutate");
  render(<MemoryRouter><CreativeTypes /></MemoryRouter>);
  await userEvent.click(await screen.findByRole("tab", { name: "编写 Markdown" }));
  const editor = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "资料 Markdown" });
  editor.setSelectionRange(0, editor.value.length);
  fireEvent.paste(editor, { clipboardData: { getData: () => file } });
  await waitFor(() => expect(editor).toHaveValue(body));
  expect(screen.getByRole("status")).toHaveTextContent("已识别文件信息，仅粘贴正文。");
  expect(creativeTypesApi.prepare).toHaveBeenCalledWith(file, { base_markdown: record.draft });
  expect(mutate).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("tab", { name: "阅读预览" }));
  expect(screen.getByRole("heading", { name: "自由标题" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: /schema:/ })).not.toBeInTheDocument();
});

test("standalone Tag preview shows its complete preamble, extra sections and beat table", async () => {
  const record = typeRecord("tag.betrayal", "背叛", ["Betrayal"]);
  record.draft =
    "# 背叛 Betrayal\n\n> 关联 Genre：Romance · 浪漫\n> 作者原文的基调\n\n## 人物配置\n她要自己拿回决定权。\n\n## 前 10 章强制节拍\n| 章 | 事件 |\n|---|---|\n| 1 | 做出选择 |\n";
  record.document = {
    ...record.document!,
    format: "tag-prompt.v1",
    appendices: [],
  };
  vi.mocked(creativeTypesApi.get).mockResolvedValue(record);
  vi.spyOn(creativeTypesApi, "preview").mockResolvedValue({
    document: record.document,
    bytes: 300,
  });
  render(
    <MemoryRouter>
      <CreativeTypes />
    </MemoryRouter>,
  );
  await screen.findByRole("tab", { name: "阅读预览" });
  expect(screen.getByRole("tab", { name: "阅读预览" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(
    await screen.findByRole("heading", { name: "背叛 Betrayal" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/作者原文的基调/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "人物配置" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "做出选择" })).toBeInTheDocument();
  expect(screen.queryByText(/附录，不进入模型输入/)).not.toBeInTheDocument();
});

test("Markdown is the single editable source; unchanged page hides save and successful save is acknowledged", async () => {
  const original = typeRecord();
  const mutate = vi
    .spyOn(creativeTypesApi, "mutate")
    .mockImplementation(
      async (_id, _action, _revision, _operation, markdown) => ({
        ...original,
        revision: 1,
        draft: markdown!,
      }),
    );
  render(
    <MemoryRouter>
      <CreativeTypes />
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("tab", { name: "编写 Markdown" }),
  );
  const editor = await screen.findByLabelText("资料 Markdown");
  expect(
    screen.queryByRole("button", { name: "保存草稿" }),
  ).not.toBeInTheDocument();
  fireEvent.change(editor, {
    target: { value: original.draft.replace(/^---\n[\s\S]*?\n---\n/, "") + "\nAn additional choice." },
  });
  await userEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  expect(
    await screen.findByRole("dialog", { name: "已保存" }),
  ).toBeInTheDocument();
  expect(mutate).toHaveBeenCalledWith(
    original.id,
    "draft",
    0,
    expect.any(String),
    original.draft + "\nAn additional choice.",
    undefined,
  );
  expect(
    screen.queryByRole("button", { name: "保存草稿" }),
  ).not.toBeInTheDocument();
});

test("a failed save retains author Markdown and reuses operation identity on retry", async () => {
  const mutate = vi
    .spyOn(creativeTypesApi, "mutate")
    .mockRejectedValue(new Error("transport failed"));
  render(
    <MemoryRouter>
      <CreativeTypes />
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("tab", { name: "编写 Markdown" }),
  );
  fireEvent.change(await screen.findByLabelText("资料 Markdown"), {
    target: { value: "my unsaved complete document" },
  });
  await userEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "transport failed",
  );
  expect(screen.getByLabelText("资料 Markdown")).toHaveValue(
    "my unsaved complete document",
  );
  await userEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  expect(mutate.mock.calls[0]).toEqual(mutate.mock.calls[1]);
});

test("historical version can be enabled without changing its saved text", async () => {
  const record = typeRecord();
  const old = record.versions[0];
  record.versions.push({
    ...old,
    version: 2,
    markdown: old.markdown + "\nNew promise.",
  });
  record.active_version = 2;
  record.draft = record.versions[1].markdown;
  vi.mocked(creativeTypesApi.get).mockResolvedValue(record);
  const mutate = vi
    .spyOn(creativeTypesApi, "mutate")
    .mockResolvedValue({
      ...record,
      revision: 1,
      active_version: 1,
      draft: old.markdown,
    });
  render(
    <MemoryRouter>
      <CreativeTypes />
    </MemoryRouter>,
  );
  await userEvent.click(await screen.findByRole("tab", { name: "版本记录" }));
  await userEvent.click(screen.getByText(/^v1/));
  await userEvent.click(screen.getByRole("button", { name: "使用 v1" }));
  expect(mutate).toHaveBeenCalledWith(
    record.id,
    "restore",
    0,
    expect.any(String),
    undefined,
    1,
  );
  expect(
    await screen.findByRole("dialog", { name: "已保存" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "阅读预览" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

test("only associated tags are offered; changing genre clears incompatible tags", async () => {
  const horror = typeRecord("genre.horror", "恐怖");
  const tag = typeRecord("tag.dread", "恐惧");
  tag.document!.genre_ids = ["genre.horror"];
  vi.mocked(creativeTypesApi.list).mockResolvedValue(
    typeLibrary([
      typeRecord(),
      typeRecord("tag.betrayal", "背叛"),
      horror,
      tag,
    ]),
  );
  function Picker() {
    const [refs, setRefs] = useState<CreativeTypeRef[]>([]);
    return (
      <>
        <CreativeTypePicker refs={refs} onChange={(_, r) => setRefs(r)} />
        <output data-testid="selection">{JSON.stringify(refs)}</output>
      </>
    );
  }
  render(
    <MemoryRouter>
      <Picker />
    </MemoryRouter>,
  );
  await userEvent.click(await screen.findByRole("radio", { name: "Romance" }));
  expect(
    screen.queryByRole("button", { name: "恐惧" }),
  ).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "背叛" }));
  expect(screen.getByTestId("selection")).toHaveTextContent("tag.betrayal");
  await userEvent.click(screen.getByRole("radio", { name: "恐怖" }));
  expect(screen.getByTestId("selection")).toHaveTextContent("tag.betrayal");
  await userEvent.click(screen.getByRole("button", { name: "确认更换" }));
  expect(screen.getByTestId("selection")).not.toHaveTextContent("tag.betrayal");
  expect(screen.getByRole("button", { name: "恐惧" })).toBeInTheDocument();
});


test("headed Markdown import keeps custom content and stages replacement before activation", async () => {
  const record = typeRecord("tag.betrayal", "背叛");
  const body = "前言原文\n## 一、自定义标题\n作者要求。";
  const prepared = { needs_identity: false as const, markdown: "generated metadata\n" + body,
    document: { ...record.document!, schema: "novel-knowledge.v3" as const, body } };
  vi.mocked(creativeTypesApi.prepare).mockResolvedValue(prepared);
  vi.mocked(creativeTypesApi.get).mockResolvedValue(record);
  const mutate = vi.spyOn(creativeTypesApi, "mutate").mockResolvedValue({ ...record, draft: prepared.markdown, document: prepared.document });
  render(<MemoryRouter><CreativeTypes /></MemoryRouter>);
  await screen.findByRole("tab", { name: "阅读预览" });
  await userEvent.upload(screen.getByLabelText("导入 Markdown"), new File(["original file"], "custom.md", { type: "text/markdown" }));
  expect(await screen.findByRole("heading", { name: "一、自定义标题" })).toBeInTheDocument();
  expect(mutate).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "保存并启用" }));
  expect(mutate).toHaveBeenCalledWith(record.id, "activate", record.revision, expect.any(String), prepared.markdown, undefined);
});

test("plain Markdown asks for identity once and can replace an existing Tag without YAML", async () => {
  const record = typeRecord("tag.betrayal", "背叛");
  const body = "没有文件头，也没有固定标题。\n";
  vi.mocked(creativeTypesApi.get).mockResolvedValue(record);
  vi.mocked(creativeTypesApi.prepare)
    .mockResolvedValueOnce({ needs_identity: true, body })
    .mockResolvedValueOnce({ needs_identity: false, markdown: "envelope\n" + body,
      document: { ...record.document!, schema: "novel-knowledge.v3", body } });
  const create = vi.spyOn(creativeTypesApi, "create");
  render(<MemoryRouter><CreativeTypes /></MemoryRouter>);
  await screen.findByRole("tab", { name: "阅读预览" });
  await userEvent.upload(screen.getByLabelText("导入 Markdown"), new File([body], "背叛.md", { type: "text/markdown" }));
  expect(await screen.findByRole("dialog", { name: "导入写作资料" })).toBeInTheDocument();
  expect(screen.getByLabelText("名称")).toHaveValue("背叛");
  await userEvent.selectOptions(screen.getByLabelText("保存到"), record.id);
  await userEvent.click(screen.getByRole("button", { name: "导入到编辑器" }));
  expect(await screen.findByText(body.trim())).toBeInTheDocument();
  expect(creativeTypesApi.prepare).toHaveBeenLastCalledWith(body, { base_markdown: record.draft });
  expect(create).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});
