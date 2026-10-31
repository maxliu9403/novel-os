import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { WorkshopDesignContext, WorkshopDesignRoundView } from "./WorkshopDesign";
import { workshopDesign } from "../test/workshopDesignFixture";

test("keeps the next focus visible and offers optional author choices with genre consequences", async () => {
  const design = workshopDesign();
  const onDecide = vi.fn().mockResolvedValue(true);
  const user = userEvent.setup();
  render(<><WorkshopDesignContext design={design} /><WorkshopDesignRoundView round={design.rounds[0]} design={design} disabled={false} onDecide={onDecide} /></>);
  expect(screen.getByText("下一步建议 · 旧敌为何愿意结盟")).toBeVisible();
  expect(screen.getByText("庇护的交换")).not.toBeVisible();
  await user.click(screen.getByText(/题材与创作决定/));
  expect(screen.getByText("狼人 · 专项指导")).toBeVisible();
  expect(screen.getByText("原创类型 · 通用指导")).toBeVisible();
  await user.click(screen.getByText(/本轮方案与取舍/));
  await user.click(screen.getByText("题材如何推动这个故事"));
  expect(screen.getByText("高潮的权力交换不成立")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "采用这条" }));
  expect(onDecide).toHaveBeenCalledWith("r1:p1", "accepted");
});

test("revision preserves an unsaved note on failure and requires an explicit nonblank edit", async () => {
  const design = workshopDesign();
  const onDecide = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
  const user = userEvent.setup();
  render(<WorkshopDesignRoundView round={design.rounds[0]} design={design} disabled={false} onDecide={onDecide} />);
  await user.click(screen.getByText(/本轮方案与取舍/));
  await user.click(screen.getByRole("button", { name: "修改方案" }));
  expect(screen.getByRole("button", { name: "保存修改意见" })).toBeDisabled();
  await user.type(screen.getByLabelText("写下要采用的修改内容"), "盟友改为姐姐");
  await user.click(screen.getByRole("button", { name: "保存修改意见" }));
  expect(screen.getByLabelText("写下要采用的修改内容")).toHaveValue("盟友改为姐姐");
  await user.click(screen.getByRole("button", { name: "保存修改意见" }));
  expect(onDecide).toHaveBeenLastCalledWith("r1:p1", "revised", "盟友改为姐姐");
  expect(screen.queryByLabelText("写下要采用的修改内容")).not.toBeInTheDocument();
});

test("restores latest decision and disables mutations during background inference", async () => {
  const design = workshopDesign();
  design.decisions = [
    { proposal_id: "r1:p1", status: "accepted", note: "", source: "author_action", revision: 3, message_index: 2, created_at: "now" },
    { proposal_id: "r1:p1", status: "rejected", note: "换路线", source: "author_action", revision: 4, message_index: 3, created_at: "later" },
  ];
  render(<WorkshopDesignRoundView round={design.rounds[0]} design={design} disabled onDecide={vi.fn()} />);
  await userEvent.click(screen.getByText(/本轮方案与取舍/));
  expect(screen.getByRole("status")).toHaveTextContent("不采用");
  for (const button of screen.getAllByRole("button")) expect(button).toBeDisabled();
});

test("malformed companion notes show a nonblocking fallback without selection controls", () => {
  const design = workshopDesign();
  render(<WorkshopDesignRoundView round={{ source_revision: 1, message_index: 0, status: "unavailable", proposals: [], notice: "讨论原文已保留" }} design={design} disabled={false} onDecide={vi.fn()} />);
  expect(screen.getByText("讨论原文已保留")).toBeVisible();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
