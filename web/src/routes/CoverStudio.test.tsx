import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { ToastProvider } from "../components/Toaster";
import { ConfirmProvider } from "../components/Confirm";
import CoverStudio from "./CoverStudio";
import { api, ApiError, type CoverDirection, type CoverDirectionTask, type CoverGenerationTask, type CoverSet } from "../api/client";
import { workshopApi } from "../api/workshop";
import { repairTaskFixture } from "../test/coverRepairFixtures";


const project = {
  id: "cover-book", title: "The Door Is Mine", genre: "Domestic revenge",
  author: "A. Writer", chapter_count: 12, status: "in_progress", style: {},
};

const coverStatus = {
  configured: true, has_api_key: true, base_url: "https://sub2api.example/v1",
  model: "gpt-image-2", size: "2048x3072", quality: "high" as const,
  output_format: "jpeg" as const, count: 4, timeout_seconds: 180,
  inherits_base_url: false, inherits_api_key: false, error: null,
};

function coverSet(status: CoverSet["status"] = "partial"): CoverSet {
  const states = status === "selected"
    ? ["selected", "ready", "ready", "rejected"] as const
    : ["ready", "failed", "ready", "pending"] as const;
  return {
    cover_set_id: "cover-abc", project_id: project.id, status,
    requested_count: 4, selected_candidate_id: status === "selected" ? "candidate-1" : "",
    revision: 5, active_revision: status === "selected" ? 1 : 0,
    source_prompt_sha256: "a".repeat(64), foundation_sha256: "",
    created_at: "2026-08-29T10:00:00Z", updated_at: "2026-08-29T10:05:00Z",
    brief: {
      title: project.title, target_audience: "women 30-50",
      core_conflict: "Two households demand her labor.",
      decisive_story_node: "Her name appears alone on the deed.",
    },
    concepts: states.map((_, index) => ({
      concept_id: `concept-${index + 1}`,
      visual_strategy: ["Confrontation", "Decisive node", "Evidence", "Pressure"][index],
      focal_scene: `Scene ${index + 1}`, composition: "Portrait", palette: "Crimson",
      secondary_signal: "House key", title_treatment: "Title at top",
      generation_prompt: "redacted in UI fixture",
    })),
    candidates: states.map((candidateStatus, index) => ({
      candidate_id: `candidate-${index + 1}`, concept_id: `concept-${index + 1}`,
      status: candidateStatus, url: candidateStatus === "pending" || candidateStatus === "failed"
        ? null : `/api/projects/${project.id}/media/media-${index + 1}/raw`,
      relative_path: candidateStatus === "pending" || candidateStatus === "failed"
        ? "" : `outputs/deliverables/covers/pending/cover-0${index + 1}.jpg`,
      media_id: candidateStatus === "pending" || candidateStatus === "failed" ? "" : `media-${index + 1}`,
      sha256: candidateStatus === "pending" || candidateStatus === "failed" ? "" : "b".repeat(64),
      width: candidateStatus === "pending" || candidateStatus === "failed" ? 0 : 2048,
      height: candidateStatus === "pending" || candidateStatus === "failed" ? 0 : 3072,
      content_type: candidateStatus === "pending" || candidateStatus === "failed" ? "" : "image/jpeg",
      error: candidateStatus === "failed" ? "Provider timeout" : "",
    })),
  };
}

function NavigationProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return <><output aria-label="当前地址">{location.pathname + location.search}</output><button onClick={() => navigate(-1)}>返回上一子页</button></>;
}

function renderStudio(scope: "project" | "workshop" = "project", query = "") {
  if (!vi.isMockFunction(api.coverSource)) vi.spyOn(api, "coverSource").mockResolvedValue({
    selector: {source_type: "project_input", project_id: project.id},
    source_content_sha256: "a".repeat(64), source_bundle_sha256: "b".repeat(64),
    origin: {relative_path: "outputs/input/prompt.md"}, documents: [], analyses: [],
  });
  if (!vi.isMockFunction(api.coverDirections)) {
    vi.spyOn(api, "coverDirections").mockResolvedValue([]);
  }
  // Creating a direction first reads cover facts; these scenarios have no
  // missing facts. Preserve explicit mocks for tests of the confirmation flow.
  if (!vi.isMockFunction(api.coverStoryFacts)) {
    vi.spyOn(api, "coverStoryFacts").mockResolvedValue({
      brief: coverDirection().brief!, pending_fields: [], revision_sha256: "fixture",
    });
  }
  const entry = scope === "project" ? `/projects/${project.id}/covers` : "/workshop/cover-session/covers";
  return render(
    <MemoryRouter initialEntries={[entry + query]}>
      <NavigationProbe />
      <ToastProvider><ConfirmProvider>
        <Link to="/elsewhere">离开工作室</Link>
        <Routes>
          <Route path="/projects/:id/covers" element={<CoverStudio />} />
          <Route path="/workshop/:sessionId/covers" element={<CoverStudio />} />
          <Route path="/elsewhere" element={<Link to={entry}>返回工作室</Link>} />
        </Routes>
      </ConfirmProvider></ToastProvider>
    </MemoryRouter>,
  );
}

function coverDirection(status: CoverDirection["status"] = "awaiting_approval"): CoverDirection {
  const treatments = [
    ["intimate_character_window", "asymmetric_close_plane", "private_reckoning", "lived-in apartment entry", "intimate_editorial_portrait", "wounded_recognition", "airy_literary_serif"],
    ["relationship_geometry", "triangular_depth_tableau", "causal_ensemble", "lived-in apartment entry", "deep_focus_prestige_drama", "divided_loyalty", "fractured_editorial_serif"],
    ["evidence_mystery", "evidence_led_negative_space", "evidence_discovery", "lived-in apartment entry", "graphic_editorial_suspense", "shock_and_dread", "condensed_evidence_lockup"],
    ["kinetic_threshold", "diagonal_threshold_motion", "threshold_departure", "lived-in apartment entry", "kinetic_cinematic_key_art", "cathartic_resolve", "bold_cinematic_serif"],
  ];
  return {
    direction_id: "direction-" + "a".repeat(32),
    schema_version: 1,
    director_model: "fixture-director",
    profile_version: "cover-profiles.v5",
    brief_sha256: "a".repeat(64),
    direction_sha256: "b".repeat(64),
    status,
    visual_assumptions: [],
    visual_identity: {
      design_thesis: "Turn the shared doorway into a measure of who still belongs.",
      dominant_emotional_contradiction: "domestic warmth against irreversible separation",
      story_signatures: ["shared brass key", "child backpack by the door"],
      visual_grammar: ["threshold geometry", "handled domestic evidence"],
      material_language: ["worn brass", "painted wood"],
      palette_logic: "warm amber interrupted by cool blue",
      lighting_logic: "ordinary hall light reveals the decisive object",
      spatial_logic: "access and distance measure belonging",
      typography_voice: "doorway-like verticals with one controlled break",
      cast_policy: "Use people only when their action is stronger than their trace.",
      cliche_blacklist: ["large crying face", "foreground victim with background lovers"],
      uniqueness_anchors: ["key leaving the ring", "backpack marking the threshold"],
      spoiler_boundary: ["do not reveal the final relationship outcome"],
    },
    evidence_ledger: {
      schema_version: 1,
      source_bundle_sha256: "c".repeat(64),
      source_files: { "outputs/input/prompt.md": "d".repeat(64) },
      items: [{
        evidence_id: "ev-door", source_type: "publication_intro",
        source_ref: "outputs/publication/publication-copy.json:hook_lead",
        summary: "Can one removed key protect a child without closing every future door?",
        story_function: "spoiler-safe opening hook", spoiler_level: "safe",
        visual_tags: ["reader hook"],
      }],
    },
    core_conflict_visual_contract: {
      protagonist_character_id: "char_mara",
      conflict_kind: "interpersonal boundary rupture",
      pressure_source: "Her estranged spouse still expects access after withdrawing from the family.",
      pressure_character_ids: ["char_oren"],
      relationship_stakes: "Mara and her child's safe belonging in the home",
      visible_cause: "His attention avoids her while his hand remains extended for the key",
      decisive_consequence: "Mara removes the key and closes the threshold",
      required_visual_signals: ["his expected access", "her active boundary"],
      evidence_refs: ["node:door_choice"],
      spoiler_boundary: "Do not reveal the final relationship outcome",
    },
    brief: {
      schema_version: 2,
      title: project.title,
      genre: "family ethics",
      target_audience: "adult relationship-drama readers",
      principal_characters: [{
        character_id: "char_mara", name: "Mara", age: 34,
        occupation_and_status: "caregiver returning to paid work",
        lived_environment: "a lived-in apartment entry",
      }],
      lived_environment: { primary_spaces: ["lived-in apartment entry"] },
    },
    plans: Array.from({ length: 4 }, (_, index) => ({
      concept_id: `concept-${index + 1}`,
      visual_strategy: `strategy_${index + 1}`,
      portfolio_slot: treatments[index][0],
      composition_family: treatments[index][1],
      scene_family: treatments[index][2],
      location_family: treatments[index][3],
      art_style: treatments[index][4],
      emotion_register: treatments[index][5],
      typography_style: treatments[index][6],
      focal_strategy: index === 0 ? "character_led" : index === 1 ? "object_led" : "environment_led",
      design_rationale: "This hypothesis turns a different layer of the doorway choice into visual form.",
      evidence_summary: "The key and backpack are approved recurring story evidence.",
      typography_rationale: "The title rhythm echoes access and separation.",
      novelty_rationale: "The subject, topology, and title behavior differ from the other plans.",
      visual_signature: "One removed key changes the shape of the home.",
      causal_visibility: index < 3 ? "direct" : "indirect",
      conflict_delivery: `distinct conflict language ${index + 1}`,
      conflict_read: "A woman closes access after her spouse withdraws from the family.",
      cause_signal: "His visible expectation of access",
      consequence_signal: "Her hand removes the shared key",
      conflict_character_ids: index < 3 ? ["char_oren"] : [],
      protagonist_action_visible: index < 3,
      cast: ["char_mara"], focal_character_id: "char_mara",
      frozen_action: "Mara removes the shared key before the door closes",
      blocking: "Mara foreground right at the threshold",
      primary_prop: "shared brass key",
      visual_hook: {
        hook_type: "irreversible_moment",
        first_glance_subject: "Mara and the key",
        open_question: "Will she close the door?",
        reader_promise: "she chooses her boundary",
        expected_thumbnail_read: "one woman, one key, one decision",
      },
    })),
  };
}

