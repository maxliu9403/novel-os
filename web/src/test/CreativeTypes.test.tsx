import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";
import {
  creativeTypesApi,
  CreativeApiError,
  bindCreativeSelection,
  creativeRef,
  type CreativePreview,
  type SessionTypeContext,
} from "../api/creativeTypes";
import type { WorkshopSession } from "../api/workshop";
import CreativeTypes from "../routes/CreativeTypes";
import CreativeTypePicker from "../components/CreativeTypePicker";
import WorkshopTypeSnapshot from "../components/WorkshopTypeSnapshot";
import { typeRecord, typeLibrary, typeSummary } from "./creativeTypesFixture";
const emptyPreview: CreativePreview = {
  context: { schema: "genre-context.v1", stage: "brainstorm", profiles: [] },
  sha256: "hash",
  bytes: 100,
  preview_only: true,
};
beforeEach(() => {
  vi.spyOn(creativeTypesApi, "list").mockResolvedValue(typeLibrary());
  vi.spyOn(creativeTypesApi, "get").mockImplementation(async (id) =>
    typeRecord(id),
  );
  vi.spyOn(creativeTypesApi, "preview").mockResolvedValue(emptyPreview);
});
afterEach(() => vi.restoreAllMocks());

test("saves a draft, keeps enabled preview immutable, and publishes only on explicit activation", async () => {
  const original = typeRecord();
  let current = original;
  const mutate = vi
    .spyOn(creativeTypesApi, "mutate")
    .mockImplementation(async (_id, action, _revision, _op, profile) => {
      current = { ...current, revision: current.revision + 1, draft: profile! };
      if (action === "activate")
        current = {
          ...current,
          active_version: 2,
          versions: [
            ...original.versions,
            {
              profile: { ...profile!, version: 2 },
              sha256: "b".repeat(64),
              created_at: "now",
            },
          ],
        };
      return current;
    });
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <CreativeTypes />
    </MemoryRouter>,
  );
  const summary = await screen.findByLabelText("一句话说明");
  expect(screen.queryByRole("button", { name: "保存并启用 ↗" })).not.toBeInTheDocument();
  fireEvent.change(summary, {
    target: { value: "保留自我，实现新的关系选择" },
  });
  expect(screen.getByRole("button", { name: "保存并启用 ↗" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "保存草稿" }));
  expect(mutate).toHaveBeenCalledWith(
    original.id,
    "draft",
    0,
    expect.any(String),
    expect.objectContaining({ summary: "保留自我，实现新的关系选择" }),
  );
  expect(await screen.findByRole("dialog", { name: "已保存" })).toHaveTextContent("草稿已保存");
  expect(screen.queryByRole("button", { name: "保存并启用 ↗" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "知道了" }));
  await user.click(screen.getByRole("tab", { name: "模型预览与版本" }));
  await waitFor(() =>
    expect(creativeTypesApi.preview).toHaveBeenCalledWith({
      profile: original.versions[0].profile,
      stage: "brainstorm",
    }),
  );
  await user.click(screen.getByRole("button", { name: "启用此草稿" }));
  await waitFor(() =>
    expect(mutate).toHaveBeenLastCalledWith(
      original.id,
      "activate",
      1,
      expect.any(String),
      expect.objectContaining({ summary: "保留自我，实现新的关系选择" }),
    ),
  );
  expect(await screen.findByRole("dialog", { name: "已保存" })).toHaveTextContent("资料已保存并启用");
});

