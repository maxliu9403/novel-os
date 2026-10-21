import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { repairTaskFixture } from "../test/coverRepairFixtures";
import CoverRepairTaskPanel, { CoverRepairStartDialog } from "./CoverRepairTaskPanel";
import type { CoverCandidate } from "../api/client";

afterEach(cleanup);
const props = {checking:false, error:"", syncedAt:Date.now(), submitting:false, disabled:false};

test("saved commercial advice stays optional and does not disable confirmed single-image approval", async () => {
  const base = repairTaskFixture();
  const task = {...base, proposal: {...base.proposal!, commercial_review: {policy_version:"cover-commercial-review.v1", advisories:[
    {category:"composition_craft", concept_id:"concept-1", message:"可进一步减少背景细节。", evidence_refs:["evidence:source-1"]},
  ]}}};
  const approve=vi.fn();const user=userEvent.setup();
  render(<CoverRepairTaskPanel {...props} task={task} onApprove={approve} />);
  expect(screen.getByText("可选优化建议")).toBeInTheDocument();
  await user.click(screen.getByText("可选优化建议"));
  expect(screen.getByText("可进一步减少背景细节。")).toBeVisible();
  expect(screen.getByText(/不影响批准/)).toBeVisible();
  await user.click(screen.getByRole("checkbox"));
  const button=screen.getByRole("button",{name:"批准并生成这 1 张"});expect(button).toBeEnabled();
  await user.click(button);expect(approve).toHaveBeenCalledOnce();
});

test("legacy repair proposals do not gain commercial notices or optional advice", () => {
  render(<CoverRepairTaskPanel {...props} task={repairTaskFixture()} onApprove={vi.fn()} />);
  expect(screen.queryByText("可选优化建议")).not.toBeInTheDocument();
  expect(screen.queryByText(/不必逐幕复现/)).not.toBeInTheDocument();
});

test("approval requires explicit confirmation and a changed proposal clears it", async () => {
  const approve=vi.fn();const task=repairTaskFixture();const user=userEvent.setup();
  const {rerender}=render(<CoverRepairTaskPanel {...props} task={task} onApprove={approve} />);
  const button=screen.getByRole("button",{name:"批准并生成这 1 张"});expect(button).toBeDisabled();
  expect(approve).not.toHaveBeenCalled();
  await user.click(screen.getByRole("checkbox"));await user.click(button);expect(approve).toHaveBeenCalledTimes(1);
  rerender(<CoverRepairTaskPanel {...props} task={{...task,proposal_sha256:"new-proposal"}} onApprove={approve} />);
  expect(screen.getByRole("checkbox")).not.toBeChecked();expect(screen.getByRole("button",{name:"批准并生成这 1 张"})).toBeDisabled();
});

test("zero-image review states the actual calls without promising image repair", () => {
  render(<CoverRepairTaskPanel {...props} task={{...repairTaskFixture(),kind:"evaluation",image_calls:0}} onApprove={vi.fn()} />);
  expect(screen.getByText("确认只处理这一张并重新审核，新增图片调用为 0 次。")).toBeInTheDocument();
  expect(screen.getByRole("button",{name:"确认执行 · 不重新出图"})).toBeDisabled();
});

test("completed blocked repair never says it passed and preserves failure context while offline", () => {
  render(<CoverRepairTaskPanel {...props} task={{...repairTaskFixture("completed"),result_quality_status:"blocked",error:"saved review failed"}}
    checking error="offline" onApprove={vi.fn()} />);
  expect(screen.getByText(/本次结果仍有质量问题/)).toBeInTheDocument();
  expect(screen.getByText(/修复已完成，正在同步结果/)).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("saved review failed");
});

test("interrupted task explains preserved original and no paid replay", () => {
  render(<CoverRepairTaskPanel {...props} task={repairTaskFixture("interrupted")} onApprove={vi.fn()} />);
  expect(screen.getByRole("heading",{name:"单张修复已中断"})).toBeInTheDocument();
  expect(screen.getByText(/不会自动重试或追加出图/)).toBeInTheDocument();
  expect(screen.queryByRole("button",{name:"批准并生成这 1 张"})).not.toBeInTheDocument();
});

const original: CoverCandidate = {candidate_id:"candidate-1", concept_id:"concept-1", status:"ready", url:"/cover.png",
  relative_path:"cover.png", media_id:"image", sha256:"cover-sha", width:1024, height:1536, content_type:"image/png", error:"",
  quality_report:{status:"recommended_for_human_review", blockers:[], repair_codes:[], evidence:[], assets:{artwork_url:"/artwork.png"}}};

test("refinement dialog requires personal instructions and starts only explicit planning with the reference", async () => {
  const start=vi.fn();const user=userEvent.setup();
  render(<CoverRepairStartDialog candidate={original} number={2} busy={false} error="" onClose={vi.fn()} onStart={start} mode="refinement" />);
  expect(screen.getByRole("img",{name:"候选图 2 的精修参考图片"})).toHaveAttribute("src",expect.stringContaining("/artwork.png"));
  const button=screen.getByRole("button",{name:"制定精修方案"});expect(button).toBeDisabled();
  const field=screen.getByRole("textbox",{name:"优化要求"});expect(field).toHaveAttribute("maxLength","2000");
  await user.type(field,"  ");expect(button).toBeDisabled();expect(start).not.toHaveBeenCalled();
  await user.type(field,"保留人物，让光线更柔和。  ");
  await user.click(button);expect(start).toHaveBeenCalledExactlyOnceWith("保留人物，让光线更柔和。");
  expect(screen.getByText(/原图、其他候选和已选交付封面会保留/)).toBeInTheDocument();
});

test("restored refinement displays saved instructions and requires one image edit approval", async () => {
  const approve=vi.fn();const user=userEvent.setup();
  const task={...repairTaskFixture(), kind:"refinement" as const, refinement_prompt:"保留人物\n减少背景杂物"};
  render(<CoverRepairTaskPanel {...props} original={original} task={task} onApprove={approve} />);
  expect(screen.getByRole("region",{name:"单张封面精修任务"})).toHaveTextContent("保留人物 减少背景杂物");
  expect(screen.getByRole("img",{name:"候选图 1 的精修前图片"})).toHaveAttribute("src",expect.stringContaining("/artwork.png"));
  const button=screen.getByRole("button",{name:"批准并精修这 1 张"});expect(button).toBeDisabled();
  expect(approve).not.toHaveBeenCalled();
  await user.click(screen.getByRole("checkbox",{name:/1 次图片编辑调用/}));
  await user.click(button);expect(approve).toHaveBeenCalledTimes(1);
});

test("refinement planning preserves prompt while offline and offers no implicit retry", () => {
  render(<CoverRepairTaskPanel {...props} error="offline" checking task={{...repairTaskFixture("planning"),kind:"refinement",refinement_prompt:"让神态更坚定"}} onApprove={vi.fn()} />);
  expect(screen.getByRole("heading",{name:"单张精修状态暂未确认"})).toBeInTheDocument();
  expect(screen.getByText("让神态更坚定")).toBeInTheDocument();
  expect(screen.getByText(/不会自动重复调用模型/)).toBeInTheDocument();
  expect(screen.queryByRole("button",{name:/批准/})).not.toBeInTheDocument();
});