function directionTask(status: CoverDirectionTask["status"], directionId = coverDirection().direction_id): CoverDirectionTask {
  return {task_id: "direction-task-1", status, analysis_id: "analysis-complete", count: 4,
    freshness_policy: "require_current_head", direction_id: status === "completed" ? directionId : null,
    error: null, created_at: "2026-10-11T14:00:00Z", updated_at: "2026-10-11T14:00:01Z", finished_at: null};
}

function mockCreatedDirection(direction: CoverDirection) {
  return vi.spyOn(api, "startCoverDirectionTask").mockImplementation(async () => {
    vi.mocked(api.coverDirections).mockResolvedValue([direction]);
    vi.mocked(api.coverDirectionTask).mockResolvedValue(directionTask("completed", direction.direction_id));
    return directionTask("completed", direction.direction_id);
  });
}

function generationTask(status: CoverGenerationTask["status"] = "running", setId: string | null = "cover-new"): CoverGenerationTask {
  return {task_id: "generation-a", job_id: "job-generate", status, direction_id: "direction-1", cover_set_id: setId,
    count: 4, result_status: status === "completed" ? "ready" : null, error: null,
    created_at: "2026-10-11T10:00:00Z", started_at: "2026-10-11T10:00:01Z", updated_at: "2026-10-11T10:02:00Z",
    finished_at: status === "completed" ? "2026-10-11T10:02:00Z" : null,
    progress: {phase: "generating", total: 4, completed: 1, ready: 1, failed: 0,
      current_candidate_id: "candidate-2", current_candidate_index: 2,
      phase_started_at: "2026-10-11T10:01:00Z", last_event_at: "2026-10-11T10:02:00Z"}};
}
function mockCompletedGeneration(set: CoverSet) {
  return vi.spyOn(api, "generateCovers").mockImplementation(async () => {
    vi.mocked(api.coverGenerationTask).mockResolvedValue({...generationTask("completed", set.cover_set_id), count: set.requested_count});
    return {job_id: "job-generate", task_id: "generation-a", kind: "cover.generate", status: "done", error: null};
  });
}

beforeEach(() => {
  vi.spyOn(api, "coverRepairTask").mockResolvedValue(null);
  vi.spyOn(api, "coverDirectionTask").mockResolvedValue(null);
  vi.spyOn(api, "coverGenerationTask").mockResolvedValue(null);
});

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("keeps four 2:3 slots stable while the workspace loads", () => {
  vi.spyOn(api, "project").mockReturnValue(new Promise(() => {}));
  vi.spyOn(api, "studioCover").mockReturnValue(new Promise(() => {}));
  vi.spyOn(api, "covers").mockReturnValue(new Promise(() => {}));

  renderStudio();

  expect(screen.getAllByTestId("cover-slot")).toHaveLength(4);
  expect(screen.getByText("正在加载封面工作区")).toBeInTheDocument();
});

test("routes an unconfigured workspace to independent cover settings", async () => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue({
    ...coverStatus, configured: false, has_api_key: false, error: "Add a cover API key.",
  });
  vi.spyOn(api, "covers").mockResolvedValue([]);

  renderStudio();

  expect(await screen.findByRole("heading", { name: "封面工作室" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "配置封面模型" })).toHaveAttribute("href", "/settings");
  expect(screen.getByRole("button", { name: "生成 4 张封面" })).toBeDisabled();
});

test.each(["gpt-image-2", "codex-image-auto"])("labels Codex automatic selection without presenting %s as a verified model", async model => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue({...coverStatus, model, model_capabilities: {
    exact_model_selection: false, default_model: "codex-image-auto", model_verification: "unavailable",
    note: "Codex chooses its image tool model.",
  }});
  vi.spyOn(api, "covers").mockResolvedValue([]);
  renderStudio();
  expect(await screen.findByText("Codex · 自动选择图片模型")).toBeInTheDocument();
  expect(screen.queryByText(model, {exact: true})).not.toBeInTheDocument();
});

test("shows story facts and requires exact art direction approval before generation", async () => {
  const pending = coverDirection();
  const approved = coverDirection("approved");
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([pending]);
  const approve = vi.spyOn(api, "approveCoverDirection").mockResolvedValue(approved);
  const user = userEvent.setup();

  renderStudio();

  expect(await screen.findByText("美术方向审核")).toBeInTheDocument();
  expect(screen.getByText("年龄 34 · caregiver returning to paid work")).toBeInTheDocument();
  expect(screen.getByText("a lived-in apartment entry")).toBeInTheDocument();
  expect(screen.getByText("Asymmetric close plane")).toBeInTheDocument();
  expect(screen.getByText("Graphic editorial suspense")).toBeInTheDocument();
  expect(screen.getByText("Cathartic resolve")).toBeInTheDocument();
  expect(screen.getByText("Bold cinematic serif")).toBeInTheDocument();
  expect(screen.getByText("本书视觉语言")).toBeInTheDocument();
  expect(screen.getByText("核心冲突视觉契约")).toBeInTheDocument();
  expect(screen.getAllByText(/缩略图故事：A woman closes access/)).toHaveLength(4);
  expect(screen.getByText("Turn the shared doorway into a measure of who still belongs.")).toBeInTheDocument();
  expect(screen.getByText("1 条故事证据 · 1 类来源")).toBeInTheDocument();
  expect(screen.getAllByText(/This hypothesis turns a different layer/)).toHaveLength(4);
  expect(screen.getByRole("button", { name: "生成 4 张封面" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "批准美术方向" }));
  await waitFor(() => expect(approve).toHaveBeenCalledWith(
    project.id, pending.direction_id, pending.brief_sha256, pending.direction_sha256,
  ));
  expect(screen.getByRole("button", { name: "生成 4 张封面" })).toBeEnabled();
});

test("commercial art direction displays saved optional advice without blocking approval", async () => {
  const pending: CoverDirection = {...coverDirection(), profile_version:"cover-profiles.v11", source_binding:{
    snapshot_id:"snapshot-a", analysis_id:"analysis-a", source_content_sha256:"a".repeat(64), freshness_policy:"require_current_head",
    campaign_evidence_review:{policy_version:"cover-commercial-review.v1", advisories:[
      {category:"visual_ambiguity", concept_id:"concept-1", message:"可以强化主角与背景的视觉区分。", evidence_refs:["evidence:ev-1"]},
    ]},
  }};
  vi.spyOn(api,"project").mockResolvedValue(project);
  vi.spyOn(api,"studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api,"covers").mockResolvedValue([]);
  vi.spyOn(api,"coverDirections").mockResolvedValue([pending]);
  const start = vi.spyOn(api,"startCoverDirectionTask");
  const user=userEvent.setup();renderStudio("project","?tab=directions");
  expect(await screen.findByText(/不必逐幕复现/)).toBeVisible();
  await user.click(screen.getByText("可选优化建议"));
  expect(screen.getByText("可以强化主角与背景的视觉区分。")).toBeVisible();
  expect(screen.getByText(/不影响批准/)).toBeVisible();
  expect(screen.getByRole("button",{name:"批准美术方向"})).toBeEnabled();
  expect(start).not.toHaveBeenCalled();
});

test("creates art direction from the complete skeleton analysis before generation", async () => {
  const pending = coverDirection();
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([]);
  const create = mockCreatedDirection(pending);
  vi.spyOn(api, "coverSource").mockResolvedValue({
    selector: {source_type: "project_input", project_id: project.id},
    source_content_sha256: "a".repeat(64), source_bundle_sha256: "b".repeat(64),
    origin: {relative_path: "outputs/input/prompt.md"}, documents: [],
    analyses: [{analysis_id: "analysis-complete", snapshot_id: "snapshot-1", coverage_status: "complete"}],
  });
  vi.spyOn(api, "coverAnalysis").mockResolvedValue({analysis_id: "analysis-complete", snapshot_id: "snapshot-1", coverage_status: "complete"});
  const user = userEvent.setup();

  renderStudio();

  await screen.findByRole("heading", { name: "封面工作室" });
  expect(screen.getByRole("button", { name: "生成 4 张封面" })).toBeDisabled();
  await user.selectOptions(await screen.findByLabelText("已保存的骨架分析"), "analysis-complete");
  await screen.findByText("已完成全段分析与全局核查");
  await user.click(screen.getByRole("button", { name: "创建美术方向" }));

  await waitFor(() => expect(create).toHaveBeenCalledWith(project.id, 4, "analysis-complete", "require_current_head"));
  expect(await screen.findByText("美术方向审核")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批准美术方向" })).toBeEnabled();
});

test("keeps art direction creation busy after leaving and reopening the studio", async () => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([]);
  vi.spyOn(api, "coverSource").mockResolvedValue({
    selector: {source_type: "project_input", project_id: project.id},
    source_content_sha256: "a".repeat(64), source_bundle_sha256: "b".repeat(64),
    origin: {relative_path: "outputs/input/prompt.md"}, documents: [],
    analyses: [{analysis_id: "analysis-complete", snapshot_id: "snapshot-1", coverage_status: "complete"}],
  });
  vi.spyOn(api, "coverAnalysis").mockResolvedValue({
    analysis_id: "analysis-complete", snapshot_id: "snapshot-1", coverage_status: "complete",
  });
  const create = vi.spyOn(api, "startCoverDirectionTask").mockImplementation(async () => {
    vi.mocked(api.coverDirectionTask).mockResolvedValue(directionTask("running"));
    return directionTask("running");
  });
  const user = userEvent.setup();
  const firstVisit = renderStudio();
  await screen.findByText("已完成全段分析与全局核查");
  await user.click(screen.getByRole("button", {name: "创建美术方向"}));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("button", {name: "创建美术方向"})).toBeDisabled();

  firstVisit.unmount();
  renderStudio();

  await screen.findByText("已完成全段分析与全局核查");
  expect(await screen.findByText("美术方向正在后台规划")).toBeInTheDocument();
  expect(screen.getByRole("button", {name: "创建美术方向"})).toBeDisabled();
  expect(create).toHaveBeenCalledTimes(1);
});

