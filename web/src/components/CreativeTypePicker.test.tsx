import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import CreativeTypePicker from "./CreativeTypePicker";
import { ConfirmCtx } from "./confirmContext";
import { creativeTypesApi, creativeRef, validCreativeInput, type AudienceChannel, type CreativeTypeRef } from "../api/creativeTypes";
import { typeLibrary, typeRecord } from "../test/creativeTypesFixture";
vi.mock("../api/creativeTypes", async (original) => ({
  ...(await original<typeof import("../api/creativeTypes")>()), creativeTypesApi: { list: vi.fn() },
}));

function renderPicker(library = typeLibrary(), initial: number[] = [], audience: AudienceChannel | null = "female", confirm = vi.fn().mockResolvedValue(true), legacy = false) {
  vi.mocked(creativeTypesApi.list).mockResolvedValue(library);
  function Form() {
    const [refs, setRefs] = useState<CreativeTypeRef[]>(initial.map(i => creativeRef(library.items[i])));
    const [channel, setChannel] = useState<AudienceChannel | null>(audience);
    return <><CreativeTypePicker refs={refs} channel={channel} legacy={legacy && !channel} onChange={(_, next, selectedChannel) => { setRefs(next); setChannel(selectedChannel ?? null); }} />
      <output data-testid="selection">{channel}:{refs.map(r => r.id).join(",")}</output></>;
  }
  render(<MemoryRouter><ConfirmCtx.Provider value={confirm}><Form /></ConfirmCtx.Provider></MemoryRouter>);
  return { user: userEvent.setup(), confirm };
}

it("does not silently choose a channel, exposes only ready guidance, and preserves tags when Genre changes", async () => {
  const library = typeLibrary([typeRecord(), typeRecord("genre.mystery", "悬疑"), typeRecord("tag.betrayal", "背叛"), typeRecord("tag.incomplete", "未维护")]);
  library.items[3].audience_ready = false;
  const { user } = renderPicker(library, [], null);
  expect(screen.queryByRole("radio", { name: "Romance" })).not.toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("radio", { name: "女频" })).toBeEnabled());
  await user.click(screen.getByRole("radio", { name: "女频" }));
  await user.click(screen.getByRole("radio", { name: "Romance" }));
  await user.click(screen.getByRole("button", { name: "背叛" }));
  await user.click(screen.getByRole("radio", { name: "悬疑" }));
  expect(screen.getByTestId("selection")).toHaveTextContent("female:genre.mystery,tag.betrayal");
  expect(screen.queryByRole("button", { name: "未维护" })).not.toBeInTheDocument();
});

it("requires shared confirmation for channel removals and preserves compatible choices", async () => {
  const library = typeLibrary([typeRecord(), typeRecord("tag.betrayal", "背叛"), typeRecord("tag.shared", "共享")]);
  library.items[0].audience_channels = ["female", "male"];
  library.items[2].audience_channels = ["female", "male"];
  const confirm = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
  const { user } = renderPicker(library, [0, 1, 2], "female", confirm);
  await waitFor(() => expect(screen.getByRole("radio", { name: "男频" })).toBeEnabled());
  await user.click(screen.getByRole("radio", { name: "男频" }));
  expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringContaining("背叛"), confirmLabel: "切换频道" }));
  expect(screen.getByTestId("selection")).toHaveTextContent("female:genre.romance,tag.betrayal,tag.shared");
  await user.click(screen.getByRole("radio", { name: "男频" }));
  await waitFor(() => expect(screen.getByTestId("selection")).toHaveTextContent("male:genre.romance,tag.shared"));
});

it("limits new selections to two Tags without disabling their removal", async () => {
  const library = typeLibrary([typeRecord(), typeRecord("tag.one", "一"), typeRecord("tag.two", "二"), typeRecord("tag.three", "三")]);
  const { user } = renderPicker(library, [0]);
  await user.click(await screen.findByRole("button", { name: "一" }));
  await user.click(screen.getByRole("button", { name: "二" }));
  expect(screen.getByRole("button", { name: "三" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "一" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "一" }));
  expect(screen.getByRole("button", { name: "三" })).toBeEnabled();
});

it("guards new input channel and tag count while retaining legacy input validation", () => {
  const base = { title: "", author: "", genres: [], premise: "Story", language: "English", audience: "", market: "", constraints: "", creative_types: typeLibrary().items.slice(0, 2).map(creativeRef) };
  expect(validCreativeInput({ ...base, creation_flow: "genre_tags.v2" })).toBe(false);
  expect(validCreativeInput({ ...base, creation_flow: "genre_tags.v2", audience_channel: "male" })).toBe(true);
  expect(validCreativeInput({ ...base, creation_flow: "genre_tags.v1" })).toBe(true);
});


