import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { CoverCandidate, CoverConcept } from "../api/client";
import CoverQualityDetails from "./CoverQualityDetails";

afterEach(cleanup);
const concept: CoverConcept = {
  concept_id: "one", visual_strategy: "threshold", focal_scene: "An interrupted departure",
  composition: "scene", palette: "blue", secondary_signal: "key", title_treatment: "top",
  scene_plan: { _compiled_recipe: {}, _generation_authorization: { extra_image_attempts: 0, image_attempts_reserved: 1 } },
};
function candidate(extra: Partial<CoverCandidate> = {}): CoverCandidate {
  return { candidate_id: "candidate-1", concept_id: "one", status: "ready", url: "/media/final", relative_path: "", media_id: "final", sha256: "final-sha", width: 200, height: 300, content_type: "image/jpeg", error: "", ...extra };
}

test("separates independent observations from verified story and unknown scores", async () => {
  render(<CoverQualityDetails candidate={candidate({ quality_report: {
    status: "human_review_required", blockers: [], repair_codes: [], evidence: [],
    core_conflict_fidelity: 81, causal_relationship_clarity: null,
    evaluation: { evaluation_status: "incomplete", blindness_level: "artwork",
      blind_read: { relationships: ["A person blocks the doorway"], actions: ["Another person reaches for the latch"], stakes: [], anomalies: [], open_questions: ["Will the person leave?"], uncertainty: [] },
      story_assessment: { conflict: "The departure agrees with the approved refusal" },
      craft_assessment: { hierarchy: "The hand and face remain clear" }, uncertainty: ["The rear figure is difficult to read"] },
    repair_decision: { kind: "evaluation" },
  } })} concept={concept} />);
  await userEvent.click(screen.getByText("查看故事与画面评估"));
  expect(screen.getByText("A · 未知故事时看到什么")).toBeInTheDocument();
  expect(screen.getByText("A person blocks the doorway")).toBeInTheDocument();
  expect(screen.getByText("B · 对照故事后的核验")).toBeInTheDocument();
  expect(screen.getByText("The departure agrees with the approved refusal")).toBeInTheDocument();
  expect(screen.getByText("81/100")).toBeInTheDocument();
  expect(screen.getAllByText("未评估").length).toBeGreaterThan(0);
  expect(screen.queryByText("0/100")).not.toBeInTheDocument();
  expect(screen.getByText("The rear figure is difficult to read")).toBeInTheDocument();
  expect(screen.getByText("不会自动追加出图。已批准额外出图 0 次，当前剩余 0 次。")).toBeInTheDocument();
});

test("reevaluation acts only on the existing image callback and reports no independent result honestly", async () => {
  const reevaluate = vi.fn();
  render(<CoverQualityDetails candidate={candidate({ quality_report: {
    status: "human_review_required", blockers: [], repair_codes: [], evidence: [],
    evaluation: { evaluation_status: "unavailable", blindness_level: "unavailable" },
    repair_decision: { kind: "evaluation" },
  } })} concept={concept} onReevaluate={reevaluate} />);
  expect(screen.getByText("评估暂不可用")).toBeInTheDocument();
  await userEvent.click(screen.getByText("重新评估已有图片"));
  expect(reevaluate).toHaveBeenCalledOnce();
  await userEvent.click(screen.getByText("查看故事与画面评估"));
  expect(screen.getByText("未取得独立观察，不能用故事梗概代替观众的理解。")).toBeInTheDocument();
});