test.each(["project", "workshop"] as const)("%s restores a running direction across navigation and loads its completed result on return", async scope => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(workshopApi, "get").mockResolvedValue({
    id: "cover-session", revision: 1, status: "ready", input: {
      title: project.title, author: project.author, genres: [project.genre],
      premise: "A woman claims her home", language: "English", audience: "Fiction readers", market: "unspecified", constraints: "",
    }, messages: [], prompt_md: "# Skeleton", questions: [], ready_for_confirmation: true,
    last_error: null, job_id: null, skill_version: "fixture", prepared: null, project_id: null, logs: [],
  });
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  const directions = vi.spyOn(api, "coverDirections").mockResolvedValue([]);
  vi.mocked(api.coverDirectionTask).mockResolvedValue(directionTask("running"));
  const create = vi.spyOn(api, "startCoverDirectionTask");
  const user = userEvent.setup();
  renderStudio(scope);
  await screen.findByText("美术方向正在后台规划");
  await user.click(screen.getByRole("link", {name: "离开工作室"}));
  await user.click(screen.getByRole("link", {name: "返回工作室"}));
  await screen.findByText("美术方向正在后台规划");
  expect(screen.getByRole("button", {name: "创建美术方向"})).toBeDisabled();
  await user.click(screen.getByRole("link", {name: "离开工作室"}));
  directions.mockResolvedValue([coverDirection()]);
  vi.mocked(api.coverDirectionTask).mockResolvedValue(directionTask("completed"));
  await user.click(screen.getByRole("link", {name: "返回工作室"}));
  await waitFor(() => expect(screen.getByRole("button", {name: "批准美术方向"})).toBeEnabled());
  expect(screen.getByText("美术方向已生成")).toBeInTheDocument();
  expect(create).not.toHaveBeenCalled();
  expect(api.coverDirectionTask).toHaveBeenLastCalledWith(scope === "project" ? project.id : "workshop:cover-session");
});

test.each([false, true])("keeps running direction status by the main actions before the source panel with previous direction=%s", async previous => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue(previous ? [coverSet()] : []);
  vi.spyOn(api, "coverDirections").mockResolvedValue(previous ? [coverDirection("approved")] : []);
  vi.mocked(api.coverDirectionTask).mockResolvedValue(directionTask("running"));
  const start = vi.spyOn(api, "startCoverDirectionTask");
  renderStudio();
  const status = await screen.findByText("美术方向正在后台规划");
  await userEvent.setup().click(screen.getByRole("tab", {name: /故事分析/}));
  const source = screen.getByRole("region", { name: "骨架来源与故事理解" });
  expect(status).toBeVisible();
  expect(status.compareDocumentPosition(source) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByRole("button", { name: "生成 4 张封面" })).toBeDisabled();
  expect(start).not.toHaveBeenCalled();
});

test("shows true direction elapsed time from creation and updates without announcing every tick", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-11T14:11:15Z"));
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.mocked(api.coverDirectionTask).mockResolvedValue(directionTask("running"));
  renderStudio();
  await act(async () => {});
  const elapsed = screen.getByText("已用时 11 分 15 秒");
  expect(elapsed.closest('[role="status"], [aria-live="polite"], [aria-live="assertive"]')).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByText("已用时 11 分 16 秒")).toBeVisible();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
});

test.each(["completed", "failed", "interrupted"] as const)("freezes %s direction elapsed time at the recorded finish", async status => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-11T14:11:15Z"));
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.mocked(api.coverDirectionTask).mockResolvedValue({...directionTask(status), finished_at: "2026-10-11T14:05:07Z"});
  renderStudio();
  await act(async () => {});
  expect(screen.getByText("总用时 5 分 07 秒")).toBeVisible();
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.getByText("总用时 5 分 07 秒")).toBeVisible();
});

test("labels queued work separately and prioritizes lost status connection over a stale running report", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-11T14:11:15Z"));
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.mocked(api.coverDirectionTask).mockResolvedValueOnce(directionTask("queued"))
    .mockResolvedValueOnce(directionTask("running")).mockRejectedValue(new Error("offline"));
  renderStudio();
  await act(async () => {});
  expect(screen.getByText("美术方向已排队")).toBeVisible();
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(screen.getByText("美术方向正在后台规划")).toBeVisible();
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(screen.getByText("正在恢复状态连接")).toBeVisible();
  expect(screen.queryByText("美术方向正在后台规划")).not.toBeInTheDocument();
  expect(screen.getByText(/上次确认：运行中/)).toBeVisible();
  expect(screen.getByText("正在同步美术方向状态，请稍候")).toBeVisible();
  expect(screen.getByRole("button", { name: "生成 4 张封面" })).toBeDisabled();
});

test("does not claim a healthy running task when its next status response hangs", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-11T14:11:15Z"));
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.mocked(api.coverDirectionTask).mockResolvedValueOnce(directionTask("running"))
    .mockReturnValue(new Promise(() => {}));
  const start = vi.spyOn(api, "startCoverDirectionTask");
  renderStudio();
  await act(async () => {});
  expect(screen.getByText("美术方向正在后台规划")).toBeVisible();
  await act(async () => { await vi.advanceTimersByTimeAsync(16000); });
  expect(screen.getByText("状态同步较慢")).toBeVisible();
  expect(screen.getByText(/上次确认：运行中/)).toBeVisible();
  expect(screen.getByText("本次任务已等待 11 分 31 秒")).toBeVisible();
  expect(screen.queryByText("美术方向正在后台规划")).not.toBeInTheDocument();
  expect(screen.queryByText(/正在自动重试/)).not.toBeInTheDocument();
  expect(start).not.toHaveBeenCalled();
});

test("does not overwrite a completed direction with an older initial list response", async () => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  let resolveInitial!: (directions: CoverDirection[]) => void;
  vi.spyOn(api, "coverDirections").mockReturnValueOnce(new Promise(resolve => { resolveInitial = resolve; }))
    .mockResolvedValue([coverDirection()]);
  vi.mocked(api.coverDirectionTask).mockResolvedValue(directionTask("completed"));
  renderStudio();
  await waitFor(() => expect(screen.getByRole("button", {name: "批准美术方向"})).toBeEnabled());
  await act(async () => { resolveInitial([coverDirection("approved")]); });
  expect(screen.getByRole("button", {name: "批准美术方向"})).toBeEnabled();
  expect(screen.getByRole("button", {name: "生成 4 张封面"})).toBeDisabled();
});

test("lets operations replace an approved legacy direction with a new portfolio", async () => {
  const approved = coverDirection("approved");
  const replanned = coverDirection();
  replanned.direction_id = `direction-${"d".repeat(32)}`;
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([approved]);
  const create = mockCreatedDirection(replanned);
  const user = userEvent.setup();

  renderStudio();

  await user.click(await screen.findByRole("button", { name: "重新规划封面方向" }));
  await waitFor(() => expect(create).toHaveBeenCalledWith(project.id, 4, "", "require_current_head"));
  await waitFor(() => expect(screen.getByRole("button", { name: "批准美术方向" })).toBeEnabled());
  expect(screen.getByRole("button", { name: "生成 4 张封面" })).toBeDisabled();
});

test("does not reuse an older approval when the newest direction awaits review", async () => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([
    coverDirection("awaiting_approval"),
    { ...coverDirection("approved"), direction_id: `direction-${"c".repeat(32)}` },
  ]);

  renderStudio();

  expect(await screen.findByText("美术方向审核")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "生成 4 张封面" })).toBeDisabled();
});

