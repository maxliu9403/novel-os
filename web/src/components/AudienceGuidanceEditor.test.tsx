import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import AudienceGuidanceEditor from "./AudienceGuidanceEditor";
import { creativeTypesApi } from "../api/creativeTypes";
import { typeRecord } from "../test/creativeTypesFixture";

afterEach(() => vi.restoreAllMocks());

it("requires a guide for every enabled channel, saves only module fields, and keeps disabled guidance", async () => {
  const record = typeRecord();
  const onSaved = vi.fn();
  const save = vi.spyOn(creativeTypesApi, "audience").mockResolvedValue(record);
  const user = userEvent.setup();
  render(<AudienceGuidanceEditor record={record} onSaved={onSaved} onClose={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "保存并启用" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: "男频" }));
  expect(screen.getByRole("button", { name: "保存并启用" })).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox", { name: "男频写作指导 · 必填" }), { target: { value: "将能力与承诺变成有风险的选择。" } });
  await user.click(screen.getByRole("checkbox", { name: "女频" }));
  await user.click(screen.getByRole("button", { name: "保存并启用" }));
  expect(save).toHaveBeenCalledWith(record.id, 0, expect.any(String), ["male"], {
    female: "让关系、主体性与代价进入具体人物选择。", male: "将能力与承诺变成有风险的选择。",
  });
  expect(save.mock.calls[0]).not.toContain(record.draft);
  expect(onSaved).toHaveBeenCalledWith(record);
});

it("retains unsaved author guidance and operation identity after failure", async () => {
  const save = vi.spyOn(creativeTypesApi, "audience").mockRejectedValue(new Error("版本已更新，请重新读取"));
  const user = userEvent.setup();
  render(<AudienceGuidanceEditor record={typeRecord()} onSaved={vi.fn()} onClose={vi.fn()} />);
  fireEvent.change(screen.getByRole("textbox", { name: "女频写作指导 · 必填" }), { target: { value: "新的完整要求" } });
  await user.click(screen.getByRole("button", { name: "保存并启用" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("版本已更新");
  expect(screen.getByRole("textbox", { name: "女频写作指导 · 必填" })).toHaveValue("新的完整要求");
  await user.click(screen.getByRole("button", { name: "保存并启用" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
  expect(save.mock.calls[0]).toEqual(save.mock.calls[1]);
});

it("does not mark an unchanged guide dirty after restoring channel membership or clearing an unused field", async () => {
  const record = typeRecord();
  record.document!.audience_guidance!.audience_channels = ["male", "female"];
  record.document!.audience_guidance!.guides.male = "另一份写作指导";
  render(<AudienceGuidanceEditor record={record} onSaved={vi.fn()} onClose={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("checkbox", { name: "女频" }));
  expect(screen.getByRole("button", { name: "保存并启用" })).toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: "女频" }));
  expect(screen.queryByRole("button", { name: "保存并启用" })).not.toBeInTheDocument();
});
