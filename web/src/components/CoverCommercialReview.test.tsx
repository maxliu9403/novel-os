import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";
import CoverCommercialReview from "./CoverCommercialReview";

afterEach(cleanup);
const concepts=[{concept_id:"a", label:"方向 1"}, {concept_id:"b", label:"方向 2"}];
const advice={category:"composition_craft", concept_id:"a", message:"让背景更简洁。", evidence_refs:["evidence:a"]};
const review={policy_version:"cover-commercial-review.v1", advisories:[advice]};

test("saved advice is collapsed, labels its concept and retains citations without execution controls", async () => {
  const user=userEvent.setup();render(<CoverCommercialReview review={review} concepts={concepts} commercial />);
  expect(screen.getByText("1 条")).toBeVisible();
  expect(screen.getByText("让背景更简洁。")).not.toBeVisible();
  await user.click(screen.getByText("可选优化建议"));
  expect(screen.getByText("让背景更简洁。")).toBeVisible();
  expect(screen.getByText("方向 1 · 构图与细节")).toBeVisible();
  await user.click(screen.getByText("故事证据引用"));
  expect(screen.getByText("evidence:a")).toBeVisible();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

test.each([undefined, null, {}, {advisories:[advice]}, {...review,policy_version:"legacy"},
  {...review,advisories:[{...advice,category:"material_story_contradiction"}]},
  {...review,advisories:[{...advice,concept_id:"unknown"}]},
  {...review,advisories:[{...advice,evidence_refs:[]}]}, {...review,advisories:[{...advice,message:null}]}])(
  "legacy or unclassified records do not gain optional advice notices (%j)", value => {
    const {container}=render(<CoverCommercialReview review={value} concepts={concepts} />);
    expect(container).toBeEmptyDOMElement();
  },
);

test("a single repair shows only its target and portfolio advice", () => {
  render(<CoverCommercialReview concepts={concepts} conceptId="a" review={{...review,advisories:[
    advice, {...advice,concept_id:"b",message:"其它候选建议"}, {...advice,concept_id:"portfolio",message:"整组取舍"},
  ]}} />);
  expect(screen.getByText("2 条")).toBeVisible();
  expect(screen.queryByText("其它候选建议")).not.toBeInTheDocument();
  expect(screen.getByText("整组取舍")).toBeInTheDocument();
});

test("new policy explanation remains concise with no empty advice panel", () => {
  render(<CoverCommercialReview commercial concepts={concepts} review={{...review,advisories:[]}} />);
  expect(screen.getByText(/不必逐幕复现/)).toBeVisible();
  expect(screen.queryByText("可选优化建议")).not.toBeInTheDocument();
});