test("shows partial results and retries only the failed candidate", async () => {
  const partial = coverSet();
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([partial]);
  const retry = vi.spyOn(api, "retryCover").mockResolvedValue({
    job_id: "job-retry", kind: "cover.retry", status: "done", error: null, meta: {},
  });

  renderStudio();
  const user = userEvent.setup();

  expect(await screen.findByText("Provider timeout")).toBeInTheDocument();
  expect(screen.getAllByTestId("cover-slot")).toHaveLength(4);
  expect(screen.getByRole("link", { name: "查看候选图 1原始尺寸" })).toHaveAttribute(
    "href", expect.stringContaining("/media/media-1/raw"),
  );
  await user.click(screen.getByRole("button", { name: "重试候选图 2" }));
  await waitFor(() => expect(retry).toHaveBeenCalledWith(
    project.id, partial.cover_set_id, "candidate-2", partial.revision,
  ));
});

test("offers only reported quality repair codes for a ready candidate", async () => {
  const ready = coverSet("ready");
  const reviewed = {
    ...ready,
    candidates: ready.candidates.map((candidate, index) => index === 0 ? {
      ...candidate,
      quality_report: {
        status: "blocked" as const,
        blockers: ["generic_ai_face"],
        repair_codes: ["generic_ai_face"],
        evidence: ["face region"],
      },
    } : candidate),
  };
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([reviewed]);
  const retry = vi.spyOn(api, "retryCover").mockResolvedValue({
    job_id: "job-repair", kind: "cover.retry", status: "done", error: null, meta: {},
  });
  const user = userEvent.setup();

  renderStudio();

  expect(await screen.findByText("发现质量阻塞问题")).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "候选图 1的修复原因" })).toHaveValue("generic_ai_face");
  await user.click(screen.getByRole("button", { name: "按修复建议重新生成候选图 1" }));
  await waitFor(() => expect(retry).toHaveBeenCalledWith(
    project.id, reviewed.cover_set_id, "candidate-1", reviewed.revision, ["generic_ai_face"],
  ));
});

test("requires confirmation before selection and exposes the delivery package", async () => {
  const ready = coverSet("ready");
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([ready]);
  const select = vi.spyOn(api, "selectCover").mockResolvedValue(coverSet("selected"));
  const user = userEvent.setup();

  renderStudio();
  await screen.findByRole("heading", { name: "封面工作室" });
  await user.click(screen.getByRole("button", { name: "选择候选图 1" }));

  expect(select).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog", { name: "使用这张封面？" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "使用此封面" }));
  await waitFor(() => expect(select).toHaveBeenCalledWith(
    project.id, ready.cover_set_id, "candidate-1", ready.revision, ready.active_revision, false,
  ));
  expect(screen.getByRole("link", { name: "下载交付包" })).toHaveAttribute(
    "href", expect.stringContaining("/deliverables/package"),
  );
});

test.each(["blocked", "human_review_required"] as const)("completed %s covers are selectable without a reason while other failed candidates remain unselectable", async status => {
  const ready = coverSet("ready");
  ready.concepts[0].scene_plan = {_compiled_recipe: {recipe_sha256: "approved"},
    _generation_authorization: {extra_image_attempts: 0, image_attempts_reserved: 1}};
  ready.candidates[0].quality_report = {status, blockers: ["core_conflict_missing"],
    repair_codes: ["core_conflict_missing"], evidence: ["The opposing character is absent"],
    evaluation: {evaluation_status: "complete", blindness_level: "artwork"},
    repair_decision: {kind: "design", action: "new_direction_approval"}};
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([ready]);
  const select = vi.spyOn(api, "selectCover").mockResolvedValue(coverSet("selected"));
  const user = userEvent.setup(); renderStudio();
  const button = await screen.findByRole("button", {name: "选择候选图 1"});
  expect(button).toBeEnabled();
  expect(screen.queryByLabelText("候选图 1的人工接受理由")).not.toBeInTheDocument();
  expect(screen.queryByText("人工接受理由")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "选择候选图 2"})).not.toBeInTheDocument();
  expect(screen.getByRole("button", {name: "选择候选图 3"})).toBeEnabled();
  await waitFor(() => expect(screen.getByRole("region", {name:"主要评估问题"})).toBeVisible());
  expect(screen.queryByRole("button", {name: "按修复建议重新生成候选图 1"})).not.toBeInTheDocument();
  await user.click(button);
  expect(select).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole("dialog", {name:"使用这张封面？"})).toBeVisible());
  await user.click(screen.getByRole("button", {name: "使用此封面"}));
  await waitFor(() => expect(select).toHaveBeenCalledWith(project.id, ready.cover_set_id, "candidate-1",
    ready.revision, ready.active_revision, false));
});