test("shows contaminated blind read, design escalation, artwork and immutable attempt comparison", async () => {
  render(<CoverQualityDetails candidate={candidate({ quality_report: {
    status: "blocked", blockers: ["core_conflict_missing"], repair_codes: ["core_conflict_missing"], evidence: [],
    evaluation: { evaluation_status: "complete", blindness_level: "contaminated" },
    repair_decision: { kind: "design" }, assets: { artwork_url: "/media/artwork" },
    human_acceptance: { reason: "The original scene best matches the story" },
  }, attempt_history: [
    { attempt_id: "a", image_sha256: "original-sha", status: "ready", url: "/media/original", quality_report: { status: "blocked" } },
    { attempt_id: "b", image_sha256: "final-sha", status: "ready", url: "/media/final" },
  ] })} concept={concept} />);
  expect(screen.getByText("当前问题需要修订美术方向，重新审批后再出图。")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "查看无标题画稿" })).toHaveAttribute("href", expect.stringContaining("/media/artwork"));
  await userEvent.click(screen.getByText("查看故事与画面评估"));
  expect(screen.getByText("盲读受到画面文字影响")).toBeInTheDocument();
  await userEvent.click(screen.getByText("生成与修复对比（2 次）"));
  expect(screen.getByRole("link", { name: "查看初次生成图片" })).toHaveAttribute("href", expect.stringContaining("/media/original"));
  expect(screen.getByText("修复 1 · 当前")).toBeInTheDocument();
  expect(screen.getByText("人工接受理由：The original scene best matches the story")).toBeInTheDocument();
});

test("pixel similarity warnings never imply a semantic diversity score", () => {
  render(<CoverQualityDetails candidate={candidate({ quality_report: {
    status: "blocked", blockers: ["duplicate_artwork"], repair_codes: [], evidence: [],
    portfolio_comparison: { status: "complete", basis: "artwork", matches: [{ kind: "exact", pair: ["candidate-1", "candidate-2"] }] },
  } })} concept={concept} />);
  expect(screen.getByText("与同批候选图存在完全相同的画稿，请检查是否重复。")).toBeInTheDocument();
  expect(screen.getByText("此项只比较图像相似度，不代表故事差异或商业吸引力评分。")).toBeInTheDocument();
});

test("retained artwork can retry local title composition after failure without offering image regeneration", async () => {
  const review = vi.fn();
  render(<CoverQualityDetails candidate={candidate({ status: "failed", url: null, quality_report: {
    status: "blocked", blockers: ["title_layout_failure"], repair_codes: [], evidence: [],
    repair_decision: { kind: "typography" },
    evaluation: { evaluation_status: "not_run", blindness_level: "unavailable" },
  } })} concept={concept} onReevaluate={review} />);
  await userEvent.click(screen.getByRole("button", { name: "重新合成标题并评估" }));
  expect(review).toHaveBeenCalledOnce();
  expect(screen.queryByRole("button", { name: "修复" })).not.toBeInTheDocument();
});

