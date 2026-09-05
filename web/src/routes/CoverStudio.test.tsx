import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ToastProvider } from "../components/Toaster";
import { ConfirmProvider } from "../components/Confirm";
import CoverStudio from "./CoverStudio";
import { api, type CoverDirection, type CoverSet } from "../api/client";


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

function renderStudio() {
  if (!vi.isMockFunction(api.coverDirections)) {
    vi.spyOn(api, "coverDirections").mockResolvedValue([]);
  }
  return render(
    <MemoryRouter initialEntries={[`/projects/${project.id}/covers`]}>
      <ToastProvider><ConfirmProvider>
        <Routes><Route path="/projects/:id/covers" element={<CoverStudio />} /></Routes>
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

afterEach(() => {
  cleanup();
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

test("creates art direction from the persisted story facts before generation", async () => {
  const pending = coverDirection();
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([]);
  const create = vi.spyOn(api, "createCoverDirection").mockResolvedValue(pending);
  const user = userEvent.setup();

  renderStudio();

  await screen.findByRole("heading", { name: "封面工作室" });
  expect(screen.getByRole("button", { name: "生成 4 张封面" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "创建美术方向" }));

  await waitFor(() => expect(create).toHaveBeenCalledWith(project.id, 4));
  expect(await screen.findByText("美术方向审核")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批准美术方向" })).toBeEnabled();
});

test("lets operations replace an approved legacy direction with a new portfolio", async () => {
  const approved = coverDirection("approved");
  const replanned = coverDirection();
  replanned.direction_id = `direction-${"d".repeat(32)}`;
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValue([]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([approved]);
  const create = vi.spyOn(api, "createCoverDirection").mockResolvedValue(replanned);
  const user = userEvent.setup();

  renderStudio();

  await user.click(await screen.findByRole("button", { name: "重新规划封面方向" }));
  await waitFor(() => expect(create).toHaveBeenCalledWith(project.id, 4));
  expect(screen.getByRole("button", { name: "批准美术方向" })).toBeEnabled();
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

test("generates the configured candidate count from the persisted story handoff", async () => {
  const ready = coverSet("ready");
  const approved = coverDirection("approved");
  vi.spyOn(api, "project").mockResolvedValue(project);
  vi.spyOn(api, "studioCover").mockResolvedValue(coverStatus);
  vi.spyOn(api, "covers").mockResolvedValueOnce([]).mockResolvedValue([ready]);
  vi.spyOn(api, "coverDirections").mockResolvedValue([approved]);
  const generate = vi.spyOn(api, "generateCovers").mockResolvedValue({
    job_id: "job-generate", kind: "cover.generate", status: "done", error: null, meta: {},
  });
  const user = userEvent.setup();

  renderStudio();
  await screen.findByRole("heading", { name: "封面工作室" });
  await user.click(screen.getByRole("button", { name: "生成 4 张封面" }));

  await waitFor(() => expect(generate).toHaveBeenCalledWith(project.id, 4, {
    direction_id: approved.direction_id,
    approved_direction_sha256: approved.direction_sha256,
  }));
  expect(await screen.findByText("已生成 2/4 张")).toBeInTheDocument();
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
  vi.spyOn(api, "generateCovers").mockResolvedValue({
    job_id: "job-generate", kind: "cover.generate", status: "running", error: null, meta: {},
  });
  vi.spyOn(api, "getJob")
    .mockResolvedValueOnce({
      job_id: "job-generate", kind: "cover.generate", status: "running", error: null, meta: {},
    })
    .mockResolvedValueOnce({
      job_id: "job-generate", kind: "cover.generate", status: "done", error: null, meta: {},
    });
  const user = userEvent.setup();

  renderStudio();
  await screen.findByRole("heading", { name: "封面工作室" });
  await user.click(screen.getByRole("button", { name: "生成 4 张封面" }));

  expect(screen.getByText("正在准备生成封面")).toBeInTheDocument();
  expect(await screen.findByText("正在生成第 2/4 张封面")).toBeInTheDocument();
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
  expect(screen.getAllByText("正在生成图片…")).toHaveLength(3);
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