test.each(["project", "workshop"] as const)("%s can reselect a complete previously rejected image without a reason", async scope => {
  const ready = coverSet("ready");
  ready.candidates[0] = {...ready.candidates[0], status:"rejected", quality_report:{status:"blocked", blockers:["thumbnail_clutter"], repair_codes:[], evidence:[]}};
  vi.spyOn(api,"project").mockResolvedValue(project);
  vi.spyOn(workshopApi,"get").mockResolvedValue({
    id:"cover-session", revision:1, status:"ready", input:{title:project.title, author:project.author, genres:[project.genre],
      premise:"Fixture story", language:"English", audience:"Fiction readers", market:"unspecified", constraints:""},
    messages:[], prompt_md:"# Skeleton", questions:[], ready_for_confirmation:true, last_error:null, job_id:null,
    skill_version:"fixture", prepared:null, project_id:null, logs:[],
  });
  vi.spyOn(api,"studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api,"covers").mockResolvedValue([ready]);
  const select=vi.spyOn(api,"selectCover").mockResolvedValue(coverSet("selected"));
  const user=userEvent.setup();renderStudio(scope);
  const button=await screen.findByRole("button",{name:"选择候选图 1"});
  expect(button).toBeEnabled();
  expect(screen.queryByLabelText("候选图 1的人工接受理由")).not.toBeInTheDocument();
  expect(screen.queryByRole("button",{name:"拒绝候选图 1"})).not.toBeInTheDocument();
  await user.click(button);
  expect(select).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button",{name:"使用此封面"}));
  await waitFor(() => expect(select).toHaveBeenCalledExactlyOnceWith(scope === "project" ? project.id : "workshop:cover-session",
    ready.cover_set_id,"candidate-1",ready.revision,ready.active_revision,false));
});

test("generates the configured candidate count from the persisted story handoff", async () => {
  const ready = coverSet("ready");
  const approved = coverDirection("approved");
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValueOnce([]).mockResolvedValue([ready]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([approved]);
  const generate = mockCompletedGeneration(ready);
  const user = userEvent.setup();

  renderStudio();
  await screen.findByRole("heading", { name: "封面工作室" });
  await user.click(screen.getByRole("button", { name: "生成 4 张封面" }));

  await waitFor(() => expect(generate).toHaveBeenCalledWith(project.id, 4, {
    direction_id: approved.direction_id,
    approved_direction_sha256: approved.direction_sha256,
  }, expect.any(String)));
  expect(await screen.findByText("已生成 2/4 张")).toBeInTheDocument();
});

test.each(["project", "workshop"] as const)("%s keeps an approved three-cover batch while planning four new directions", async (scope) => {
  const sessionId = "cover-session";
  const owner = scope === "workshop" ? `workshop:${sessionId}` : project.id;
  const approved = coverDirection("approved");
  approved.plans = approved.plans.slice(0, 3);
  approved.source_binding = {snapshot_id: "snapshot-1", analysis_id: "analysis-complete",
    source_content_sha256: "a".repeat(64), freshness_policy: "require_current_head"};
  const approvedBefore = JSON.stringify(approved);
  const replanned = {...coverDirection(), direction_id: `direction-${"d".repeat(32)}`};
  const ready = coverSet("ready");
  ready.project_id = owner;
  ready.requested_count = 3;
  ready.concepts = ready.concepts.slice(0, 3);
  ready.candidates = ready.candidates.slice(0, 3).map((candidate, index) => ({
    ...ready.candidates[0], candidate_id: candidate.candidate_id, concept_id: candidate.concept_id,
    status: "ready", url: `/mock-cover-${index + 1}.jpg`,
  }));
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(workshopApi, "get").mockResolvedValue({
    id: sessionId, revision: 1, status: "ready", input: {
      title: project.title, author: project.author, genres: [project.genre],
      premise: "A woman claims her home", language: "English", audience: "Fiction readers",
      market: "unspecified", constraints: "",
    },
    messages: [], prompt_md: "# Complete skeleton", questions: [], ready_for_confirmation: true,
    last_error: null, job_id: null, skill_version: "fixture", prepared: null, project_id: null, logs: [],
  });
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValueOnce([]).mockResolvedValue([ready]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([approved]);
  vi.spyOn(api, "coverSource").mockResolvedValue({
    selector: scope === "workshop" ? {source_type: "workshop_current", session_id: sessionId}
      : {source_type: "project_input", project_id: project.id},
    source_content_sha256: "a".repeat(64), source_bundle_sha256: "b".repeat(64),
    origin: {relative_path: "prompt.md"}, documents: [],
    analyses: [{analysis_id: "analysis-complete", snapshot_id: "snapshot-1", coverage_status: "complete"}],
  });
  vi.spyOn(api, "coverAnalysis").mockResolvedValue({analysis_id: "analysis-complete",
    snapshot_id: "snapshot-1", coverage_status: "complete"});
  const generate = mockCompletedGeneration(ready);
  const create = mockCreatedDirection(replanned);
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={[scope === "workshop"
    ? `/workshop/${sessionId}/covers` : `/projects/${project.id}/covers`]}>
    <ToastProvider><ConfirmProvider><Routes>
      <Route path="/projects/:id/covers" element={<CoverStudio />} />
      <Route path="/workshop/:sessionId/covers" element={<CoverStudio />} />
    </Routes></ConfirmProvider></ToastProvider>
  </MemoryRouter>);

  await user.click(await screen.findByRole("button", {name: "生成 3 张封面"}));
  await waitFor(() => expect(generate).toHaveBeenCalledWith(owner, 3, {
    direction_id: approved.direction_id, approved_direction_sha256: approved.direction_sha256,
  }, expect.any(String)));
  expect(await screen.findByText("已生成 3/3 张")).toBeInTheDocument();
  await user.click(screen.getByRole("tab", {name: /故事分析/}));
  await user.selectOptions(await screen.findByLabelText("已保存的骨架分析"), "analysis-complete");
  await screen.findByText("已完成全段分析与全局核查");
  await user.click(screen.getByRole("tab", {name: /美术方向/}));
  await user.click(screen.getByRole("button", {name: "重新规划封面方向"}));
  await waitFor(() => expect(create).toHaveBeenCalledWith(owner, 4, "analysis-complete", "require_current_head"));
  expect(screen.getByRole("button", {name: "生成 4 张封面"})).toBeDisabled();
  expect(screen.getByRole("button", {name: "批准美术方向"})).toBeEnabled();
  expect(screen.getByText("已生成 3/3 张")).toBeInTheDocument();
  expect(generate).toHaveBeenCalledTimes(1);
  expect(JSON.stringify(approved)).toBe(approvedBefore);
});

test("shows generation progress in the preview while candidates are rendering", async () => {
  const existing = coverSet("selected");
  const approved = coverDirection("approved");
  const generatingBase = { ...coverSet("generating"), cover_set_id: "cover-new" };
  const generating = {
    ...generatingBase,
    candidates: generatingBase.candidates.map((candidate, index) => (
      index === 0
        ? candidate
        : {
            ...candidate,
            status: "pending" as const,
            url: null,
            relative_path: "",
            media_id: "",
            sha256: "",
            width: 0,
            height: 0,
            content_type: "",
            error: "",
          }
    )),
  };
  const ready = { ...coverSet("ready"), cover_set_id: "cover-new" };
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "coverDirections").mockResolvedValue([approved]);
  vi.spyOn(api, "covers")
    .mockResolvedValueOnce([existing])
    .mockResolvedValueOnce([generating, existing])
    .mockResolvedValueOnce([ready, existing]);
  vi.spyOn(api, "generateCovers").mockImplementation(async () => {
    vi.mocked(api.coverGenerationTask).mockResolvedValue(generationTask());
    return {job_id: "job-generate", task_id: "generation-a", kind: "cover.generate", status: "running", error: null};
  });
  const user = userEvent.setup();

  renderStudio();
  await screen.findByRole("heading", { name: "封面工作室" });
  await user.click(screen.getByRole("button", { name: "生成 4 张封面" }));

  expect(await screen.findByText("正在生成封面图片")).toBeInTheDocument();
  expect(await screen.findByText("当前处理：候选图 2 / 4")).toBeInTheDocument();
  expect(screen.getByText("已处理 1/4 张 · 可查看 1 张 · 生成失败 0 张")).toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  expect(screen.getAllByText("正在生成图片…")).toHaveLength(1);
});

test("restores a running cover generation after remount instead of enabling a second batch", async () => {
  const existing = coverSet("selected");
  const approved = coverDirection("approved");
  const generating = {...coverSet("generating"), cover_set_id: "cover-running"};
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "coverDirections").mockResolvedValue([approved]);
  const covers = vi.spyOn(api, "covers").mockResolvedValue([existing]);
  const generate = vi.spyOn(api, "generateCovers").mockImplementation(async () => {
    vi.mocked(api.coverGenerationTask).mockResolvedValue(generationTask("running", "cover-running"));
    covers.mockResolvedValue([generating, existing]);
    return {job_id: "job-running", task_id: "generation-a", kind: "cover.generate", status: "running", error: null};
  });
  const user = userEvent.setup();
  const firstVisit = renderStudio();
  await screen.findByRole("heading", {name: "封面工作室"});
  await user.click(screen.getByRole("button", {name: "生成 4 张封面"}));
  await waitFor(() => expect(api.coverGenerationTask).toHaveBeenCalledWith(project.id, "generation-a"));
  expect(screen.getByRole("button", {name: "生成 4 张封面"})).toBeDisabled();

  covers.mockResolvedValue([generating, existing]);
  firstVisit.unmount();
  renderStudio();
  await screen.findByRole("heading", {name: "封面工作室"});
  await screen.findByText("03 / 设计档案");
  expect(screen.getByRole("button", {name: "生成 4 张封面"})).toBeDisabled();
  expect(screen.getByRole("region", {name: "封面生成任务状态"})).toBeVisible();
  expect(generate).toHaveBeenCalledTimes(1);
});

test("keeps a generation failure visible in the cover workspace", async () => {
  const approved = coverDirection("approved");
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([approved]);
  vi.spyOn(api, "generateCovers").mockRejectedValue(
    new Error("Legacy cover story data is incomplete"),
  );
  const user = userEvent.setup();

  renderStudio();
  await screen.findByRole("heading", { name: "封面工作室" });
  await user.click(screen.getByRole("button", { name: "生成 4 张封面" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Legacy cover story data is incomplete",
  );
});

test("offers a new manual batch on the same approved direction after all four images failed", async () => {
  const approved = coverDirection("approved");
  const failed = coverSet("failed");
  failed.candidates = failed.candidates.map(candidate => ({...candidate, status: "failed", url: null,
    relative_path: "", media_id: "", sha256: "", width: 0, height: 0, content_type: "", error: "Provider timeout"}));
  const savedTask = {...generationTask("failed", failed.cover_set_id), direction_id: approved.direction_id,
    error: "四张图片生成失败", progress: {...generationTask().progress!, phase: "saving" as const,
      completed: 4, ready: 0, failed: 4}};
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([failed]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([approved]);
  vi.mocked(api.coverGenerationTask).mockResolvedValue(savedTask);
  const generate = vi.spyOn(api, "generateCovers").mockImplementation(async () => {
    vi.mocked(api.coverGenerationTask).mockResolvedValue({...generationTask(), task_id: "generation-new", cover_set_id: null});
    return {job_id: "job-new", task_id: "generation-new", kind: "cover.generate", status: "running", error: null};
  });
  const user = userEvent.setup();
  renderStudio();
  const retry = await screen.findByRole("button", {name: "重新生成 4 张封面"});
  await waitFor(() => expect(retry).toBeEnabled());
  expect(generate).not.toHaveBeenCalled();
  expect(screen.getByText("沿用已批准方向，新建一批候选；已有结果保留。")).toBeVisible();
  await user.click(retry);
  expect(generate).toHaveBeenCalledWith(project.id, 4, {
    direction_id: approved.direction_id, approved_direction_sha256: approved.direction_sha256,
  }, expect.any(String));
  await waitFor(() => expect(screen.getByRole("button", {name: "生成 4 张封面"})).toBeDisabled());
  expect(generate).toHaveBeenCalledTimes(1);
  expect(screen.getByText("已生成 0/4 张")).toBeInTheDocument();
});

test("requires confirmation before rejecting a ready candidate", async () => {
  const ready = coverSet("ready");
  const rejected = {
    ...ready,
    revision: ready.revision + 1,
    candidates: ready.candidates.map((candidate, index) => (
      index === 0 ? { ...candidate, status: "rejected" as const } : candidate
    )),
  };
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([ready]);
  const reject = vi.spyOn(api, "rejectCover").mockResolvedValue(rejected);
  const user = userEvent.setup();

  renderStudio();
  await screen.findByRole("heading", { name: "封面工作室" });
  await user.click(screen.getByRole("button", { name: "拒绝候选图 1" }));

  expect(reject).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "拒绝候选图" }));
  await waitFor(() => expect(reject).toHaveBeenCalledWith(
    project.id, ready.cover_set_id, "candidate-1", ready.revision,
  ));
  expect(screen.getByText("已拒绝")).toBeInTheDocument();
});

test("reloads cover revisions after a selection conflict", async () => {
  const ready = coverSet("ready");
  const refreshed = { ...ready, revision: ready.revision + 2, active_revision: 3 };
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  const covers = vi.spyOn(api, "covers")
    .mockResolvedValueOnce([ready])
    .mockResolvedValue([refreshed]);
  vi.spyOn(api, "selectCover").mockRejectedValue(new Error("Cover set revision changed"));
  const user = userEvent.setup();

  renderStudio();
  await screen.findByRole("heading", { name: "封面工作室" });
  await user.click(screen.getByRole("button", { name: "选择候选图 1" }));
  await user.click(screen.getByRole("button", { name: "使用此封面" }));

  await waitFor(() => expect(covers).toHaveBeenCalledTimes(2));
  expect(await screen.findByText("Cover set revision changed")).toBeInTheDocument();
});