it("keeps legacy choices read-only until a channel is selected and asks the author to reduce excess Tags", async () => {
  const library = typeLibrary([typeRecord(), typeRecord("tag.one", "一"), typeRecord("tag.two", "二"), typeRecord("tag.three", "三")]);
  const { user } = renderPicker(library, [0, 1, 2, 3], null, vi.fn().mockResolvedValue(true), true);
  await waitFor(() => expect(screen.getByRole("radio", { name: "女频" })).toBeEnabled());
  expect(screen.queryByRole("radio", { name: "Romance" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "一" })).not.toBeInTheDocument();
  expect(screen.getByTestId("selection")).toHaveTextContent("genre.romance,tag.one,tag.two,tag.three");
  await user.click(screen.getByRole("radio", { name: "女频" }));
  expect(screen.getByText("保留了 3 个原有 Tags，请取消至一至两个后继续。")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "三" }));
  expect(screen.getByTestId("selection")).toHaveTextContent("female:genre.romance,tag.one,tag.two");
});

it("keeps saved choices intact when library loading fails and allows a read-only retry", async () => {
  const library = typeLibrary();
  vi.mocked(creativeTypesApi.list).mockRejectedValueOnce(new Error("资料暂不可用")).mockResolvedValue(library);
  const onChange = vi.fn();
  render(<MemoryRouter><CreativeTypePicker refs={library.items.slice(0, 2).map(creativeRef)} channel="female" onChange={onChange} /></MemoryRouter>);
  expect(await screen.findByRole("alert")).toHaveTextContent("资料暂不可用");
  expect(screen.getByRole("radio", { name: "男频" })).toBeDisabled();
  expect(onChange).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "重新读取资料" }));
  await waitFor(() => expect(screen.getByRole("radio", { name: "男频" })).toBeEnabled());
  expect(onChange).not.toHaveBeenCalled();
});

it("binds current guide versions when switching a restored v2 selection and allows removing unavailable saved Tags", async () => {
  const library = typeLibrary();
  library.items.forEach(item => { item.audience_channels = ["female", "male"]; item.active_version = 2; item.source_sha256 = "b".repeat(64); });
  library.items[1].status = "disabled";
  vi.mocked(creativeTypesApi.list).mockResolvedValue(library);
  const refs = library.items.slice(0, 2).map(item => ({ id: item.id, version: 1, source_sha256: "a".repeat(64) }));
  const onChange = vi.fn();
  const confirm = vi.fn().mockResolvedValue(true);
  render(<MemoryRouter><ConfirmCtx.Provider value={confirm}><CreativeTypePicker refs={refs} channel="female" onChange={onChange} /></ConfirmCtx.Provider></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole("radio", { name: "男频" })).toBeEnabled());
  await userEvent.click(screen.getByText("背叛", { selector: "summary" }));
  await userEvent.click(screen.getByRole("button", { name: "移除背叛" }));
  expect(onChange).toHaveBeenLastCalledWith(["Romance"], [refs[0]], "female");
  await userEvent.click(screen.getByRole("radio", { name: "男频" }));
  expect(onChange).toHaveBeenLastCalledWith(["Romance"], [creativeRef(library.items[0])], "male");
});

it("renders h5_guided profile receipts read-only and requires explicit confirmation before replacing their schema", async () => {
  const refs = [{ profile_id: "ct_werewolf", version: 4, source_sha256: "a".repeat(64), role: "unspecified" },
    { profile_id: "ct_female_awakening", version: 2, source_sha256: "b".repeat(64), role: "unspecified" }];
  vi.mocked(creativeTypesApi.list).mockResolvedValue(typeLibrary());
  const onChange = vi.fn();
  const confirm = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
  render(<MemoryRouter><ConfirmCtx.Provider value={confirm}><CreativeTypePicker refs={refs} legacy legacyNames={["奇幻", "狼人", "女性觉醒"]} onChange={onChange} /></ConfirmCtx.Provider></MemoryRouter>);
  expect(screen.getByText("历史创作资料 · 只读")).toBeInTheDocument();
  expect(screen.getByText("奇幻 · 狼人 · 女性觉醒")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("radio", { name: "女频" })).toBeEnabled());
  expect(onChange).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("radio", { name: "女频" }));
  expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ confirmLabel: "选择新版题材" }));
  expect(onChange).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("radio", { name: "女频" }));
  expect(onChange).toHaveBeenCalledWith([], [], "female");
  expect(refs[0].profile_id).toBe("ct_werewolf");
});
