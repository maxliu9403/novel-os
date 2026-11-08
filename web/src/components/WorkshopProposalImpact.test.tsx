import { render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import WorkshopStoryWorkspace from "./WorkshopStoryWorkspace";
import WorkshopProposalImpact from "./WorkshopProposalImpact";
import { storySession } from "../test/workshopStoryFixture";

it("shows the actual tag mechanism inside the selectable story direction without selecting it", () => {
  const session = storySession();
  const proposal = session.creative_design!.story_state!.pending!.proposals[0];
  proposal.tag_contributions = [{ tag_id: "tag.revenge", tag_name: "复仇", contribution: "她以撤回合约作为反击，迫使旧盟友公开承担代价。" }];
  proposal.choice = "拒绝借新关系报复旧关系";
  proposal.cost = "失去眼前庇护";
  proposal.payoff = "赢得可自由离开的新关系";
  render(<WorkshopStoryWorkspace session={session} tab="decision" onTab={vi.fn()} mode="" selected={[]} disabled={false}
    onSelect={vi.fn()} onMode={vi.fn()} onReopen={vi.fn()} latestReply={null} />);
  const choice = screen.getByText(proposal.title).closest("label")!;
  expect(within(choice).getByText("复仇")).toBeVisible();
  expect(within(choice).getByText(proposal.tag_contributions[0].contribution)).toBeVisible();
  expect(within(choice).getByText(proposal.cost)).toBeVisible();
  expect(within(choice).getByRole("radio")).not.toBeChecked();
  expect(screen.queryByText("tag.revenge")).not.toBeInTheDocument();
});

it("marks incomplete combinations without hiding the useful proposal or exposing internal IDs", () => {
  const proposal = storySession().creative_design!.story_state!.pending!.proposals[0];
  proposal.combination_status = "partial";
  proposal.missing_contributions = ["tag.revenge"];
  proposal.missing_contribution_names = ["复仇"];
  proposal.choice = "保留自己的退出权";
  render(<WorkshopProposalImpact proposal={proposal} />);
  expect(screen.getByText("组合说明待补全")).toBeVisible();
  expect(screen.getByText("复仇：待说明")).toBeVisible();
  expect(screen.getByText("保留自己的退出权")).toBeVisible();
  expect(screen.queryByText("tag.revenge")).not.toBeInTheDocument();
});