test("the gallery is the default and approved design records are available on their own tab", async () => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([coverSet()]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([coverDirection("approved")]);
  const generate = vi.spyOn(api, "generateCovers");
  const approve = vi.spyOn(api, "approveCoverDirection");
  const user = userEvent.setup();
  renderStudio();
  const gallery = await screen.findByRole("region", {name: "封面候选画廊"});
  expect(screen.queryByRole("region", {name: "故事事实与美术方向"})).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab", {name: /美术方向/}));
  expect(gallery).not.toBeVisible();
  expect(screen.getByRole("region", {name: "故事事实与美术方向"})).toBeVisible();
  const summary = screen.getByText("查看已批准方案与故事依据");
  const disclosure = summary.closest("details");
  expect(disclosure).not.toHaveAttribute("open");
  // Native summary owns keyboard activation in browsers; jsdom models its click default.
  expect(summary.tagName).toBe("SUMMARY");
  await user.click(summary);
  expect(disclosure).toHaveAttribute("open");
  expect(screen.getByText("年龄 34 · caregiver returning to paid work")).toBeVisible();
  expect(screen.getByRole("button", {name: "重新规划封面方向"})).toBeEnabled();
  expect(generate).not.toHaveBeenCalled();
  expect(approve).not.toHaveBeenCalled();
});

test.each(["project", "workshop"] as const)("%s restores generation across navigation and selects the exact completed batch on return", async scope => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(workshopApi, "get").mockResolvedValue({
    id: "cover-session", revision: 1, status: "ready", input: {title: project.title, author: project.author, genres: [project.genre],
      premise: "Fixture story", language: "English", audience: "Fiction readers", market: "unspecified", constraints: ""},
    messages: [], prompt_md: "# Skeleton", questions: [], ready_for_confirmation: true, last_error: null, job_id: null,
    skill_version: "fixture", prepared: null, project_id: null, logs: [],
  });
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  const old = coverSet("selected");
  const bound = {...coverSet("generating"), cover_set_id: "cover-new"};
  const covers = vi.spyOn(api, "covers").mockResolvedValue([old, bound]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([coverDirection("approved")]);
  vi.mocked(api.coverGenerationTask).mockResolvedValue(generationTask());
  const generate = vi.spyOn(api, "generateCovers");
  const user = userEvent.setup();
  renderStudio(scope);
  const status = await screen.findByRole("region", {name: "封面生成任务状态"});
  await user.click(screen.getByRole("tab", {name: /故事分析/}));
  const source = screen.getByRole("region", {name: "骨架来源与故事理解"});
  expect(status).toBeVisible();
  expect(status.compareDocumentPosition(source) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  await user.click(screen.getByRole("tab", {name: /候选封面/}));
  await waitFor(() => expect(screen.getByRole("combobox", {name: "版本"})).toHaveValue("cover-new"));
  expect(screen.getByRole("button", {name: "生成 4 张封面"})).toBeDisabled();
  await user.click(screen.getByRole("link", {name: "离开工作室"}));
  await user.click(screen.getByRole("link", {name: "返回工作室"}));
  await screen.findByText("正在生成封面图片");
  expect(screen.getByRole("button", {name: "生成 4 张封面"})).toBeDisabled();
  await user.click(screen.getByRole("link", {name: "离开工作室"}));
  covers.mockResolvedValue([old, {...bound, status: "ready"}]);
  vi.mocked(api.coverGenerationTask).mockResolvedValue(generationTask("completed"));
  await user.click(screen.getByRole("link", {name: "返回工作室"}));
  await screen.findByText("本批封面已处理完成");
  await waitFor(() => expect(screen.getByRole("button", {name: "生成 4 张封面"})).toBeEnabled());
  expect(screen.getByRole("combobox", {name: "版本"})).toHaveValue("cover-new");
  expect(generate).not.toHaveBeenCalled();
  expect(api.coverGenerationTask).toHaveBeenLastCalledWith(scope === "project" ? project.id : "workshop:cover-session", undefined);
});

test("does not let a delayed initial image list overwrite the recovered completed batch", async () => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "coverDirections").mockResolvedValue([coverDirection("approved")]);
  const old = coverSet("selected");
  const fresh = {...coverSet("ready"), cover_set_id: "cover-new"};
  let finish!: (value: CoverSet[]) => void;
  vi.spyOn(api, "covers").mockReturnValueOnce(new Promise(resolve => {finish = resolve;})).mockResolvedValue([fresh, old]);
  vi.mocked(api.coverGenerationTask).mockResolvedValue(generationTask("completed"));
  renderStudio();
  await waitFor(() => expect(api.covers).toHaveBeenCalledTimes(2));
  await act(async () => {finish([old]);});
  await screen.findByText("本批封面已处理完成");
  expect(screen.getByRole("combobox", {name: "版本"})).toHaveValue("cover-new");
});

test("locks generation and candidate actions while initial task discovery is unknown", async () => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "coverDirections").mockResolvedValue([coverDirection("approved")]);
  vi.spyOn(api, "covers").mockResolvedValue([coverSet("ready")]);
  vi.mocked(api.coverGenerationTask).mockRejectedValue(new Error("offline"));
  const generate = vi.spyOn(api, "generateCovers");
  renderStudio();
  await screen.findByText("封面任务状态暂未确认");
  expect(screen.getByRole("button", {name: "生成 4 张封面"})).toBeDisabled();
  expect(screen.getByRole("button", {name: "选择候选图 1"})).toBeDisabled();
  expect(screen.getByRole("button", {name: "重试候选图 2"})).toBeDisabled();
  expect(generate).not.toHaveBeenCalled();
});

function repairableSet(): CoverSet {
  const set = coverSet("ready");
  set.concepts[0].scene_plan = {_source_binding: {direction_id: coverDirection().direction_id}};
  set.candidates[0].quality_report = {status: "blocked", blockers: ["core_conflict_missing"], repair_codes: [],
    evidence: ["The action contradicts the source event."], repair_decision: {kind: "design"}};
  return set;
}

function failedRefinement(status: "failed" | "interrupted" = "failed") {
  const parent = repairableSet(), child = structuredClone(parent);
  parent.candidates[0].quality_report!.assets = {artwork_url: "/reference-original.png"};
  child.cover_set_id = "failed-refinement";
  child.candidates[0] = {...child.candidates[0], candidate_id: "failed-image", status: "failed", url: null, error: "Image edit interrupted"};
  child.concepts[0].scene_plan!._image_refinement = {reference_image_sha256: "original-sha"};
  const task = {...repairTaskFixture(status), kind: "refinement" as const,
    parent_cover_set_id: parent.cover_set_id, cover_set_id: child.cover_set_id,
    result_candidate_id: child.candidates[0].candidate_id, refinement_prompt: "保留人物，让光线更柔和。"};
  vi.spyOn(api, "project").mockResolvedValue(project); vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([child, parent]); vi.mocked(api.coverRepairTask).mockResolvedValue(task);
  return {parent, child, task};
}

test("failed refinement without an output image offers an explicit retry on its card and task panel", async () => {
  failedRefinement();
  const generate = vi.spyOn(api, "retryCover"); const approve = vi.spyOn(api, "approveCoverRepair");
  renderStudio();
  const card = await screen.findByRole("article", {name: "候选图 1"});
  await waitFor(() => expect(within(card).getByRole("button", {name: "重试精修候选图 1"})).toBeEnabled());
  expect(within(screen.getByRole("region", {name: "单张封面精修任务"})).getByRole("button", {name: "重试精修"})).toBeEnabled();
  expect(within(card).queryByRole("button", {name: "重试候选图 1"})).not.toBeInTheDocument();
  expect(generate).not.toHaveBeenCalled(); expect(approve).not.toHaveBeenCalled();
});

test.each(["failed", "interrupted"] as const)("%s refinement retries with its original image and saved request, awaiting fresh approval", async status => {
  const {task}=failedRefinement(status);
  const proposed={...task,task_id:"retry-proposal",status:"awaiting_approval" as const,cover_set_id:null,result_candidate_id:null,
    proposal_sha256:"fresh-approval",retry_of_task_id:task.task_id,proposal_reused:true};
  vi.spyOn(api,"coverRepairTaskForCandidate").mockResolvedValue(task);
  const retry=vi.spyOn(api,"retryCoverRefinement").mockImplementation(async()=>{vi.mocked(api.coverRepairTask).mockResolvedValue(proposed);return proposed;});
  const approve=vi.spyOn(api,"approveCoverRepair").mockImplementation(async()=>{
    const active={...proposed,status:"generating" as const};vi.mocked(api.coverRepairTask).mockResolvedValue(active);return active;
  });
  const generic=vi.spyOn(api,"retryCover");const start=vi.spyOn(api,"startCoverRepair");const user=userEvent.setup();const view=renderStudio();
  const button=await screen.findByRole("button",{name:"重试精修候选图 1"});await waitFor(()=>expect(button).toBeEnabled());
  await user.click(button);
  const panel=await screen.findByRole("region",{name:"单张封面精修任务"});
  await waitFor(()=>expect(panel).toHaveTextContent("已恢复原方案"));
  await waitFor(()=>expect(panel).toHaveFocus());
  expect(retry).toHaveBeenCalledExactlyOnceWith(project.id,task.task_id);expect(approve).not.toHaveBeenCalled();expect(start).not.toHaveBeenCalled();expect(generic).not.toHaveBeenCalled();
  expect(within(panel).getByRole("img",{name:"候选图 1 的精修前图片"})).toHaveAttribute("src",expect.stringContaining("/reference-original.png"));
  expect(panel).toHaveTextContent(task.refinement_prompt);
  expect(within(panel).getByRole("button",{name:"批准并精修这 1 张"})).toBeDisabled();
  view.unmount();renderStudio();
  await screen.findByText("已恢复原方案与原始参考图片，本次尚未出图。批准后将新增 1 次图片编辑调用；此前任务和费用记录保留。");
  expect(retry).toHaveBeenCalledTimes(1);expect(approve).not.toHaveBeenCalled();
  await user.click(screen.getByRole("checkbox",{name:/新增 1 次图片编辑调用/}));
  const approval=screen.getByRole("button",{name:"批准并精修这 1 张"});await waitFor(()=>expect(approval).toBeEnabled());await user.click(approval);
  await waitFor(()=>expect(approve).toHaveBeenCalledExactlyOnceWith(project.id,proposed.task_id,"fresh-approval",1));
});