// Synthetic records preserve the latest four-candidate shape: strong craft,
// shared semantic blockers, and one candidate with a separate layer failure.
test.each([1, 2, 3, 4])("candidate %s exposes distinct reasons and missing evidence without hiding blind-read uncertainty", async index => {
  const blockers = ["core_conflict_missing", ...(index === 2 ? ["composite_layer_conflation"] : ["protagonist_action_missing"]), "causal_relationship_missing"];
  const findings = [
    {code: "core_conflict_missing", severity: "blocker", message: "The scene suggests discussion without a clear decision.", evidence: "Both figures attend to the same object."},
    ...(index === 2 ? [{code: "composite_layer_conflation", severity: "blocker", message: "Separate layers read as one room.", evidence: "The surface continues across the layer boundary."}]
      : [{code: "protagonist_action_missing", severity: "blocker", message: "The intended response is ambiguous.", evidence: "The hand is resting rather than moving away."}]),
    {code: "photographic_craft", severity: "info", message: "The photographic finish is credible.", evidence: "Natural skin and motivated light."},
  ];
  render(<CoverQualityDetails candidate={candidate({quality_report: {
    status: "blocked", blockers, repair_codes: [...blockers, "thumbnail_action_loss"], evidence: [], findings,
    medium_fidelity: 95, photorealism: 91, causal_relationship_clarity: 46,
    evaluation: {evaluation_status: "complete", blindness_level: index === 3 ? "artwork" : "unverified"},
    repair_decision: {kind: "design", codes: blockers},
  }})} concept={concept} />);
  const summary = screen.getByRole("region", {name: "主要评估问题"});
  expect(within(summary).getByText("核心冲突不清晰")).toBeVisible();
  expect(within(summary).getByText(index === 2 ? "组合图层关系不清晰" : "主角行动不清晰")).toBeVisible();
  expect(within(summary).getByText("因果关系不清晰")).toBeVisible();
  expect(within(summary).getAllByRole("listitem")).toHaveLength(3);
  expect(within(summary).getByText("故事表达")).toBeVisible();
  expect(within(summary).getByText(index === 2 ? "构图" : "动作")).toBeVisible();
  expect(within(summary).getByText("因果")).toBeVisible();
  expect(screen.queryByText("故事或构图需要调整。请回到美术方向，修改后重新审批。")).not.toBeInTheDocument();
  expect(screen.getByRole("region", {name: "评估依据可靠性"})).toHaveTextContent(index === 3 ? "已独立观察无标题画稿" : "无法确认盲读未受文字影响");
  await userEvent.click(screen.getByText("查看故事与画面评估"));
  const evidence = screen.getByRole("region", {name: "具体证据与问题"});
  expect(within(evidence).getByText("因果关系不清晰")).toBeVisible();
  expect(within(evidence).getAllByText("未提供对应的单独视觉证据，请结合已有观察复核。").length).toBeGreaterThan(0);
  expect(screen.getByText("不会自动追加出图。已批准额外出图 0 次，当前剩余 0 次。")).toBeInTheDocument();
});

test("retains decision-only text issues and unknown codes alongside existing findings", async () => {
  render(<CoverQualityDetails candidate={candidate({ quality_report: {
    status: "blocked", blockers: [], repair_codes: [], evidence: ["A general observation without a code."],
    findings: [{ code: "photographic_craft", severity: "info", message: "The lighting is natural.", evidence: "Soft light falls from one window." }],
    repair_decision: { kind: "typography", codes: ["title_failure", "provider_specific_issue", "title_failure"] },
    evaluation: { evaluation_status: "complete", blindness_level: "partial" },
  } })} concept={concept} />);
  const summary = screen.getByRole("region", { name: "主要评估问题" });
  expect(within(summary).getAllByRole("listitem")).toHaveLength(2);
  expect(within(summary).getByText("文字")).toBeVisible();
  expect(within(summary).getByText("标题存在问题")).toBeVisible();
  expect(within(summary).getByText("provider_specific_issue")).toBeVisible();
  expect(within(summary).queryByText("构图")).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "评估依据可靠性" })).toHaveTextContent("部分盲读：标题遮罩会遮住部分画面");
  await userEvent.click(screen.getByText("查看故事与画面评估"));
  const evidence = screen.getByRole("region", { name: "具体证据与问题" });
  expect(within(evidence).getByText("标题存在问题")).toBeVisible();
  expect(within(evidence).getByText("provider_specific_issue")).toBeVisible();
  expect(within(evidence).getAllByText("未提供对应的单独视觉证据，请结合已有观察复核。")).toHaveLength(2);
  expect(within(evidence).getByText("A general observation without a code.")).toBeVisible();
});

test("positive observations alone do not become a failure summary", () => {
  render(<CoverQualityDetails candidate={candidate({ quality_report: {
    status: "recommended_for_human_review", blockers: [], repair_codes: [], evidence: [],
    findings: [{ code: "photographic_craft", severity: "info", message: "The lighting is natural.", evidence: "Soft light falls from one window." }],
    repair_decision: { kind: "none", codes: [] },
    evaluation: { evaluation_status: "complete", blindness_level: "artwork" },
  } })} concept={concept} />);
  expect(screen.queryByRole("region", { name: "主要评估问题" })).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "评估依据可靠性" })).toHaveTextContent("已独立观察无标题画稿");
  expect(screen.getByText("评估已完成")).toBeVisible();
});