test("concurrent-save conflict preserves local editing and requires latest revision before retry", async () => {
  const original = typeRecord();
  const mutate = vi
    .spyOn(creativeTypesApi, "mutate")
    .mockRejectedValue(new CreativeApiError("资料已被修改", 409));
  render(
    <MemoryRouter>
      <CreativeTypes />
    </MemoryRouter>,
  );
  fireEvent.change(await screen.findByLabelText("类型名称"), {
    target: { value: "我的本地修改" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("资料已被修改");
  expect(screen.queryByRole("dialog", { name: "已保存" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("类型名称")).toHaveValue("我的本地修改");
  expect(screen.getByRole("button", { name: "保存草稿" })).toBeDisabled();
  vi.mocked(creativeTypesApi.get).mockResolvedValue({
    ...original,
    revision: 4,
  });
  fireEvent.click(screen.getByText("读取最新修订，保留我的编辑"));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "保存草稿" })).toBeEnabled(),
  );
  expect(screen.getByLabelText("类型名称")).toHaveValue("我的本地修改");
  mutate.mockResolvedValue({
    ...original,
    revision: 5,
    draft: { ...original.draft, name: "我的本地修改" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() =>
    expect(mutate).toHaveBeenLastCalledWith(
      original.id,
      "draft",
      4,
      expect.any(String),
      expect.objectContaining({ name: "我的本地修改" }),
    ),
  );
});

test("unchanged or reverted form has no save actions; successful activation shows confirmation", async () => {
  const original = typeRecord();
  const user = userEvent.setup();
  vi.spyOn(creativeTypesApi, "mutate").mockImplementation(async (_id, _action, _revision, _op, profile) => ({
    ...original, revision: 1, draft: profile!,
  }));
  render(<MemoryRouter><CreativeTypes /></MemoryRouter>);
  const summary = await screen.findByLabelText("一句话说明");
  expect(screen.queryByRole("button", { name: "保存并启用 ↗" })).not.toBeInTheDocument();
  fireEvent.change(summary, { target: { value: "新说明" } });
  expect(screen.getByRole("button", { name: "保存并启用 ↗" })).toBeVisible();
  fireEvent.change(summary, { target: { value: original.draft.summary } });
  expect(screen.queryByRole("button", { name: "保存并启用 ↗" })).not.toBeInTheDocument();
  fireEvent.change(summary, { target: { value: "更新说明" } });
  await user.click(screen.getByRole("button", { name: "保存并启用 ↗" }));
  expect(await screen.findByRole("dialog", { name: "已保存" })).toHaveTextContent("资料已保存并启用");
  expect(screen.queryByRole("button", { name: "保存并启用 ↗" })).not.toBeInTheDocument();
});

test("delete requires confirmation, retries the same request, and clears the final selected type", async () => {
  const original = typeRecord();
  const user = userEvent.setup();
  const mutate = vi.spyOn(creativeTypesApi, "mutate").mockRejectedValueOnce(new Error("网络错误"));
  render(<MemoryRouter><CreativeTypes /></MemoryRouter>);
  await screen.findByLabelText("类型名称");
  await user.click(screen.getByRole("button", { name: "删除类型" }));
  expect(screen.getByRole("dialog", { name: "删除类型" })).toHaveTextContent("已有讨论和历史版本会保留");
  await user.click(screen.getByRole("button", { name: "取消" }));
  expect(mutate).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "删除类型" }));
  await user.click(screen.getByRole("button", { name: "确认删除" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("网络错误");
  expect(screen.getByLabelText("类型名称")).toHaveValue(original.draft.name);
  mutate.mockResolvedValue({ ...original, status: "disabled", revision: 1, deleted_at: "now" });
  vi.mocked(creativeTypesApi.list).mockResolvedValue(typeLibrary([]));
  await user.click(screen.getByRole("button", { name: "删除类型" }));
  await user.click(screen.getByRole("button", { name: "确认删除" }));
  await screen.findByText("选择或新建一个类型，开始维护写作说明。");
  expect(screen.queryByLabelText("类型名称")).not.toBeInTheDocument();
  expect(mutate.mock.calls[0]).toEqual(mutate.mock.calls[1]);
  expect(mutate.mock.calls[1]).toEqual([original.id, "delete", 0, expect.any(String), undefined]);
});

test("delete conflict preserves edits and does not silently switch records", async () => {
  const original = typeRecord();
  const user = userEvent.setup();
  vi.spyOn(creativeTypesApi, "mutate").mockRejectedValue(new CreativeApiError("资料已被修改", 409));
  render(<MemoryRouter><CreativeTypes /></MemoryRouter>);
  fireEvent.change(await screen.findByLabelText("一句话说明"), { target: { value: "保留编辑" } });
  await user.click(screen.getByRole("button", { name: "删除类型" }));
  expect(screen.getByRole("dialog", { name: "删除类型" })).toHaveTextContent("当前未保存的修改将被放弃");
  await user.click(screen.getByRole("button", { name: "确认删除" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("资料已被修改");
  expect(screen.getByLabelText("类型名称")).toHaveValue(original.draft.name);
  expect(screen.getByLabelText("一句话说明")).toHaveValue("保留编辑");
});

test("network retry reuses operation identity and history restores only a draft", async () => {
  const original = typeRecord();
  const mutate = vi
    .spyOn(creativeTypesApi, "mutate")
    .mockRejectedValueOnce(new Error("network lost"))
    .mockResolvedValue({
      ...original,
      revision: 1,
      draft: { ...original.draft, summary: "本地草稿" },
    });
  render(
    <MemoryRouter>
      <CreativeTypes />
    </MemoryRouter>,
  );
  fireEvent.change(await screen.findByLabelText("一句话说明"), {
    target: { value: "本地草稿" },
  });
  fireEvent.click(screen.getByText("保存草稿"));
  await screen.findByText("network lost");
  fireEvent.click(screen.getByText("保存草稿"));
  await waitFor(() => expect(mutate).toHaveBeenCalledTimes(2));
  expect(mutate.mock.calls[0][3]).toBe(mutate.mock.calls[1][3]);
  await waitFor(() => expect(screen.queryByText("保存草稿")).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "知道了" }));
  fireEvent.click(screen.getByRole("tab", { name: "模型预览与版本" }));
  fireEvent.click(screen.getByText("恢复为草稿"));
  fireEvent.click(screen.getByRole("tab", { name: "定位与分类" }));
  expect(screen.getByLabelText("一句话说明")).toHaveValue(
    original.draft.summary,
  );
  expect(mutate).toHaveBeenCalledTimes(2);
});

test("picker loads enabled references, preserves custom labels, and never changes selection on load", async () => {
  const hidden = {
    ...typeRecord("ct_disabled", "停用主题"),
    status: "disabled" as const,
  };
  vi.mocked(creativeTypesApi.list).mockResolvedValue(
    typeLibrary([
      typeRecord(),
      typeRecord("ct_fantasy", "奇幻", ["Fantasy"]),
      hidden,
    ]),
  );
  const change = vi.fn();
  render(
    <MemoryRouter>
      <CreativeTypePicker
        selected={["Fantasy", "我的主题"]}
        onChange={change}
        other=""
        onOtherChange={vi.fn()}
      />
    </MemoryRouter>,
  );
  const marriage = await screen.findByRole("button", { name: "婚姻背叛" });
  expect(change).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("button", { name: "停用主题" }),
  ).not.toBeInTheDocument();
  fireEvent.click(marriage);
  expect(change).toHaveBeenCalledWith(
    ["Fantasy", "我的主题", "婚姻背叛"],
    [
      expect.objectContaining({ profile_id: "ct_fantasy" }),
      expect.objectContaining({
        profile_id: "ct_marriage_betrayal",
        role: "unspecified",
      }),
    ],
  );
  fireEvent.change(screen.getByLabelText("奇幻在本书的作用"), {
    target: { value: "primary" },
  });
  expect(change).toHaveBeenLastCalledWith(
    ["Fantasy", "我的主题"],
    [expect.objectContaining({ profile_id: "ct_fantasy", role: "primary" })],
  );
});

test("ambiguous aliases need an explicit reference, and explicit references never hot-update", async () => {
  const first = typeRecord("ct_first", "第一类", ["Fantasy"]),
    second = typeRecord("ct_second", "第二类", ["Fantasy"]);
  vi.mocked(creativeTypesApi.list).mockResolvedValue(
    typeLibrary([first, second]),
  );
  await expect(bindCreativeSelection({ genres: ["Fantasy"] })).rejects.toThrow(
    "对应多个类型",
  );
  const explicit = {
    genres: ["Fantasy"],
    creative_types: [creativeRef(typeSummary(first))],
  };
  await expect(bindCreativeSelection(explicit)).resolves.toEqual(explicit);
  expect(creativeTypesApi.list).toHaveBeenCalledTimes(1);
});

test("saved-session version update is reviewed, explicit, idempotent after lost response, and does not run LLM", async () => {
  const original = typeRecord(),
    newer = {
      ...original,
      revision: 1,
      active_version: 2,
      versions: [
        ...original.versions,
        {
          profile: { ...original.draft, version: 2, summary: "新版" },
          sha256: "b".repeat(64),
          created_at: "now",
        },
      ],
    };
  const snapshot = {
    schema: "book-type-selection.v1",
    snapshot_sha256: "hash",
    profiles: [
      {
        profile: original.draft,
        source_sha256: "a".repeat(64),
        role: "primary" as const,
      },
    ],
  };
  const context: SessionTypeContext = {
    snapshot,
    contexts: {},
    context_sha256: {},
    latest: [typeSummary(newer)],
    revision: 3,
    last_request: null,
  };
  vi.spyOn(creativeTypesApi, "sessionContext").mockResolvedValue(context);
  vi.mocked(creativeTypesApi.get).mockResolvedValue(newer);
  const session = { id: "session-1", revision: 3 } as WorkshopSession;
  const update = vi.fn();
  const adopt = vi
    .spyOn(creativeTypesApi, "adoptSession")
    .mockRejectedValueOnce(new Error("network lost"))
    .mockResolvedValue({ ...session, revision: 4 });
  render(
    <WorkshopTypeSnapshot
      session={session}
      disabled={false}
      onUpdate={update}
    />,
  );
  fireEvent.click(screen.getByText("本讨论使用的类型说明与版本"));
  fireEvent.click(await screen.findByText(/有 1 份新版说明/));
  await screen.findByText("采用前请核对变化");
  expect(adopt).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("采用这些新版说明"));
  await screen.findByText("network lost");
  fireEvent.click(screen.getByText("采用这些新版说明"));
  await waitFor(() =>
    expect(update).toHaveBeenCalledWith(
      expect.objectContaining({ revision: 4 }),
    ),
  );
  expect(adopt.mock.calls[0]).toEqual(adopt.mock.calls[1]);
  expect(adopt.mock.calls[0][2]).toEqual([
    expect.objectContaining({
      profile_id: original.id,
      version: 2,
      role: "primary",
    }),
  ]);
});