test("a stale proposal restores an editable request on the original parent and never auto replans",async()=>{
  const {parent,task}=failedRefinement();
  const detail={code:"cover_refinement_replan_required",message:"原方案已过期，请重新规划。",replan_context:{parent_cover_set_id:parent.cover_set_id,parent_candidate_id:"candidate-1",expected_revision:parent.revision,refinement_prompt:task.refinement_prompt}};
  vi.spyOn(api,"retryCoverRefinement").mockRejectedValue(new ApiError(detail.message,409,detail));
  const planned={...task,status:"planning" as const,cover_set_id:null,result_candidate_id:null};
  const start=vi.spyOn(api,"startCoverRepair").mockImplementation(async()=>{vi.mocked(api.coverRepairTask).mockResolvedValue(planned);return planned;});
  const approve=vi.spyOn(api,"approveCoverRepair");const user=userEvent.setup();renderStudio();
  const retry=await screen.findByRole("button",{name:"重试精修候选图 1"});await waitFor(()=>expect(retry).toBeEnabled());await user.click(retry);
  const dialog=await screen.findByRole("dialog");expect(dialog).toHaveTextContent(detail.message);
  const prompt=within(dialog).getByRole("textbox",{name:"优化要求"});expect(prompt).toHaveValue(task.refinement_prompt);
  expect(within(dialog).getByRole("img",{name:"候选图 1 的精修参考图片"})).toHaveAttribute("src",expect.stringContaining("/reference-original.png"));
  expect(start).not.toHaveBeenCalled();expect(approve).not.toHaveBeenCalled();
  await user.clear(prompt);await user.type(prompt,"保留人物，减少背景杂物。");
  const create=within(dialog).getByRole("button",{name:"制定精修方案"});await waitFor(()=>expect(create).toBeEnabled());await user.click(create);
  await waitFor(()=>expect(start).toHaveBeenCalledExactlyOnceWith(project.id,parent.cover_set_id,"candidate-1",parent.revision,"保留人物，减少背景杂物。"));
});

test("failed refinement can revise the saved request directly without retrying or using its failed child",async()=>{
  const {task}=failedRefinement();const retry=vi.spyOn(api,"retryCoverRefinement");const start=vi.spyOn(api,"startCoverRepair");
  const user=userEvent.setup();renderStudio();const button=await screen.findByRole("button",{name:"修改候选图 1 的精修要求并重新规划"});
  await waitFor(()=>expect(button).toBeEnabled());await user.click(button);
  expect(screen.getByRole("textbox",{name:"优化要求"})).toHaveValue(task.refinement_prompt);
  expect(screen.getByRole("img",{name:"候选图 1 的精修参考图片"})).toHaveAttribute("src",expect.stringContaining("/reference-original.png"));
  expect(retry).not.toHaveBeenCalled();expect(start).not.toHaveBeenCalled();
});

test("missing original leaves both retry entry points disabled with an explanation",async()=>{
  const {child}=failedRefinement();vi.mocked(api.covers).mockResolvedValue([child]);const retry=vi.spyOn(api,"retryCoverRefinement");renderStudio();
  const panel=await screen.findByRole("region",{name:"单张封面精修任务"});
  expect(await screen.findByRole("button",{name:"重试精修候选图 1"})).toBeDisabled();
  expect(within(panel).getByRole("button",{name:"重试精修"})).toBeDisabled();
  expect(panel).toHaveTextContent("原始参考图片暂不可用");expect(retry).not.toHaveBeenCalled();
});

test.each(["ready", "selected", "rejected"] as const)("a %s candidate supports explicit refinement without selecting or regenerating the full batch", async status => {
  const parent=repairableSet();parent.candidates[0].status=status;
  parent.candidates[0].quality_report={status:"recommended_for_human_review",blockers:[],repair_codes:[],evidence:[],assets:{artwork_url:"/original-art.png"}};
  const planned={...repairTaskFixture("planning"),kind:"refinement" as const,refinement_prompt:"让眼神更坚定，保留构图。",parent_cover_set_id:parent.cover_set_id};
  vi.spyOn(api,"project").mockResolvedValue(project);vi.spyOn(api,"studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api,"covers").mockResolvedValue([parent]);
  const start=vi.spyOn(api,"startCoverRepair").mockImplementation(async()=>{vi.mocked(api.coverRepairTask).mockResolvedValue(planned);return planned;});
  const select=vi.spyOn(api,"selectCover");const generate=vi.spyOn(api,"generateCovers");const approve=vi.spyOn(api,"approveCoverRepair");
  const user=userEvent.setup();renderStudio();
  const refine=await screen.findByRole("button",{name:"精修候选图 1"});await waitFor(()=>expect(refine).toBeEnabled());
  await user.click(refine);expect(start).not.toHaveBeenCalled();
  expect(screen.getByRole("img",{name:"候选图 1 的精修参考图片"})).toHaveAttribute("src",expect.stringContaining("/original-art.png"));
  await user.type(screen.getByRole("textbox",{name:"优化要求"}),`  ${planned.refinement_prompt}  `);
  await user.click(screen.getByRole("button",{name:"制定精修方案"}));
  await waitFor(()=>expect(start).toHaveBeenCalledExactlyOnceWith(project.id,parent.cover_set_id,"candidate-1",5,planned.refinement_prompt));
  expect(await screen.findByRole("region",{name:"单张封面精修任务"})).toHaveTextContent(planned.refinement_prompt);
  expect(refine).toBeDisabled();expect(select).not.toHaveBeenCalled();expect(generate).not.toHaveBeenCalled();expect(approve).not.toHaveBeenCalled();
});

test.each(["binding", "artwork", "import", "amendment"] as const)("refinement explains its unavailable %s prerequisite instead of starting a broken task", async missing => {
  const parent=repairableSet();
  if(missing==="binding") parent.concepts[0].scene_plan={};
  if(missing==="artwork") parent.candidates[0].url=null;
  if(missing==="import") parent.concepts[0].scene_plan!._cover_import={import_id:"existing"};
  if(missing==="amendment") parent.candidates[0].quality_report!.typography_amendment={amendment_id:"existing"};
  vi.spyOn(api,"project").mockResolvedValue(project);vi.spyOn(api,"studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api,"covers").mockResolvedValue([parent]);const start=vi.spyOn(api,"startCoverRepair");renderStudio();
  const card=await screen.findByRole("article",{name:"候选图 1"});
  expect(within(card).getByRole("button",{name:"精修候选图 1"})).toBeDisabled();
  expect(within(card).getByRole("button",{name:"精修候选图 1"})).toHaveAccessibleDescription(/来源绑定|编辑的图片|独立排版修订或导入/);
  expect(start).not.toHaveBeenCalled();
});

test("native cover refinement uses the saved cover when no separate artwork exists", async () => {
  const parent=repairableSet();vi.spyOn(api,"project").mockResolvedValue(project);vi.spyOn(api,"studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api,"covers").mockResolvedValue([parent]);const start=vi.spyOn(api,"startCoverRepair");const user=userEvent.setup();renderStudio();
  const button=await screen.findByRole("button",{name:"精修候选图 1"});await waitFor(()=>expect(button).toBeEnabled());await user.click(button);
  expect(screen.getByRole("img",{name:"候选图 1 的精修参考图片"})).toHaveAttribute("src",expect.stringContaining("media-1"));
  expect(screen.getByText("使用这张封面作为图片编辑参考。")).toBeInTheDocument();expect(start).not.toHaveBeenCalled();
});

test("single repair waits for an explicit planning action and binds exactly the chosen revision", async () => {
  const parent = repairableSet();
  const planned = {...repairTaskFixture("planning"), parent_cover_set_id: parent.cover_set_id};
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([parent]);
  const start = vi.spyOn(api, "startCoverRepair").mockImplementation(async () => {
    vi.mocked(api.coverRepairTask).mockResolvedValue(planned);
    return planned;
  });
  const generate = vi.spyOn(api, "generateCovers");
  const approve = vi.spyOn(api, "approveCoverRepair");
  const user = userEvent.setup();
  renderStudio();
  const button = await screen.findByRole("button", {name: "修复候选图 1"});
  await waitFor(() => expect(button).toBeEnabled());
  await user.click(button);
  expect(start).not.toHaveBeenCalled();
  expect(approve).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", {name: "制定这张图的修复方案"}));
  await waitFor(() => expect(start).toHaveBeenCalledExactlyOnceWith(project.id, parent.cover_set_id, "candidate-1", 5));
  expect(await screen.findByRole("region", {name: "单张封面修复任务"})).toHaveTextContent("候选图 1");
  expect(screen.getByRole("button", {name: "修复候选图 1"})).toBeDisabled();
  expect(generate).not.toHaveBeenCalled();
  expect(approve).not.toHaveBeenCalled();
});

test("restored single repair approval cannot approve or generate the full batch", async () => {
  const parent = repairableSet();
  const task = {...repairTaskFixture(), parent_cover_set_id: parent.cover_set_id};
  const repairDirection = {...coverDirection(), direction_id: task.direction_id!,
    source_binding: {single_candidate_repair: {parent_cover_set_id: parent.cover_set_id}}};
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([parent]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([repairDirection, coverDirection()]);
  vi.mocked(api.coverRepairTask).mockResolvedValue(task);
  const start = vi.spyOn(api, "startCoverRepair");
  const generate = vi.spyOn(api, "generateCovers");
  const batchApprove = vi.spyOn(api, "approveCoverDirection");
  const approve = vi.spyOn(api, "approveCoverRepair").mockImplementation(async () => {
    const active = {...task, status: "generating" as const};
    vi.mocked(api.coverRepairTask).mockResolvedValue(active);
    return active;
  });
  const user = userEvent.setup();
  renderStudio();
  const button = await screen.findByRole("button", {name: "批准并生成这 1 张"});
  expect(button).toBeDisabled();
  expect(screen.getByRole("img", {name: "候选图 1 的修复前图片"})).toHaveAttribute("src", expect.stringContaining("media-1"));
  expect(screen.getByRole("button", {name: "生成 4 张封面"})).toBeDisabled();
  expect(start).not.toHaveBeenCalled();
  expect(approve).not.toHaveBeenCalled();
  await user.click(screen.getByRole("checkbox", {name: /我已审阅方案/}));
  await waitFor(() => expect(button).toBeEnabled());
  await user.click(button);
  await waitFor(() => expect(approve).toHaveBeenCalledExactlyOnceWith(project.id, task.task_id, task.proposal_sha256, 1));
  expect(batchApprove).not.toHaveBeenCalled();
  expect(generate).not.toHaveBeenCalled();
});

test("a delayed earlier generation result cannot replace a newer repaired batch", async () => {
  const parent = repairableSet();
  const repaired = {...parent, cover_set_id: "repaired-set"};
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([repaired, parent]);
  let finish!: (task: CoverGenerationTask) => void;
  vi.mocked(api.coverGenerationTask).mockReturnValue(new Promise(resolve => {finish = resolve;}));
  vi.mocked(api.coverRepairTask).mockResolvedValue({...repairTaskFixture("completed"),
    cover_set_id: repaired.cover_set_id, parent_cover_set_id: parent.cover_set_id});
  renderStudio();
  await waitFor(() => expect(screen.getByRole("combobox", {name: "版本"})).toHaveValue("repaired-set"));
  await act(async () => {finish(generationTask("completed", parent.cover_set_id));});
  await screen.findByText("本批封面已处理完成");
  expect(screen.getByRole("combobox", {name: "版本"})).toHaveValue("repaired-set");
});

test.each(["project", "workshop"] as const)("%s remembers a requested tab and keeps the skeleton mounted across tab switches", async scope => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(workshopApi, "get").mockResolvedValue({
    id: "cover-session", revision: 1, status: "ready", input: {title: project.title, author: project.author, genres: [project.genre], premise: "Story", language: "English", audience: "Readers", market: "unspecified", constraints: ""},
    messages: [], prompt_md: "# Skeleton", questions: [], ready_for_confirmation: true, last_error: null, job_id: null, skill_version: "fixture", prepared: null, project_id: null, logs: [],
  });
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([coverSet()]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([coverDirection("approved")]);
  const source = vi.spyOn(api, "coverSource").mockResolvedValue({
    selector: {source_type: "project_input", project_id: project.id}, source_content_sha256: "a".repeat(64), source_bundle_sha256: "b".repeat(64),
    origin: {relative_path: "prompt.md"}, documents: [], analyses: [{analysis_id: "analysis-complete", snapshot_id: "snapshot-1", coverage_status: "complete"}],
  });
  const analysis = vi.spyOn(api, "coverAnalysis").mockResolvedValue({analysis_id: "analysis-complete", snapshot_id: "snapshot-1", coverage_status: "complete"});
  const generate = vi.spyOn(api, "generateCovers");
  const create = vi.spyOn(api, "startCoverDirectionTask");
  const user = userEvent.setup();
  const visit = renderStudio(scope, "?tab=analysis&context=kept");
  expect(await screen.findByRole("tab", {name: /故事分析/})).toHaveAttribute("aria-selected", "true");
  await user.selectOptions(await screen.findByLabelText("已保存的骨架分析"), "analysis-complete");
  await screen.findByText("已完成全段分析与全局核查");
  const analysisReadsBeforeSwitch = analysis.mock.calls.length;
  await user.click(screen.getByRole("tab", {name: "费用"}));
  expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
  expect(screen.getByLabelText("当前地址")).toHaveTextContent("tab=costs&context=kept");
  expect(screen.queryByRole("region", {name: "骨架来源与故事理解"})).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", {name: "返回上一子页"}));
  expect(screen.getByRole("tab", {name: /故事分析/})).toHaveAttribute("aria-selected", "true");
  expect(screen.getByLabelText("已保存的骨架分析")).toHaveValue("analysis-complete");
  expect(source).toHaveBeenCalledTimes(1);
  expect(analysis).toHaveBeenCalledTimes(analysisReadsBeforeSwitch);
  const savedQuery = screen.getByLabelText("当前地址").textContent!.split("?")[1];
  visit.unmount();
  renderStudio(scope, "?" + savedQuery);
  expect(await screen.findByRole("tab", {name: /故事分析/})).toHaveAttribute("aria-selected", "true");
  expect(generate).not.toHaveBeenCalled();
  expect(create).not.toHaveBeenCalled();
});

test("tabs support arrow keys and move focus out of a panel that becomes hidden", async () => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  const user = userEvent.setup();
  renderStudio("project", "?tab=directions");
  await user.click(await screen.findByRole("button", {name: "前往故事分析"}));
  const analysisTab = screen.getByRole("tab", {name: /故事分析/});
  expect(analysisTab).toHaveFocus();
  expect(analysisTab).toHaveAttribute("aria-selected", "true");
  await user.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", {name: "费用"})).toHaveFocus();
  expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
  await user.keyboard("{Home}");
  expect(screen.getByRole("tab", {name: /候选封面/})).toHaveFocus();
  await user.keyboard("{End}");
  expect(screen.getByRole("tab", {name: "费用"})).toHaveAttribute("aria-selected", "true");
});

test("hides the completed direction notice after exact approval and after reopening", async () => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  const directions = vi.spyOn(api, "coverDirections").mockResolvedValue([coverDirection()]);
  vi.mocked(api.coverDirectionTask).mockResolvedValue(directionTask("completed"));
  vi.spyOn(api, "approveCoverDirection").mockResolvedValue(coverDirection("approved"));
  const user = userEvent.setup();
  const visit = renderStudio();
  expect(await screen.findByText("美术方向已生成")).toBeVisible();
  await user.click(await screen.findByRole("button", {name: "批准美术方向"}));
  await waitFor(() => expect(screen.queryByText("美术方向已生成")).not.toBeInTheDocument());
  await waitFor(() => expect(screen.getByRole("tab", {name: /候选封面/})).toHaveAttribute("aria-selected", "true"));
  expect(screen.getByRole("tab", {name: /候选封面/})).toHaveFocus();
  directions.mockResolvedValue([coverDirection("approved")]);
  visit.unmount();
  renderStudio();
  await screen.findByRole("tab", {name: /美术方向\s*已批准/});
  await waitFor(() => expect(screen.queryByText("美术方向已生成")).not.toBeInTheDocument());
});

test("an unrelated approved direction does not dismiss a completed task notice", async () => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([{...coverDirection("approved"), direction_id: "another-direction"}, coverDirection()]);
  vi.mocked(api.coverDirectionTask).mockResolvedValue(directionTask("completed"));
  renderStudio();
  expect(await screen.findByText("美术方向已生成")).toBeVisible();
});

test("approval finishing in the background does not override the user's new tab", async () => {
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([coverDirection()]);
  let finish!: (direction: CoverDirection) => void;
  vi.spyOn(api, "approveCoverDirection").mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const user = userEvent.setup();
  renderStudio();
  await user.click(await screen.findByRole("button", {name: "批准美术方向"}));
  await user.click(screen.getByRole("tab", {name: "费用"}));
  await act(async () => finish(coverDirection("approved")));
  expect(screen.getByRole("tab", {name: "费用"})).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", {name: "费用"})).toHaveFocus();
  expect(screen.getByRole("button", {name: "生成 4 张封面"})).toBeEnabled();
});
